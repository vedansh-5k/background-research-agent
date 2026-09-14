import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from agents import llm_client
from agents.query_planner import generate_queries
from config.settings import settings
from extraction.web_extractor import extract_pages
from models.schemas import ResearchAnalysis, SearchResult
from search.base import SearchProvider, extract_domain
from utils.logger import RunLogger

SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "research_system_prompt.txt"
SYSTEM_PROMPT = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")

CHARS_PER_TOKEN = 4          # rough for English prose; URLs/IDs are worse, hence the margins below
SCHEMA_TOKEN_ALLOWANCE = 900  # the JSON schema travels with every request
WRAPPER_TOKEN_ALLOWANCE = 400  # context lines, per-source labels, closing instruction
SAFETY_MARGIN_TOKENS = 400     # URLs tokenize far worse than 4 chars/token


def _evidence_char_budget() -> int:
    """How many characters of source material we can actually afford to send.

    Computed rather than hardcoded because the provider counts input AND the
    reserved max_completion_tokens against one ceiling — so every edit to the
    system prompt or output limit silently changes what's affordable. Deriving
    it here means those edits can't quietly push us over the limit again.
    """
    available = (
        settings.token_budget_for_active_provider()
        - settings.max_completion_tokens
        - len(SYSTEM_PROMPT) // CHARS_PER_TOKEN
        - SCHEMA_TOKEN_ALLOWANCE
        - WRAPPER_TOKEN_ALLOWANCE
        - SAFETY_MARGIN_TOKENS
    )
    return max(1200, min(settings.max_evidence_section_chars, available * CHARS_PER_TOKEN))

# Rough source-quality tiers (spec section 11). Unknown domains default to a
# middle score since they may well be an official company/personal page.
TIER_KEYWORDS = [
    (100, [".gov", "court", "judiciary"]),
    (90, [".edu", "university", "ac.in", ".ac."]),
    (70, ["linkedin.com"]),
    (65, ["github.com"]),
    (60, ["reuters.com", "bbc.", "nytimes.com", "forbes.com", "techcrunch.com",
          "timesofindia", "hindustantimes", "thehindu", "indianexpress"]),
    (40, ["twitter.com", "x.com", "instagram.com", "facebook.com"]),
    (20, ["medium.com", "blogspot.", "wordpress."]),
]


def _merge_snippets(existing: str, incoming: str, cap: int) -> str:
    """Combine two extracts of the same page, skipping redundant text."""
    if not incoming:
        return existing[:cap]
    if not existing:
        return incoming[:cap]
    if incoming in existing:
        return existing[:cap]
    if existing in incoming:
        return incoming[:cap]
    return (existing + "\n" + incoming)[:cap]


def _score_domain(domain: str) -> int:
    domain = (domain or "").lower()
    for score, keywords in TIER_KEYWORDS:
        if any(kw in domain for kw in keywords):
            return score
    return 50


def _get_search_provider(logger: RunLogger) -> SearchProvider:
    """Builds the configured provider as primary, and — if a key exists for the
    other one too — wires it in as an automatic fallback (spec section 9: search
    provider must be swappable and not a hard dependency on one vendor)."""
    from search.fallback_provider import FallbackSearchProvider
    from search.serper_provider import SerperSearchProvider
    from search.tavily_provider import TavilySearchProvider

    tavily = TavilySearchProvider(settings.tavily_api_key) if settings.tavily_api_key else None
    serper = SerperSearchProvider(settings.serper_api_key) if settings.serper_api_key else None

    if settings.search_provider == "serper":
        primary, primary_name = serper, "Serper"
        secondary, secondary_name = tavily, "Tavily"
    else:
        primary, primary_name = tavily, "Tavily"
        secondary, secondary_name = serper, "Serper"

    if primary is None:
        raise RuntimeError(f"No API key configured for the primary search provider ({primary_name}).")

    if secondary is not None:
        logger.log("SETUP", f"Search provider: {primary_name} (primary), {secondary_name} (fallback if exhausted)")
    else:
        logger.log("SETUP", f"Search provider: {primary_name} (no fallback configured — add the other provider's API key to enable one)")

    return FallbackSearchProvider(primary, primary_name, secondary, secondary_name, logger)


