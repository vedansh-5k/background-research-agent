from typing import Optional
from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str = ""
    domain: str = ""
    published_date: Optional[str] = None


class ExtractedPage(BaseModel):
    url: str
    title: str = ""
    domain: str = ""
    text: str = ""
    success: bool = True
    error: Optional[str] = None


class Evidence(BaseModel):
    # source_index instead of a full URL: repeating a long URL in every entry
    # burned enough output tokens to truncate the model's JSON mid-document
    # (which silently lost ALL evidence). An integer also can't be mangled the
    # way a copied URL can — the real URL is filled in by code afterwards.
    claim: str = Field(description="One factual claim, stated neutrally.")
    category: str = Field(
        description="One of: identity, professional, education, publications, "
        "social_profile, organization, legal, other"
    )
    source_index: int = Field(description="The SOURCE number (1, 2, 3...) that supports this claim")
    identity_label: str = Field(
        description="Which identity candidate this fact belongs to, e.g. 'Candidate A'. "
        "Required — facts about different same-named people must stay distinguishable."
    )
    evidence_text: str = Field(description="Short exact quote from that source supporting the claim. Max 25 words.")
    claim_confidence: str = Field(description="HIGH, MEDIUM, or LOW")
    verification_status: str = Field(
        description="VERIFIED (multiple independent sources), "
        "PARTIALLY_SUPPORTED (one source), or UNVERIFIED"
    )

    # Filled in by code from source_index — never by the model.
    source_url: str = ""
    source_domain: str = ""


class IdentityCandidate(BaseModel):
    label: str = Field(description="e.g. 'Candidate A'")
    summary: str
    matching_signals: list[str] = Field(default_factory=list)
    confidence: str = Field(description="HIGH, MEDIUM, or LOW")
    is_likely_target: bool = False


class Conflict(BaseModel):
    category: str
    claim_a: str
    source_a_index: int = Field(description="SOURCE number supporting claim_a")
    claim_b: str
    source_b_index: int = Field(description="SOURCE number supporting claim_b")
    note: str = ""

    # Filled in by code from the indices above.
    source_a: str = ""
    source_b: str = ""


class ResearchAnalysis(BaseModel):
    # evidence and identity_candidates are deliberately REQUIRED (no defaults).
    # With defaults they were optional in the generated JSON schema, and the
    # model simply omitted the evidence list entirely — producing reports with
    # rich identity summaries and zero findings. An empty list still validates,
    # so a genuine "nothing found" is still expressible.
    identity_candidates: list[IdentityCandidate] = Field(
        description="Each distinct person matching this name found in the sources"
    )
    evidence: list[Evidence] = Field(
        description="REQUIRED. One entry per distinct fact found. This is the main "
        "output — the report is built from it. Never leave empty if the sources "
        "contain any fact about the person."
    )
    conflicts: list[Conflict] = Field(default_factory=list)
    categories_with_no_evidence: list[str] = Field(default_factory=list)
