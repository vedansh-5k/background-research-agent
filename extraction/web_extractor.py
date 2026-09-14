import asyncio

from models.schemas import ExtractedPage
from search.base import extract_domain
from utils.cache import get_cached, set_cached


async def _extract_one(crawler, url: str, timeout_ms: int = 15000) -> ExtractedPage:
    try:
        from crawl4ai import CrawlerRunConfig

        config = CrawlerRunConfig(page_timeout=timeout_ms)
        result = await crawler.arun(url, config=config)
        if not result or not result.markdown:
            return ExtractedPage(url=url, domain=extract_domain(url), success=False, error="empty_result")
        text = str(result.markdown)
        return ExtractedPage(url=url, domain=extract_domain(url), text=text, success=True)
    except Exception as e:
        return ExtractedPage(url=url, domain=extract_domain(url), success=False, error=str(e))


async def _extract_all(urls: list[str], ttl_hours: int) -> list[ExtractedPage]:
    from crawl4ai import AsyncWebCrawler

    pages: list[ExtractedPage] = []
    to_fetch: list[str] = []

    for url in urls:
        cached_text = get_cached(url, ttl_hours)
        if cached_text is not None:
            pages.append(ExtractedPage(url=url, domain=extract_domain(url), text=cached_text, success=True))
        else:
            to_fetch.append(url)

    if to_fetch:
        async with AsyncWebCrawler() as crawler:
            fetched = await asyncio.gather(*[_extract_one(crawler, u) for u in to_fetch])
        for page in fetched:
            if page.success:
                set_cached(page.url, page.text)
            pages.append(page)

    return pages


def extract_pages(urls: list[str], ttl_hours: int = 24) -> list[ExtractedPage]:
    """Sync wrapper. Never raises: failed pages come back with success=False."""
    if not urls:
        return []
    return asyncio.run(_extract_all(urls, ttl_hours))
