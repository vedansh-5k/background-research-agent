"""
Adverse Media Check orchestrator.

Differs from person-research mode in three ways:
  1. The plan comes from the LLM reading the user's brief, not from fixed keywords.
  2. It runs in two stages — the entity first, then each director it discovered,
     because you cannot search for directors until you know who they are.
  3. Output is risk-shaped (match classification, severity, overall risk) rather
     than biography-shaped.

Search, ranking and extraction are shared with person-research mode.
"""
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from agents import llm_client
from agents.brief_parser import build_plan, normalise_brief
from agents.research_agent import (
    _evidence_char_budget,
    _get_search_provider,
    _merge_snippets,
    _score_domain,
)
from config.settings import settings
from extraction.web_extractor import extract_pages
from models.amc_schemas import AMCAnalysis, ResearchPlan
from models.schemas import SearchResult
from search.base import extract_domain
from utils.logger import RunLogger

MAX_DIRECTORS_TO_CHECK = int(__import__("os").getenv("MAX_DIRECTORS_TO_CHECK", "6"))

ANALYSIS_SYSTEM_PROMPT = """You are an evidence-first adverse media analyst.

Your ONLY source of truth is the SOURCE blocks provided. Never use your own
knowledge about this entity. Never invent findings, dates, case numbers or URLs.

RULES

1. CONFIRM THE ENTITY FIRST. Only treat a source as being about the subject if it
   genuinely refers to it. Where identifiers (CIN, PAN, registration numbers) are
   given, a source matching one is strong confirmation. A similar company name
   alone is not.

2. MATCH CLASSIFICATION on every finding:
   - STRONG: identifiers match, or several specifics line up (exact legal name +
     location + sector)
   - WEAK: the name matches but nothing else confirms it is the same entity
   - UNCERTAIN: you cannot tell
   A finding about a similarly-named but unconfirmed entity is WEAK or UNCERTAIN,
   never STRONG, no matter how serious the allegation or how credible the source.

3. ALLEGATION IS NOT GUILT. Distinguish conviction, charge, arrest, raid,
   investigation, regulatory penalty, civil suit, and mere allegation. Report as
   "According to [source], X was investigated for Y" — never "X did Y".

4. NOTHING FOUND IS NOT A CLEAN RECORD. If no adverse media appears, return an
   empty adverse_findings list and say plainly in clean_assessment what was
   searched and that nothing adverse appeared in those sources. Never state or
   imply the entity has no issues, no record, or is clean.

5. CITE BY NUMBER. Use the SOURCE number in source_index. Never write a URL.
   Keep evidence_text to a short quote (max 30 words).

6. RISK RATING reflects only what the evidence shows. No confirmed adverse
   findings means LOW — with the caveat recorded in limitations, not hidden."""


def _context_score(result: SearchResult, subject: str, identifiers: list[str]) -> int:
    """Identifier match is the strongest possible confirmation for a company."""
    haystack = f"{result.title} {result.snippet}".lower()
    score = 0
    for ident in identifiers:
        value = ident.split(":", 1)[-1].strip().lower()
        if value and value in haystack:
            score += 5  # a CIN/PAN match is decisive
    subject_terms = [t for t in re.split(r"\W+", subject.lower()) if len(t) > 2]
    if subject_terms and all(
        re.search(rf"\b{re.escape(t)}\b", haystack) for t in subject_terms[:3]
    ):
        score += 2
    return score


def _gather(queries: list[str], subject: str, identifiers: list[str], logger: RunLogger, max_sources: int):
    """Run the queries, rank, select and extract. Shared shape with person mode."""
    provider = _get_search_provider(logger)

    all_results: dict[str, SearchResult] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(queries)) or 1) as pool:
        futures = [pool.submit(provider.search, q, settings.max_results_per_query) for q in queries]
        for future in as_completed(futures):
            for r in future.result():
                if not r.url:
                    continue
                existing = all_results.get(r.url)
                if existing is None:
                    all_results[r.url] = r
                else:
                    existing.snippet = _merge_snippets(
                        existing.snippet, r.snippet, settings.max_chars_per_snippet
                    )
    logger.log("SEARCH", f"{len(all_results)} candidate results found")

    ranked = sorted(
        all_results.values(),
        key=lambda r: (_context_score(r, subject, identifiers), _score_domain(r.domain)),
        reverse=True,
    )
    confirmed = sum(1 for r in ranked if _context_score(r, subject, identifiers) >= 5)
    if identifiers:
        logger.log("FILTER", f"{confirmed} result(s) matched a given identifier (CIN/PAN)")

    top = ranked[:max_sources]
    logger.log("FILTER", f"{len(top)} sources selected")
    for r in top:
        logger.log("FILTER", f"  reviewing: {r.url}")

    pages = extract_pages([r.url for r in top], settings.cache_ttl_hours)
    ok = [p for p in pages if p.success and p.text.strip()]
    logger.log("EXTRACTION", f"{len(ok)} of {len(pages)} pages had full content extracted")
    return top, pages


