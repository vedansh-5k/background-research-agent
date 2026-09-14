from abc import ABC, abstractmethod
from urllib.parse import urlparse

from models.schemas import SearchResult


def extract_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


class SearchProvider(ABC):
    @abstractmethod
    def search(self, query: str, max_results: int) -> list[SearchResult]:
        """Run one search query and return normalized results. Must never raise
        on a failed/empty search — return an empty list instead."""
        raise NotImplementedError
