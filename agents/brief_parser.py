"""
Turns a free-text (or JSON) research brief into a concrete research plan.

This is the piece that replaces hardcoded category keywords. The user's brief
decides what gets searched: a brief naming watchoutinvestors.com and the RBI
defaulter list produces queries targeting those, without anyone editing code.

A small guaranteed baseline is added afterwards in code — not to override the
LLM, but so that a poor planning round can never silently skip the essentials
(the subject's own name, its identifiers, and core adverse-media terms).
"""
import json

from agents import llm_client
from models.amc_schemas import ResearchPlan
from utils.logger import RunLogger

PLANNER_SYSTEM_PROMPT = """You plan open-source research from a brief.

Read the user's brief and extract: what entity is being investigated, any
identifiers given, whether associated people (directors/officers) must also be
checked, what topics to look for, which sources the brief specifically names,
and what the output must contain.

Then write the web search queries you would actually run to answer that brief.

Rules for queries:
- Write 8-14 queries. Each must be a realistic web search, not a sentence.
- Put the subject's name in most of them.
- If the brief names specific sources or databases, write site-targeted queries
  for them (e.g. 'site:watchoutinvestors.com SUBJECT NAME').
- If identifiers are given (CIN, PAN, registration numbers), search them directly
  — they are the strongest way to confirm you have the right entity.
- Cover the topics the brief asks about in its own words. Do not substitute your
  own standard checklist for what the brief actually requested.
- If the brief asks about directors, include a query to discover who they are.

Extract only what the brief says. Do not invent identifiers or sources."""


def _baseline_queries(plan: ResearchPlan) -> list[str]:
    """Essentials that must be searched regardless of what the planner produced."""
    name = plan.subject_name
    queries = [name, f"{name} fraud OR investigation OR penalty"]
    for ident in plan.identifiers:
        # "CIN: U51101..." -> search the value, which uniquely identifies the entity
        value = ident.split(":", 1)[-1].strip()
        if value:
            queries.append(value)
    if plan.include_directors:
        queries.append(f"{name} directors")
    return queries


def build_plan(brief: str, logger: RunLogger, max_queries: int) -> ResearchPlan:
    """LLM reads the brief and returns a plan. Raises LLMError if it can't."""
    plan: ResearchPlan = llm_client.generate_structured(
        PLANNER_SYSTEM_PROMPT,
        f"Here is the brief:\n\n{brief}\n\nProduce the research plan.",
        ResearchPlan,
        logger=logger,
    )

    logger.log("PLAN", f"Subject: {plan.subject_name} ({plan.subject_type})")
    if plan.identifiers:
        logger.log("PLAN", f"Identifiers: {', '.join(plan.identifiers)}")
    if plan.preferred_sources:
        logger.log("PLAN", f"Sources named in brief: {', '.join(plan.preferred_sources)}")
    logger.log("PLAN", f"Topics: {', '.join(plan.investigation_topics)}")
    logger.log("PLAN", f"Check directors: {plan.include_directors}")

    # Merge LLM queries with the baseline, LLM first, de-duplicated.
    seen: set[str] = set()
    merged: list[str] = []
    for q in list(plan.queries) + _baseline_queries(plan):
        key = q.lower().strip()
        if key and key not in seen:
            seen.add(key)
            merged.append(q)
    plan.queries = merged[:max_queries]

    logger.log("PLAN", f"{len(plan.queries)} queries planned by the model (+ baseline safety queries)")
    for q in plan.queries:
        logger.log("PLAN", f"  → {q}")
    return plan


def looks_like_json(brief: str) -> bool:
    stripped = brief.strip()
    return stripped.startswith("{") and stripped.endswith("}")


def normalise_brief(brief: str) -> str:
    """Accept either free text or JSON. JSON is flattened to readable lines so the
    planner sees the same shape either way (the brief may arrive as a form payload
    rather than prose)."""
    if not looks_like_json(brief):
        return brief
    try:
        data = json.loads(brief)
    except json.JSONDecodeError:
        return brief
    lines = []
    for key, value in data.items():
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value)
        lines.append(f"{key}: {value}")
    return "\n".join(lines)
