import datetime

from agents import llm_client
from models.schemas import Evidence, ResearchAnalysis, SearchResult
from utils.logger import RunLogger

REPORT_SYSTEM_PROMPT = (
    "You are writing the prose sections of a background research report. You must "
    "only summarize the structured evidence you are given below — never add a fact "
    "that is not present in it. Be concise, neutral, and explicit about uncertainty. "
    "Do not state something is confirmed unless the evidence marks it VERIFIED."
)


def _by_category(evidence: list[Evidence], category: str) -> list[Evidence]:
    return [e for e in evidence if e.category == category]


def _evidence_table(items: list[Evidence]) -> str:
    """Returns "" when there's nothing — callers skip the whole section rather
    than printing a row of "no findings" placeholders for every empty category."""
    if not items:
        return ""
    # "Who" column matters when several people share the name: without it a
    # reader merges a company director, an actor and an analyst into one
    # fictional CV.
    rows = ["| Who | Finding | Evidence | Confidence | Source |", "|---|---|---|---|---|"]
    for e in items:
        excerpt = e.evidence_text.replace("\n", " ").replace("|", "/")[:160]
        claim = e.claim.replace("|", "/")
        who = (e.identity_label or "unattributed").replace("|", "/")
        rows.append(
            f"| {who} | {claim} | {excerpt} | {e.claim_confidence} | [{e.source_domain}]({e.source_url}) |"
        )
    return "\n".join(rows) + "\n"


def _identity_section(analysis: ResearchAnalysis) -> str:
    if not analysis.identity_candidates:
        return "No distinguishable identity could be established from the available evidence.\n"
    if len(analysis.identity_candidates) > 1:
        lines = ["**Multiple potentially matching identities were identified. They have not been merged.**\n"]
    else:
        lines = []
    for c in analysis.identity_candidates:
        marker = " (likely target)" if c.is_likely_target else ""
        lines.append(f"### {c.label}{marker}")
        lines.append(f"- Summary: {c.summary}")
        lines.append(f"- Matching signals: {', '.join(c.matching_signals) or 'none listed'}")
        lines.append(f"- Identity confidence: **{c.confidence}**\n")
    return "\n".join(lines)


def _conflicts_section(analysis: ResearchAnalysis) -> str:
    if not analysis.conflicts:
        return ""
    lines = []
    for c in analysis.conflicts:
        lines.append(f"**{c.category}**")
        lines.append(f"- Source A ([{c.source_a}]({c.source_a})) says: {c.claim_a}")
        lines.append(f"- Source B ([{c.source_b}]({c.source_b})) says: {c.claim_b}")
        if c.note:
            lines.append(f"- Note: {c.note}")
        lines.append("- The information could not be conclusively reconciled.\n")
    return "\n".join(lines)


def _unverified_section(analysis: ResearchAnalysis) -> str:
    unverified = [e for e in analysis.evidence if e.verification_status == "UNVERIFIED"]
    return _evidence_table(unverified)


def _source_list(analysis: ResearchAnalysis, search_results: list[SearchResult]) -> str:
    """Every source, in two clearly separated groups. Cited sources back a claim
    in this report. Reviewed-but-unused pages are listed separately and labelled
    as such — they must never be presented as if they said something about this
    person, but hiding them entirely loses the audit trail of what was checked."""
    cited: dict[str, str] = {}
    for e in analysis.evidence:
        cited[e.source_url] = e.source_domain

    out = []
    if cited:
        out.append("**Sources cited in this report** (each backs at least one finding above):\n")
        rows = ["| # | Domain | Link |", "|---|---|---|"]
        for i, (url, domain) in enumerate(cited.items(), start=1):
            rows.append(f"| {i} | {domain} | [{url}]({url}) |")
        out.append("\n".join(rows) + "\n")
    else:
        out.append("No source in this run contained verifiable information about this person.\n")

    others = [r for r in search_results if r.url not in cited]
    if others:
        out.append(
            f"\n**Also reviewed — {len(others)} page(s) that were searched and read but did NOT "
            f"yield verifiable information about this person** (listed for traceability; "
            f"they are not evidence about this person):\n"
        )
        rows = ["| # | Domain | Link |", "|---|---|---|"]
        for i, r in enumerate(others, start=1):
            rows.append(f"| {i} | {r.domain} | [{r.url}]({r.url}) |")
        out.append("\n".join(rows) + "\n")

    return "\n".join(out)


