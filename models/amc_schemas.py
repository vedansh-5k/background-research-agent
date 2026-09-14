"""
Schemas for the Adverse Media Check (AMC) mode.

The important difference from person-research mode: the RESEARCH PLAN itself is
produced by the LLM from the user's free-text (or JSON) brief. Nothing about
which categories to investigate, which sources to prefer, or which queries to
run is hardcoded here — the brief decides, so a brief asking about RBI defaulter
lists produces RBI queries without anyone editing code.
"""
from typing import Optional

from pydantic import BaseModel, Field


class ResearchPlan(BaseModel):
    """What the LLM decided to do, after reading the user's brief."""

    subject_name: str = Field(description="The main entity to investigate, exactly as named in the brief")
    subject_type: str = Field(description="'company' or 'person'")
    identifiers: list[str] = Field(
        description="Identifiers given in the brief, each as 'LABEL: VALUE' e.g. 'CIN: U51101HR2007PTC138314'"
    )
    include_directors: bool = Field(
        description="True if the brief asks to also check directors/officers/associated people"
    )
    investigation_topics: list[str] = Field(
        description="What to look for, taken from the brief, e.g. 'regulatory actions', "
        "'economic offences', 'terrorism-related mentions'"
    )
    preferred_sources: list[str] = Field(
        description="Specific sources or databases the brief names, e.g. 'watchoutinvestors.com', "
        "'RBI defaulter list'. Empty list if none named."
    )
    output_requirements: list[str] = Field(
        description="What the brief says the output must contain, e.g. 'match classification', "
        "'overall risk impression', 'disclaimers', 'date of search'"
    )
    queries: list[str] = Field(
        description="8-14 web search queries you will run, written to find exactly what the brief asks "
        "for. Include the subject name in most. Use identifiers where useful. If the brief names "
        "specific sources, include site-targeted queries for them."
    )


class AdverseFinding(BaseModel):
    """One adverse-media item found about the subject or one of its directors."""

    about: str = Field(description="Who/what this concerns — the company name or a director's name")
    issue_type: str = Field(
        description="e.g. criminal case, economic offence, ED/CBI/SFIO action, tax raid, "
        "regulatory penalty, terrorism-related, civil litigation, other"
    )
    summary: str = Field(description="What the source reports, stated neutrally and attributed")
    source_index: int = Field(description="The SOURCE number supporting this")
    evidence_text: str = Field(description="Short exact quote from that source. Max 30 words.")
    match_classification: str = Field(
        description="STRONG (identifiers or several specifics match), WEAK (name matches but little "
        "else), or UNCERTAIN (cannot tell whether this is the same entity)"
    )
    severity: str = Field(description="LOW, MEDIUM or HIGH")

    # Filled in by code from source_index.
    source_url: str = ""
    source_domain: str = ""


class DirectorInfo(BaseModel):
    name: str
    role: str = ""
    source_index: int = Field(description="SOURCE number where this director was named")
    source_url: str = ""


class AMCAnalysis(BaseModel):
    """The structured result of an adverse media check."""

    subject_confirmed: bool = Field(
        description="True only if a source confirms the subject entity itself (matching name and, "
        "where given, identifiers)"
    )
    subject_summary: str = Field(description="What the sources establish about the entity itself")
    directors: list[DirectorInfo] = Field(
        description="Directors/officers found in the sources. Empty if none found or not requested."
    )
    adverse_findings: list[AdverseFinding] = Field(
        description="REQUIRED. Every adverse item found. Empty list ONLY if the sources genuinely "
        "contain none — an empty list means the report will state that no adverse media was found."
    )
    clean_assessment: str = Field(
        description="If no adverse findings: state plainly what was searched and that nothing adverse "
        "was found in those sources. Never claim the entity is clean overall."
    )
    overall_risk: str = Field(description="LOW, MEDIUM or HIGH — based only on what the evidence shows")
    risk_reasoning: str = Field(description="Why that rating, referencing the findings")
    limitations: list[str] = Field(
        description="What could not be checked and why, e.g. sources behind paywalls/logins, "
        "identifiers that could not be verified, databases not publicly searchable"
    )