def _build_prompt(subject: str, identifiers: list[str], topics: list[str], top, pages, logger) -> str:
    pages_by_url = {p.url: p for p in pages}
    header = [f"Subject under investigation: {subject}"]
    if identifiers:
        header.append("Identifiers given: " + "; ".join(identifiers))
    if topics:
        header.append("Look for: " + ", ".join(topics))

    blocks = []
    for i, r in enumerate(top, start=1):
        page = pages_by_url.get(r.url)
        block = [f"--- SOURCE {i} ---", f"URL: {r.url}", f"DOMAIN: {r.domain}"]
        if r.title:
            block.append(f"TITLE: {r.title}")
        if r.snippet:
            block.append(f"SNIPPET: {r.snippet[: settings.max_chars_per_snippet]}")
        if page and page.success and page.text.strip():
            block.append(f"PAGE CONTENT:\n{page.text[: settings.max_chars_per_page]}")
        else:
            block.append("PAGE CONTENT: not accessible — use TITLE/SNIPPET only.")
        blocks.append("\n".join(block) + "\n")

    budget = _evidence_char_budget()
    while blocks and sum(len(b) for b in blocks) > budget:
        blocks.pop()
        logger.log("FILTER", f"Evidence over budget — dropped lowest-ranked source ({len(blocks)} left)")

    return (
        "\n".join(header)
        + "\n\nSources:\n\n"
        + "\n".join(blocks)
        + "\n\nAnalyse these sources and return the structured result. Confirm the entity "
        "first, then record every adverse finding with its match classification. If the "
        "sources contain no adverse media, return an empty adverse_findings list and "
        "explain in clean_assessment what was searched."
    )


def _resolve_sources(analysis: AMCAnalysis, top: list[SearchResult], logger: RunLogger) -> None:
    """Turn cited source numbers into real URLs — the model never writes a URL."""

    def resolve(idx: int):
        i = idx - 1
        if 0 <= i < len(top):
            return top[i].url, top[i].domain or extract_domain(top[i].url)
        return "", ""

    kept = []
    for f in analysis.adverse_findings:
        url, domain = resolve(f.source_index)
        if not url:
            logger.log("EVIDENCE", f"  dropped finding citing unknown source #{f.source_index}")
            continue
        f.source_url, f.source_domain = url, domain
        kept.append(f)
    analysis.adverse_findings = kept

    for d in analysis.directors:
        d.source_url, _ = resolve(d.source_index)


def _analyse(subject, identifiers, topics, top, pages, logger) -> AMCAnalysis:
    prompt = _build_prompt(subject, identifiers, topics, top, pages, logger)
    analysis: AMCAnalysis = llm_client.generate_structured(
        ANALYSIS_SYSTEM_PROMPT, prompt, AMCAnalysis, logger=logger
    )
    _resolve_sources(analysis, top, logger)
    return analysis


def run_amc(brief: str, logger: RunLogger):
    """Returns (plan, company_analysis, director_analyses, error)."""
    logger.log("INPUT", "Adverse Media Check requested")

    try:
        plan: ResearchPlan = build_plan(normalise_brief(brief), logger, settings.max_queries)
    except llm_client.LLMError as e:
        logger.log("ERROR", str(e))
        return None, None, {}, f"Could not plan the research: {e}"

    # --- Stage 1: the entity itself -------------------------------------------
    top, pages = _gather(plan.queries, plan.subject_name, plan.identifiers, logger, settings.max_sources_to_extract)
    if not top:
        return plan, None, {}, "No search results were found for this entity."

    try:
        company = _analyse(plan.subject_name, plan.identifiers, plan.investigation_topics, top, pages, logger)
    except llm_client.LLMError as e:
        logger.log("ERROR", str(e))
        return plan, None, {}, f"Analysis failed: {e}"

    logger.log("ENTITY", f"Subject confirmed in sources: {company.subject_confirmed}")
    logger.log("ENTITY", f"{len(company.adverse_findings)} adverse finding(s), {len(company.directors)} director(s) found")

    company_sources = list(top)

    # --- Stage 2: each director discovered ------------------------------------
    director_analyses: dict[str, tuple] = {}
    if plan.include_directors and company.directors:
        for director in company.directors[:MAX_DIRECTORS_TO_CHECK]:
            name = director.name.strip()
            if not name:
                continue
            logger.log("DIRECTOR", f"Checking: {name}")
            dir_queries = [
                f'"{name}" "{plan.subject_name}"',
                f'"{name}" (fraud OR arrest OR investigation OR chargesheet OR FIR)',
                f'"{name}" (CBI OR ED OR SFIO OR "Enforcement Directorate")',
                f'"{name}" director (penalty OR disqualified OR defaulter)',
            ]
            d_top, d_pages = _gather(dir_queries, name, [], logger, max(4, settings.max_sources_to_extract // 2))
            if not d_top:
                continue
            try:
                d_analysis = _analyse(name, [], plan.investigation_topics, d_top, d_pages, logger)
                director_analyses[name] = (d_analysis, d_top)
                logger.log("DIRECTOR", f"  {name}: {len(d_analysis.adverse_findings)} adverse finding(s)")
            except llm_client.LLMError as e:
                logger.log("ERROR", f"  {name}: analysis failed — {e}")

    return plan, (company, company_sources), director_analyses, None