def _llm_prose(prompt: str, logger: RunLogger) -> str:
    try:
        return llm_client.generate_text(REPORT_SYSTEM_PROMPT, prompt, logger=logger)
    except llm_client.LLMError as e:
        return f"_Could not generate this section: {e}_"


def build_report(
    name: str,
    company: str,
    location: str,
    research_type: str,
    custom_request: str,
    analysis: ResearchAnalysis,
    search_results: list[SearchResult],
    logger: RunLogger,
) -> str:
    evidence_summary = "\n".join(
        f"- [{e.category}] {e.claim} (confidence: {e.claim_confidence}, status: {e.verification_status})"
        for e in analysis.evidence
    ) or "No evidence was extracted."

    identity_summary = "\n".join(
        f"- {c.label}: {c.summary} (confidence: {c.confidence})" for c in analysis.identity_candidates
    ) or "No identity candidates established."

    exec_summary = _llm_prose(
        f"Write a short (3-6 sentence) executive summary for a background research "
        f"report on '{name}'. Base it ONLY on this evidence and identity data:\n\n"
        f"Identity candidates:\n{identity_summary}\n\nEvidence:\n{evidence_summary}",
        logger,
    )

    reliability_notes = _llm_prose(
        f"Write a short 'Reliability and Limitations' section (3-6 sentences) for a "
        f"background research report on '{name}'. Mention identity confidence, source "
        f"quality, any unresolved conflicts, and any categories with no verified "
        f"evidence. Base it ONLY on this data:\n\nIdentity candidates:\n{identity_summary}\n\n"
        f"Evidence:\n{evidence_summary}\n\nConflicts: {len(analysis.conflicts)}\n"
        f"Categories with no evidence: {', '.join(analysis.categories_with_no_evidence) or 'none'}",
        logger,
    )
    logger.log("REPORT", "Report generated")

    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    scope_line = f"{research_type}{f' — {custom_request}' if custom_request else ''}"
    target_block = "\n".join(
        [
            f"- **Name:** {name}",
            f"- **Company:** {company or 'not provided'}",
            f"- **Location:** {location or 'not provided'}",
            f"- **Research scope:** {scope_line}",
        ]
    )

    # Only sections with actual content get rendered, and they're numbered as
    # they're added — an empty category is simply absent rather than printing a
    # "no findings" placeholder. What wasn't found is summarised in one line at
    # the end instead of seven near-identical empty blocks.
    category_sections = [
        ("Professional Background", "professional"),
        ("Education", "education"),
        ("Professional / Social Profiles", "social_profile"),
        ("Publications / Research", "publications"),
        ("Organizations / Companies", "organization"),
        ("Public Legal / Regulatory Information", "legal"),
        ("Other Significant Public Information", "other"),
    ]

    sections: list[tuple[str, str]] = [
        ("Search Target", target_block),
        ("Identity Resolution", _identity_section(analysis)),
        ("Executive Summary", exec_summary),
    ]

    empty_categories = []
    for title, key in category_sections:
        table = _evidence_table(_by_category(analysis.evidence, key))
        if table:
            sections.append((title, table))
        else:
            empty_categories.append(title)

    conflicts = _conflicts_section(analysis)
    if conflicts:
        sections.append(("Conflicting Information", conflicts))

    unverified = _unverified_section(analysis)
    if unverified:
        sections.append(("Unverified Information", unverified))

    sections.append(("Source List", _source_list(analysis, search_results)))

    if empty_categories:
        sections.append(
            (
                "Categories With No Findings",
                "No verifiable public information about this person was found for: "
                + ", ".join(empty_categories)
                + ".\n\nThis means nothing was found in the sources searched — it does "
                "not mean nothing exists.\n",
            )
        )

    sections.append(("Reliability / Limitations", reliability_notes))

    body = "\n\n".join(
        f"## {i}. {title}\n{content}" for i, (title, content) in enumerate(sections, start=1)
    )

    return f"# AI Public-Web Background Research Report\n\n*Generated {generated_at}*\n\n{body}\n"
