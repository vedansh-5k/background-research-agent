from models.schemas import SearchResult
from search.base import SearchProvider


class FallbackSearchProvider(SearchProvider):
    """Tries a primary provider first. If it comes back empty — rate-limited,
    quota exhausted, key invalid, or a genuine no-results — automatically retries
    the same query with the secondary provider, if one is configured."""

    def __init__(
        self,
        primary: SearchProvider,
        primary_name: str,
        secondary: SearchProvider | None = None,
        secondary_name: str = "",
        logger=None,
    ):
        self.primary = primary
        self.primary_name = primary_name
        self.secondary = secondary
        self.secondary_name = secondary_name
        self.logger = logger

    def _log(self, message: str) -> None:
        if self.logger:
            self.logger.log("SEARCH", message)

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        results = self.primary.search(query, max_results)
        if results:
            return results

        if not self.secondary:
            return []

        self._log(
            f"{self.primary_name} returned nothing for '{query}' "
            f"(rate limit, quota, or no results) — falling back to {self.secondary_name}"
        )
        results = self.secondary.search(query, max_results)
        if results:
            self._log(f"{self.secondary_name} fallback succeeded for '{query}'")
        return results
