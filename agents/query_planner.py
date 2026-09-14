"""
Query generation is deliberately rule-based, not an LLM call: it's free, instant,
and 100% predictable, which matters for cost control (spec section 24). The LLM's
reasoning is spent later, on identity resolution and evidence extraction, where it
actually adds value.
"""
from itertools import zip_longest

CATEGORY_KEYWORDS = {
    "professional": ["company", "LinkedIn", "official profile"],
    "education": ["education", "university", "college"],
    "publications": ["publication", "research paper", "patent"],
    "social_profile": ["Instagram", "Facebook", "Twitter OR X profile", "GitHub"],
    "organization": ["company employee", "team page"],
    "legal": ["court", "lawsuit", "judgment"],
}

ALL_CATEGORIES = list(CATEGORY_KEYWORDS.keys())

CUSTOM_REQUEST_KEYWORDS = {
    "education": ["education", "university", "college", "degree", "academic"],
    "professional": ["professional", "employment", "job", "career", "work"],
    "publications": ["publication", "research", "paper", "patent"],
    "social_profile": ["social", "profile", "linkedin", "github", "twitter", "instagram", "facebook"],
    "organization": ["company", "organization", "employer"],
    "legal": ["legal", "court", "criminal", "lawsuit", "regulatory", "crime"],
}

RESEARCH_TYPE_TO_CATEGORIES = {
    "Comprehensive": ALL_CATEGORIES,
    "Professional": ["professional", "organization"],
    "Education": ["education"],
    "Publications": ["publications"],
    "Legal / Public Court Information": ["legal"],
    "Social / Professional Profiles": ["social_profile", "professional"],
    "Company Relationship": ["organization", "professional"],
}


def _categories_for(research_type: str, custom_request: str) -> list[str]:
    if research_type == "Custom" and custom_request.strip():
        text = custom_request.lower()
        matched = [
            cat
            for cat, keywords in CUSTOM_REQUEST_KEYWORDS.items()
            if any(kw in text for kw in keywords)
        ]
        return matched or ALL_CATEGORIES
    return RESEARCH_TYPE_TO_CATEGORIES.get(research_type, ALL_CATEGORIES)


def generate_queries(
    name: str,
    company: str,
    location: str,
    research_type: str,
    custom_request: str,
    max_queries: int,
) -> list[tuple[str, str]]:
    """Returns (category, query) pairs. Context queries (bare name / +company /
    +location) are tagged category "context" since they aren't category-specific.

    Queries are interleaved round-robin across categories (one from each, then a
    second from each, ...) rather than fully exhausting one category before the
    next. This matters because max_queries can truncate the list — round-robin
    means truncation trims every category a little instead of wiping some out
    entirely, which is what silently happened before this was fixed.
    """
    base: list[tuple[str, str]] = [("context", name)]
    if company:
        base.append(("context", f"{name} {company}"))
    if location:
        base.append(("context", f"{name} {location}"))

    categories = _categories_for(research_type, custom_request)

    # When the user gave a company (or failing that, a location), the FIRST query
    # of each category carries it — "Ayush Bhardwaj EY education" rather than
    # "Ayush Bhardwaj education". Without this, category searches returned some
    # other same-named person's degrees, so the target's own education/
    # publications/etc were never retrieved even when his profile was found.
    # Later queries in each category stay unqualified, so a person whose context
    # isn't indexed alongside the keyword is still discoverable.
    context_term = company or location or ""

    template_lists = []
    for cat in categories:
        items = []
        for i, keyword in enumerate(CATEGORY_KEYWORDS[cat]):
            if i == 0 and context_term:
                items.append((cat, f"{name} {context_term} {keyword}"))
            else:
                items.append((cat, f"{name} {keyword}"))
        template_lists.append(items)

    interleaved: list[tuple[str, str]] = []
    for round_items in zip_longest(*template_lists):
        for item in round_items:
            if item is not None:
                interleaved.append(item)

    all_queries = base + interleaved

    seen = set()
    deduped: list[tuple[str, str]] = []
    for cat, q in all_queries:
        key = q.lower().strip()
        if key not in seen:
            seen.add(key)
            deduped.append((cat, q))

    return deduped[:max_queries]
