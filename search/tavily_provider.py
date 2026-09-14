from models.schemas import SearchResult
from search.base import SearchProvider, extract_domain


class TavilySearchProvider(SearchProvider):
    def __init__(self, api_key: str):
        from tavily import TavilyClient

        self.client = TavilyClient(api_key=api_key)

    def search(self, query: str, max_results: int = 6) -> list[SearchResult]:
        try:
            response = self.client.search(
                query=query, max_results=max_results, search_depth="basic"
            )
        except Exception:
            return []

        results = []
        for item in response.get("results", []):
            url = item.get("url", "")
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=url,
                    snippet=item.get("content", ""),
                    domain=extract_domain(url),
                    published_date=item.get("published_date"),
                )
            )
        return results
