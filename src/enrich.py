#!/usr/bin/env python3
"""Fetch 讀墨 / Goodreads ratings for books in current lists and attach them to data/<slug>.json.

Per book and platform: URL from data/rating_overrides.json, else OpenAI web search proposes
candidates → we fetch each page politely → verify title/author ourselves → read structured
rating. Results are cached in data/ratings/<kobo_id>.json with a TTL per status.

Env: OPENAI_API_KEY (needed for discovery; without it only overrides/cached URLs are refreshed),
OPENAI_MODEL (optional).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from rating_discover import PLATFORMS, DiscoveryError, canonical_url, discover
from rating_extract import authors_match, extract_rating, page_identity, titles_match
from rating_fetch import Blocked, Disallowed, NotFound, PoliteFetcher
from store import ebook_id

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RATINGS_DIRNAME = "ratings"
OVERRIDES_NAME = "rating_overrides.json"
TAIPEI = ZoneInfo("Asia/Taipei")
PLATFORM_ORDER = ("readmoo", "goodreads")
# Days before a cached result is retried, by status.
TTL_DAYS = {"ok": 30, "no_rating": 14, "not_found": 14, "low_confidence": 14, "disallowed": 30, "blocked": 7}
ACTIVE_WINDOW_DAYS = 14
MAX_BOOKS_PER_RUN = 40

Discover = Callable[[dict, str], dict]


def _read_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def active_lists(data_dir: Path, today: dt.date) -> list[Path]:
    manifest = _read_json(data_dir / "manifest.json", {"lists": []})
    cutoff = (today - dt.timedelta(days=ACTIVE_WINDOW_DAYS)).isoformat()
    return [data_dir / f"{e['slug']}.json" for e in manifest.get("lists", []) if e.get("period_end", "") >= cutoff]


def is_fresh(entry: dict | None, today: dt.date) -> bool:
    if not entry or entry.get("status") not in TTL_DAYS:
        return False
    fetched = dt.date.fromisoformat(entry.get("fetched_at", "1970-01-01")[:10])
    return (today - fetched).days < TTL_DAYS[entry["status"]]


def needs_update(entry: dict | None, platform: str, override: str | None, today: dt.date) -> bool:
    if override and (not entry or entry.get("url") != canonical_url(PLATFORMS[platform], override)):
        return True
    return not is_fresh(entry, today)


def _verified(book: dict, platform: str, identity: dict, hints: dict) -> bool:
    if platform == "readmoo":
        return titles_match(book.get("title", ""), identity["title"]) and authors_match(book.get("author", ""), identity["authors"])
    title_ok = any(titles_match(t, identity["title"]) for t in (hints.get("original_title"), book.get("title")) if t)
    author = hints.get("original_author") or book.get("author", "")
    return title_ok and authors_match(author, identity["authors"])


def rate_platform(book: dict, platform: str, fetcher: PoliteFetcher, find: Discover | None,
                  override: str | None, hints: dict, now: str) -> tuple[dict | None, dict]:
    """Return (cache entry or None when discovery is unavailable, updated hints)."""
    if override:
        urls = [u for u in [canonical_url(PLATFORMS[platform], override)] if u]
    elif find is None:
        return None, hints
    else:
        try:
            found = find(book, platform)
        except DiscoveryError as error:
            print(f"[warn] discovery {platform} for {book.get('title')}: {error}", file=sys.stderr)
            return None, hints
        hints = {**hints, **{k: v for k, v in found.items() if k != "urls" and v and not hints.get(k)}}
        urls = found["urls"]
    base = {"platform": platform, "fetched_at": now}
    if not urls:
        return {**base, "status": "not_found"}, hints
    tried = []
    for url in urls:
        try:
            page = fetcher.get(url)
        except NotFound:
            continue
        except Disallowed:
            return {**base, "status": "disallowed", "url": url}, hints
        except Blocked as error:
            return {**base, "status": "blocked", "url": url, "detail": str(error)}, hints
        identity = page_identity(page)
        if not override and not _verified(book, platform, identity, hints):
            tried.append({"url": url, "title": identity["title"], "authors": identity["authors"]})
            continue
        rating = extract_rating(page)
        if not rating:
            return {**base, "status": "no_rating", "url": url}, hints
        return {**base, "status": "ok", "url": url, "matched_title": identity["title"], **rating}, hints
    return {**base, "status": "low_confidence", "tried": tried}, hints


def enrich_book(book: dict, cache: dict, fetcher: PoliteFetcher, find: Discover | None,
                overrides: dict, today: dt.date, now: str) -> dict:
    platforms = dict(cache.get("platforms", {}))
    hints = dict(cache.get("hints", {}))
    for platform in PLATFORM_ORDER:
        override = overrides.get(platform)
        if not needs_update(platforms.get(platform), platform, override, today):
            continue
        entry, hints = rate_platform(book, platform, fetcher, find, override, hints, now)
        if entry:
            platforms[platform] = entry
    return {"kobo_id": ebook_id(book["kobo_url"]), "title": book.get("title", ""), "hints": hints, "platforms": platforms}


def public_ratings(cache: dict) -> list[dict]:
    """What the page shows: verified ratings only."""
    return [
        {"platform": key, "name": PLATFORMS[key].name.split(" ")[0], "score": e["score"], "count": e["count"], "url": e["url"]}
        for key in PLATFORM_ORDER
        if (e := cache.get("platforms", {}).get(key)) and e.get("status") == "ok"
    ]


def run(data_dir: Path, today: dt.date, fetcher: PoliteFetcher, find: Discover | None, max_books: int) -> dict:
    ratings_dir = data_dir / RATINGS_DIRNAME
    overrides = _read_json(data_dir / OVERRIDES_NAME, {})
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    budget, stats = max_books, {"enriched": 0, "cached": 0, "lists_updated": 0}
    for list_path in active_lists(data_dir, today):
        book_list = _read_json(list_path, None)
        if not book_list:
            continue
        books = []
        for book in book_list["books"]:
            kobo_id = ebook_id(book.get("kobo_url", ""))
            cache_path = ratings_dir / f"{kobo_id}.json"
            cache = _read_json(cache_path, {})
            book_overrides = overrides.get(kobo_id, {})
            stale = any(needs_update(cache.get("platforms", {}).get(p), p, book_overrides.get(p), today) for p in PLATFORM_ORDER)
            if kobo_id and stale and budget > 0:
                updated = enrich_book(book, cache, fetcher, find, book_overrides, today, now)
                if updated["platforms"] != cache.get("platforms", {}) or updated["hints"] != cache.get("hints", {}):
                    _write_json(cache_path, updated)
                    cache = updated
                    budget -= 1
                    stats["enriched"] += 1
            else:
                stats["cached"] += 1
            books.append({**book, "ratings": public_ratings(cache)})
        if books != book_list["books"]:
            _write_json(list_path, {**book_list, "books": books})
            stats["lists_updated"] += 1
    return stats


def main() -> int:
    api_key = os.getenv("OPENAI_API_KEY", "")
    find = (lambda book, platform: discover(book, platform, api_key)) if api_key else None
    if find is None:
        print("[warn] OPENAI_API_KEY not set: only overrides are refreshed.", file=sys.stderr)
    today = dt.datetime.now(TAIPEI).date()
    stats = run(DATA_DIR, today, PoliteFetcher(), find, int(os.getenv("ENRICH_MAX_BOOKS", MAX_BOOKS_PER_RUN)))
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
