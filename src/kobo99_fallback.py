"""Fallback ratings from kobo99.com's public data (robots.txt: Allow /), for platforms we could not rate.

kobo99.com renders in the browser from data/latest.json (一週99) and data/theme_*.json (本週精選),
so search engines see no scores there; we read those JSON files politely instead (3 requests a run).
Only score and count are kept. Its links carry tracking/affiliate parameters, so URLs are rebuilt
from our own allowlists, or point to kobo99.com when they cannot be.
"""

from __future__ import annotations

import ast
import json
import re
import sys

from rating_discover import PLATFORMS, canonical_url
from rating_fetch import Blocked, Disallowed, NotFound, PoliteFetcher
from store import ebook_id

BASE_URL = "https://kobo99.com/data/"
SITE_URL = "https://kobo99.com/"
THEME_FILE_RE = re.compile(r"^theme_[\w-]+\.json$")
# kobo99 key → our platform key
PLATFORM_KEYS = {"kobo": "kobo", "books_com": "books", "readmoo": "readmoo", "goodreads": "goodreads",
                 "amazon_com": "amazon"}
MAX_SCORE = 5


def _get_json(fetcher: PoliteFetcher, name: str):
    try:
        return json.loads(fetcher.get(BASE_URL + name))
    except (Blocked, Disallowed, NotFound, ValueError) as error:
        print(f"[warn] kobo99 {name}: {error}", file=sys.stderr)
        return None


def _parse_ratings(raw) -> dict:
    if isinstance(raw, str):  # some theme files store a Python-literal string
        try:
            raw = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return {}
    return raw if isinstance(raw, dict) else {}


def _rating(platform: str, raw: object, kobo_url: str) -> dict | None:
    if not isinstance(raw, dict):
        return None
    score, count = raw.get("score"), raw.get("count")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 < score <= MAX_SCORE:
        return None
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        return None
    url = kobo_url if platform == "kobo" else canonical_url(PLATFORMS[platform], str(raw.get("url") or ""))
    return {"score": round(float(score), 2), "count": count, "url": url or SITE_URL}


def ratings_by_book(books: list) -> dict[str, dict]:
    """{kobo_id: {platform: {score, count, url}}} from kobo99 book records."""
    result: dict[str, dict] = {}
    for book in books if isinstance(books, list) else []:
        if not isinstance(book, dict):
            continue
        kobo_id = ebook_id(str(book.get("kobo_url") or ""))
        if not kobo_id:
            continue
        kobo_url = f"https://www.kobo.com/tw/zh/ebook/{kobo_id}"
        ratings = {}
        for key, raw in _parse_ratings(book.get("ratings")).items():
            platform = PLATFORM_KEYS.get(key)
            rating = _rating(platform, raw, kobo_url) if platform else None
            if rating:
                ratings[platform] = rating
        if ratings:
            result[kobo_id] = ratings
    return result


def load(fetcher: PoliteFetcher) -> dict[str, dict]:
    """Ratings for kobo99's current 一週99 and 本週精選 lists; {} if unavailable."""
    books: list = []
    latest = _get_json(fetcher, "latest.json")
    if isinstance(latest, dict):
        books += latest.get("books") or []
    themes = _get_json(fetcher, "themes.json")
    for theme in themes if isinstance(themes, list) else []:
        if not isinstance(theme, dict):
            continue
        file = str(theme.get("file", ""))
        if theme.get("promo_code") and not theme.get("hidden") and THEME_FILE_RE.match(file):
            data = _get_json(fetcher, file)
            if isinstance(data, dict):
                books += data.get("books") or []
    return ratings_by_book(books)
