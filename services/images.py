"""Pictures for words.

Generated images were dropped: no free image model on OpenRouter is dependable,
and for vocabulary work a real photograph of the thing is better than a
plausible painting of it anyway. Instead we search two openly licensed
libraries and return attributed results.

Both sources are keyed on an English search phrase, which the word-explanation
step produces alongside the definitions.
"""

import threading
import time

import requests

import config

COMMONS = "https://commons.wikimedia.org/w/api.php"
OPENVERSE = "https://api.openverse.org/v1/images/"
UA = f"CineTot/1.0 ({config.SITE_URL})"

_cache = {}
_lock = threading.Lock()
CACHE_TTL = 60 * 60 * 12


def search(query, limit=4):
    """Return [{url, thumb, title, credit, source, page}] for an English query."""
    query = (query or "").strip()
    if not query:
        return []

    ck = (query.lower(), limit)
    with _lock:
        hit = _cache.get(ck)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]

    out = _commons(query, limit)
    if len(out) < 2:
        out += [i for i in _openverse(query, limit) if i["url"] not in {o["url"] for o in out}]

    out = out[:limit]
    with _lock:
        _cache[ck] = (time.time(), out)
    return out


def _commons(query, limit):
    try:
        r = requests.get(COMMONS, headers={"User-Agent": UA}, timeout=12, params={
            "action": "query", "format": "json", "formatversion": "2",
            "generator": "search", "gsrsearch": f"{query} filetype:bitmap",
            "gsrnamespace": "6", "gsrlimit": str(limit * 2),
            "prop": "imageinfo", "iiprop": "url|extmetadata",
            "iiurlwidth": "480", "iiextmetadatafilter": "Artist|LicenseShortName",
        })
        r.raise_for_status()
        pages = (r.json().get("query") or {}).get("pages") or []
    except Exception:
        return []

    out = []
    for p in pages:
        info = (p.get("imageinfo") or [{}])[0]
        thumb = info.get("thumburl") or info.get("url")
        if not thumb:
            continue
        meta = info.get("extmetadata") or {}
        artist = _strip(meta.get("Artist", {}).get("value", ""))
        lic = _strip(meta.get("LicenseShortName", {}).get("value", ""))
        out.append({
            "url": thumb,
            "thumb": thumb,
            "title": (p.get("title") or "").replace("File:", ""),
            "credit": " · ".join(x for x in (artist, lic) if x) or "Wikimedia Commons",
            "source": "Wikimedia Commons",
            "page": info.get("descriptionurl", ""),
        })
        if len(out) >= limit:
            break
    return out


def _openverse(query, limit):
    try:
        r = requests.get(OPENVERSE, headers={"User-Agent": UA}, timeout=12,
                         params={"q": query, "page_size": limit, "mature": "false"})
        r.raise_for_status()
        results = r.json().get("results", [])
    except Exception:
        return []

    return [{
        "url": it.get("thumbnail") or it.get("url"),
        "thumb": it.get("thumbnail") or it.get("url"),
        "title": it.get("title") or query,
        "credit": " · ".join(x for x in (it.get("creator"), it.get("license")) if x) or "Openverse",
        "source": "Openverse",
        "page": it.get("foreign_landing_url", ""),
    } for it in results if it.get("thumbnail") or it.get("url")][:limit]


import re as _re
_TAG = _re.compile(r"<[^>]+>")


def _strip(s):
    return _TAG.sub("", s or "").strip()
