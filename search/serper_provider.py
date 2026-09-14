import requests

from models.schemas import SearchResult
from search.base import SearchProvider, extract_domain

SERPER_URL = "https://google.serper.dev/search"


class SerperSearchProvider(SearchProvider):
    def __init__(self, api_key: str):
        self.api_key = api_key

    def search(self, query: str, max_results: int = 6) -> list[SearchResult]:
        try:
            resp = requests.post(
                SERPER_URL,
                headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
                json={"q": query, "num": max_results},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        results = []
        for item in data.get("organic", [])[:max_results]:
            url = item.get("link", "")
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=url,
                    snippet=item.get("snippet", ""),
                    domain=extract_domain(url),
                    published_date=item.get("date"),
                )
            )
        return results