def _build_user_prompt(
    name, company, location, research_type, custom_request, top_results, pages, logger: RunLogger
) -> str:
    context_lines = [f"Target name: {name}"]
    if company:
        context_lines.append(f"Company context provided by user: {company}")
    if location:
        context_lines.append(f"Location context provided by user: {location}")
    context_lines.append(f"Research scope: {research_type}")
    if custom_request:
        context_lines.append(f"Custom research request: {custom_request}")

    pages_by_url = {p.url: p for p in pages}

    evidence_blocks = []
    for i, result in enumerate(top_results, start=1):
        page = pages_by_url.get(result.url)
        block = [
            f"--- SOURCE {i} ---",
            f"URL: {result.url}",
            f"DOMAIN: {result.domain}",
        ]
        if result.title:
            block.append(f"TITLE: {result.title}")
        if result.snippet:
            block.append(f"SNIPPET: {result.snippet[: settings.max_chars_per_snippet]}")
        if result.published_date:
            block.append(f"PUBLISHED: {result.published_date}")

        if page and page.success and page.text.strip():
            text = page.text[: settings.max_chars_per_page]
            block.append(f"PAGE CONTENT:\n{text}")
        else:
            block.append("PAGE CONTENT: not accessible — use TITLE/SNIPPET only, confidence capped below HIGH.")
        evidence_blocks.append("\n".join(block) + "\n")

    # Hard ceiling on the whole evidence section regardless of per-source
    # settings above — URLs/IDs tokenize inefficiently and per-source limits
    # alone weren't reliable enough on their own (this exact class of bug hit
    # twice). Drop lowest-ranked sources (from the end — top_results is already
    # best-first) until the assembled section actually fits the budget.
    char_budget = _evidence_char_budget()
    while evidence_blocks and sum(len(b) for b in evidence_blocks) > char_budget:
        dropped = evidence_blocks.pop()
        logger.log(
            "FILTER",
            f"Evidence section over budget — dropped lowest-ranked source to fit "
            f"({len(evidence_blocks)} remaining): {dropped.splitlines()[1] if len(dropped.splitlines()) > 1 else ''}",
        )

    return (
        "\n".join(context_lines)
        + "\n\nHere are the sources found for this research. Some have full page "
        "content, others only have the search engine's title/snippet because the "
        "full page could not be accessed — use both kinds of evidence, but weight "
        "confidence accordingly:\n\n"
        + "\n".join(evidence_blocks)
        + "\n\nNow produce the structured result.\n\n"
        "CRITICAL: the `evidence` list is the main output — the entire report is "
        "built from it, and an empty `evidence` list produces a blank report. Work "
        "through the sources ONE AT A TIME, SOURCE 1 first, then SOURCE 2, and so "
        "on to the last one. For each source, ask 'what does this tell me about a "
        "person with this name?' and emit an evidence entry for EVERY concrete "
        "fact: each job title, each employer, each degree, each institution, each "
        "certification, each profile, each publication, each legal matter. Do not "
        "stop after the first source — every source that says something about a "
        "matching person should produce at least one evidence entry, tagged to "
        "whichever identity candidate it belongs to. Cite the source by its SOURCE "
        "number in source_index. Writing a fact only in an identity candidate "
        "summary is NOT enough — it must also appear in `evidence` or it will not "
        "reach the user.\n\n"
        "Many sources are profile pages with section headings. Within EACH source, "
        "check every heading present — About, Experience, Education, Certifications, "
        "Honors & Awards, Publications, Organizations — and extract from all of "
        "them, not just the first one or two. A source whose Experience section you "
        "used but whose Education/Certifications sections you skipped is an "
        "incomplete read: go back and capture those too.\n\n"
        "Sources are ordered best-match-first, so SOURCE 1 and SOURCE 2 are the most "
        "likely to describe the actual target. Be exhaustive on those two before "
        "anything else — it is far worse to miss the target's own education or "
        "certifications than to miss a detail about a same-named stranger further "
        "down the list."
    )


