import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    search_provider: str = os.getenv("SEARCH_PROVIDER", "tavily").lower()
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")
    serper_api_key: str = os.getenv("SERPER_API_KEY", "")

    # Which LLM does the reasoning/report-writing: "groq" or "gemini".
    # Groq's free tier has much higher rate limits, which matters more for a
    # prototype under active testing than small quality differences.
    llm_provider: str = os.getenv("LLM_PROVIDER", "groq").lower()

    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    # A "lite" model on purpose: Gemini is the fallback provider here, so what
    # matters is having free-tier headroom when the primary is exhausted, not
    # peak quality. The flagship flash model caps at 20 free requests and was
    # already spent when we fell back to it.
    # Not the "lite" variant: side-by-side on the same source, lite filed
    # certifications under "other" and found half the education entries that
    # gemini-3.5-flash did. The flagship (3.8) is capped at 20 free requests,
    # so 3.5-flash is the sweet spot for a fallback — quota headroom without
    # lite's sloppier categorisation.
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

    # 15 is enough for Comprehensive mode (up to 6 categories) to get ~2 queries
    # each after the 3 base (name/+company/+location) queries — 8 was silently
    # cutting off entire categories (publications/social/organization/legal)
    # before their queries were ever generated.
    max_queries: int = int(os.getenv("MAX_QUERIES", "15"))
    max_results_per_query: int = int(os.getenv("MAX_RESULTS_PER_QUERY", "6"))
    max_sources_to_extract: int = int(os.getenv("MAX_SOURCES_TO_EXTRACT", "8"))
    cache_ttl_hours: int = int(os.getenv("CACHE_TTL_HOURS", "24"))
    # Per-source ceiling on extracted page text sent to the LLM.
    max_chars_per_page: int = int(os.getenv("MAX_CHARS_PER_PAGE", "500"))
    # Per-source ceiling on Tavily's "content" field. NOT a throwaway one-line
    # snippet like a Google blurb — Tavily does real extraction, and for a full
    # LinkedIn profile this has measured at ~1400 characters (About/Experience/
    # Education/Certifications sections), even for pages our own crawler can't
    # access. Was wrongly capped at 250, then 900 — both still silently cut off
    # real education/certification data Tavily had already found. Set above the
    # observed real-world length; the max_evidence_section_chars ceiling below
    # is the actual backstop against oversized requests, not this number.
    # Sized to hold a MERGED profile, not a single extract. The search API
    # returns a different ~1,100-1,500 char slice of the same page per query
    # (work history for one, education for another); at 2000 the merge was
    # truncating ~580 chars and the Education section — being last — was what
    # got cut. Two full extracts need ~2,600.
    max_chars_per_snippet: int = int(os.getenv("MAX_CHARS_PER_SNIPPET", "3000"))
    # Hard ceiling on the WHOLE assembled evidence section (all sources combined),
    # enforced in code regardless of per-source settings above — belt and
    # suspenders against Groq's free tier 8000-token/request cap. URLs and IDs
    # tokenize far less efficiently than prose (a long hex-ID URL can cost 2-3x
    # more tokens than its character count suggests), so per-source char limits
    # alone aren't reliable enough on their own; this final check is what
    # actually guarantees we never send an oversized request again — it drops
    # the lowest-ranked source(s) first if the total is ever still too big.
    max_evidence_section_chars: int = int(os.getenv("MAX_EVIDENCE_SECTION_CHARS", "9000"))
    # Output ceiling for the structured extraction call. Must be generous enough
    # to hold a full evidence list — too low and the JSON truncates mid-document
    # and every extracted fact is lost.
    max_completion_tokens: int = int(os.getenv("MAX_COMPLETION_TOKENS", "3000"))
    # gpt-oss models are reasoning models: their internal reasoning tokens are
    # billed against max_completion_tokens. At default effort the reasoning ate
    # the whole budget and the model emitted an EMPTY response (which the API
    # reports as a JSON validation failure). "low" leaves room for actual output.
    groq_reasoning_effort: str = os.getenv("GROQ_REASONING_EFFORT", "low")
    # The provider's per-request token ceiling. Groq's free tier counts
    # input + reserved max_completion_tokens against this, so the evidence
    # section budget is computed from what's left after the system prompt,
    # schema and reserved output — see _evidence_char_budget(). Raise this if
    # you move to a paid tier or a provider with a bigger window.
    provider_token_budget: int = int(os.getenv("PROVIDER_TOKEN_BUDGET", "8000"))

    def missing_keys(self) -> list[str]:
        missing = []
        if self.search_provider == "tavily" and not self.tavily_api_key:
            missing.append("TAVILY_API_KEY")
        if self.search_provider == "serper" and not self.serper_api_key:
            missing.append("SERPER_API_KEY")
        if self.llm_provider == "groq" and not self.groq_api_key:
            missing.append("GROQ_API_KEY")
        if self.llm_provider == "gemini" and not self.gemini_api_key:
            missing.append("GEMINI_API_KEY")
        return missing


settings = Settings()
