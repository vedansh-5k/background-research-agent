import hashlib
import json
import time
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def _key(url: str) -> Path:
    digest = hashlib.md5(url.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}.json"


def get_cached(url: str, ttl_hours: int) -> str | None:
    path = _key(url)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    age_hours = (time.time() - data.get("cached_at", 0)) / 3600
    if age_hours > ttl_hours:
        return None
    return data.get("text")


def set_cached(url: str, text: str) -> None:
    path = _key(url)
    try:
        path.write_text(
            json.dumps({"url": url, "text": text, "cached_at": time.time()}),
            encoding="utf-8",
        )
    except Exception:
        pass