def run_research(
    name: str,
    company: str,
    location: str,
    research_type: str,
    custom_request: str,
    logger: RunLogger,
) -> tuple[ResearchAnalysis | None, list[SearchResult], str | None]:
    """Returns (analysis, search_results_used, error_message)."""
    logger.log("INPUT", f"Person: {name}")

    queries = generate_queries(
        name, company, location, research_type, custom_request, settings.max_queries
    )
    logger.log("PLAN", f"{len(queries)} research queries generated across {len({c for c, _ in queries})} categories")

    provider = _get_search_provider(logger)

    # Queries were run one at a time — with 15 queries that's 15 sequential
    # network round-trips (often 20-30s total) before extraction could even
    # start. The search API call is independent per query, so run them
    # concurrently instead; wall-clock time drops to roughly the slowest single
    # query instead of the sum of all of them.
    all_results: dict[str, SearchResult] = {}
    result_categories: dict[str, set[str]] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(queries)) or 1) as pool:
        future_to_category = {
            pool.submit(provider.search, query, settings.max_results_per_query): category
            for category, query in queries
        }
        for future in as_completed(future_to_category):
            category = future_to_category[future]
            results = future.result()
            for r in results:
                if not r.url:
                    continue
                existing = all_results.get(r.url)
                if existing is None:
                    all_results[r.url] = r
                else:
                    # The search API returns a QUERY-SPECIFIC extract of each page,
                    # so the same LinkedIn profile yields its work history for one
                    # query and its education section for another. Keeping only the
                    # first-seen version silently dropped whatever the other queries
                    # found — which is why a person's education would appear in one
                    # run and vanish in the next. Merge instead of discarding.
                    existing.snippet = _merge_snippets(
                        existing.snippet, r.snippet, settings.max_chars_per_snippet
                    )
                    if not existing.title and r.title:
                        existing.title = r.title
                result_categories.setdefault(r.url, set()).add(category)

    logger.log("SEARCH", f"{len(all_results)} candidate results found")

    def _mentions_name(result: SearchResult) -> bool:
        haystack = f"{result.title} {result.snippet}".lower()
        name_parts = [p for p in name.lower().split() if len(p) > 1]
        return bool(name_parts) and all(p in haystack for p in name_parts)

    def _context_score(result: SearchResult) -> int:
        """How well a result matches the company/location the user supplied.

        This is the single strongest signal that a page is about the RIGHT
        person rather than a namesake, so it outranks everything else. Without
        it, every same-name LinkedIn profile scored identically and the ones
        actually mentioning the target's employer lost out to whichever result
        happened to arrive first — the search found the right person and the
        ranking then threw them away.
        """
        haystack = f"{result.title} {result.snippet}".lower()

        def _has_word(term: str) -> bool:
            # Whole-word match, NOT substring: a short company name like "EY"
            # is a substring of "they", "survey", "key" and "money", which made
            # almost every result look like a context match and destroyed the
            # signal this score exists to provide.
            return re.search(rf"\b{re.escape(term)}\b", haystack) is not None

        score = 0
        if company:
            terms = [t for t in company.lower().split() if len(t) > 1]
            if terms and all(_has_word(t) for t in terms):
                score += 2
        if location:
            terms = [t for t in location.lower().split() if len(t) > 1]
            if terms and all(_has_word(t) for t in terms):
                score += 1
        return score

    # Context match first (right person), then name match (mentions them at
    # all), then domain quality (how trustworthy the page is).
    ranked = sorted(
        all_results.values(),
        key=lambda r: (_context_score(r), _mentions_name(r), _score_domain(r.domain)),
        reverse=True,
    )
    if company or location:
        matched = sum(1 for r in ranked if _context_score(r) > 0)
        logger.log("FILTER", f"{matched} result(s) match the company/location context given")

    MAX_PER_DOMAIN = 3
    CONTEXT_RESERVED_SLOTS = 3

    def _root_domain(domain: str) -> str:
        parts = (domain or "").lower().split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else domain

    # Group ranked results by which category's query found them (a result can
    # belong to several). Round-robin across categories when picking extraction
    # slots — same problem as the query truncation above, one level later: a
    # single SEO-dominant category (LinkedIn under "professional") would
    # otherwise fill every slot and crowd out education/legal/social/etc even
    # when those searches DID find something.
    by_category: dict[str, list[SearchResult]] = {}
    for r in ranked:
        for cat in result_categories.get(r.url, {"context"}):
            by_category.setdefault(cat, []).append(r)
    category_order = [c for c in by_category if c != "context"]

    top_results: list[SearchResult] = []
    chosen_urls: set[str] = set()
    domain_counts: dict[str, int] = {}

    def _try_add(r: SearchResult) -> bool:
        if r.url in chosen_urls:
            return False
        key = _root_domain(r.domain)
        if domain_counts.get(key, 0) >= MAX_PER_DOMAIN:
            return False
        top_results.append(r)
        chosen_urls.add(r.url)
        domain_counts[key] = domain_counts.get(key, 0) + 1
        return True

    # Reserve the first slots for results that actually match the company or
    # location the user gave — these are the strongest evidence we have that a
    # page is about the RIGHT person. Without this the round-robin below could
    # (and did) skip the target's own profile: results found only by the
    # "name + company" query carry just the "context" tag, which the per-category
    # rotation excludes, so the single most on-target source was never picked.
    context_matches = [r for r in ranked if _context_score(r) > 0]
    for r in context_matches[:CONTEXT_RESERVED_SLOTS]:
        if len(top_results) >= settings.max_sources_to_extract:
            break
        if _try_add(r):
            logger.log("FILTER", f"  context match reserved: {r.url}")

    progressed = True
    while len(top_results) < settings.max_sources_to_extract and progressed:
        progressed = False
        for cat in category_order:
            if len(top_results) >= settings.max_sources_to_extract:
                break
            for r in by_category.get(cat, []):
                if r.url not in chosen_urls and _try_add(r):
                    progressed = True
                    break

    # Fill any still-empty slots from the overall ranking (ignoring the domain
    # cap this time) rather than waste extraction budget if categories run out
    # of distinct candidates before the domain cap does.
    for r in ranked:
        if len(top_results) >= settings.max_sources_to_extract:
            break
        if r.url not in chosen_urls:
            top_results.append(r)
            chosen_urls.add(r.url)

    logger.log("FILTER", f"{len(top_results)} relevant sources retained (round-robin across categories, max {MAX_PER_DOMAIN} per domain)")
    for r in top_results:
        cats = ", ".join(sorted(result_categories.get(r.url, {"context"})))
        logger.log("FILTER", f"  reviewing [{cats}]: {r.url}")

    if not top_results:
        return None, [], "No search results were found for this person. Try adding a company or location for more specific results."

    pages = extract_pages([r.url for r in top_results], settings.cache_ttl_hours)
    successful_pages = [p for p in pages if p.success and p.text.strip()]
    logger.log(
        "EXTRACTION",
        f"{len(successful_pages)} of {len(pages)} pages had full content extracted "
        f"(the rest still contribute their search snippet as evidence)",
    )

    user_prompt = _build_user_prompt(
        name, company, location, research_type, custom_request, top_results, pages, logger
    )

    try:
        analysis: ResearchAnalysis = llm_client.generate_structured(
            SYSTEM_PROMPT, user_prompt, ResearchAnalysis, logger=logger
        )
    except llm_client.LLMError as e:
        logger.log("ERROR", str(e))
        return None, top_results, f"Research could not be completed: {e}"

    # Resolve the source numbers the model cited back to real URLs. Doing this
    # in code means a URL can never be hallucinated or mistyped — the model only
    # had to pick which source it used, not reproduce the address.
    def _resolve(index: int) -> tuple[str, str]:
        i = index - 1  # model cites 1-based SOURCE numbers
        if 0 <= i < len(top_results):
            return top_results[i].url, top_results[i].domain
        return "", ""

    valid_evidence = []
    for e in analysis.evidence:
        url, domain = _resolve(e.source_index)
        if not url:
            logger.log("EVIDENCE", f"  dropped claim citing unknown source #{e.source_index}: {e.claim[:60]}")
            continue
        e.source_url, e.source_domain = url, domain or extract_domain(url)
        valid_evidence.append(e)
    analysis.evidence = valid_evidence

    for c in analysis.conflicts:
        c.source_a, _ = _resolve(c.source_a_index)
        c.source_b, _ = _resolve(c.source_b_index)

    logger.log("IDENTITY", f"{len(analysis.identity_candidates)} candidate identities detected")
    logger.log("EVIDENCE", f"{len(analysis.evidence)} factual claims extracted")
    verified = sum(1 for e in analysis.evidence if e.verification_status == "VERIFIED")
    logger.log(
        "VERIFICATION",
        f"{verified} verified, {len(analysis.evidence) - verified} not independently corroborated, "
        f"{len(analysis.conflicts)} conflicts found",
    )

    return analysis, top_results, None
