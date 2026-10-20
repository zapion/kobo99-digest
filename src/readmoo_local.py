#!/usr/bin/env python3
"""Rate books on 讀墨 Readmoo from your own machine (Readmoo answers 403 to GitHub Actions).

Readmoo's robots.txt disallows its own search (/search/, incl. the suggest API) and AI crawlers,
so book pages are found, in order, from: data/rating_overrides.json, a URL already cached for the
book, then Brave Search (BRAVE_API_KEY), whose index follows Readmoo's rules for /book/ pages.
Pages are then fetched politely (robots.txt, Crawl-delay 10 s) and verified strictly by enrich.py.

Env: BRAVE_API_KEY (optional), ENRICH_MAX_BOOKS. Usage: scripts/readmoo_local.sh
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

import enrich
from rating_discover import PLATFORMS, canonical_url
from rating_fetch import USER_AGENT, PoliteFetcher
from store import ebook_id

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
BRAVE_RESULTS = 10
BRAVE_DELAY_SECONDS = 1.1  # free plan: 1 request per second
MAX_CANDIDATES = 3
TIMEOUT_SECONDS = 30
PLATFORM = "readmoo"

GetJson = Callable[[str, dict], dict]
Search = Callable[[dict], list[str]]


def _get_json(url: str, headers: dict) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **headers})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode())


def _query(book: dict) -> str:
    title = re.split(r"[：:（(【]", book.get("title", ""), maxsplit=1)[0].strip()
    author = re.split(r"[（(／/,，、]", book.get("author", ""), maxsplit=1)[0].strip()
    return f"site:readmoo.com/book {title} {author}".strip()


def brave_search(book: dict, api_key: str, get_json: GetJson = _get_json) -> list[str]:
    """Readmoo book URLs Brave returns for the book's short title and author, best first."""
    url = f"{BRAVE_URL}?{urllib.parse.urlencode({'q': _query(book), 'count': BRAVE_RESULTS})}"
    payload = get_json(url, {"X-Subscription-Token": api_key})
    found: list[str] = []
    for result in payload.get("web", {}).get("results", []):
        canonical = canonical_url(PLATFORMS[PLATFORM], str(result.get("url", "")))
        if canonical and canonical not in found:
            found.append(canonical)
    return found[:MAX_CANDIDATES]


def _candidate(url: str) -> dict:
    return {"url": url, "title": "", "author": "", "confidence": 0.0, "rating": None, "rating_count": None}


def make_find(data_dir: Path, search: Search | None) -> enrich.Discover:
    """Discovery for enrich.run: a URL cached for this book first, else search (if configured)."""
    def find(book: dict, platform: str) -> dict:
        cache_path = data_dir / enrich.RATINGS_DIRNAME / f"{ebook_id(book.get('kobo_url', ''))}.json"
        cached = enrich._read_json(cache_path, {}).get("platforms", {}).get(platform, {})
        urls = [cached["url"]] if cached.get("url") else []
        if not urls and search is not None:
            try:
                urls = search(book)
            except (urllib.error.URLError, TimeoutError, ValueError) as error:
                print(f"[warn] search failed for {book.get('title')}: {error}", file=sys.stderr)
        return {"candidates": [_candidate(u) for u in urls], "original_title": "", "original_author": ""}
    return find


def run(data_dir: Path, today: dt.date, fetcher: PoliteFetcher, find: enrich.Discover,
        max_books: int = enrich.MAX_BOOKS_PER_RUN) -> dict:
    # refresh: a local run is the only place Readmoo is rated, so retry every book each time.
    return enrich.run(data_dir, today, fetcher, find, max_books, refresh=True, platforms=(PLATFORM,))


def _throttled(search: Search, delay: float) -> Search:
    last = [0.0]

    def wrapped(book: dict) -> list[str]:
        wait = delay - (time.monotonic() - last[0])
        if wait > 0:
            time.sleep(wait)
        last[0] = time.monotonic()
        return search(book)
    return wrapped


def main() -> int:
    api_key = os.getenv("BRAVE_API_KEY", "")
    search = _throttled(lambda book: brave_search(book, api_key), BRAVE_DELAY_SECONDS) if api_key else None
    if search is None:
        print("[warn] BRAVE_API_KEY not set: only overrides and cached URLs are tried.", file=sys.stderr)
    today = dt.datetime.now(enrich.TAIPEI).date()
    max_books = int(os.getenv("ENRICH_MAX_BOOKS", enrich.MAX_BOOKS_PER_RUN))
    print(json.dumps(run(enrich.DATA_DIR, today, PoliteFetcher(), make_find(enrich.DATA_DIR, search), max_books)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
