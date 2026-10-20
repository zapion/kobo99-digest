#!/usr/bin/env python3
"""Fetch Kobo / 博客來 / 讀墨 / Goodreads / Amazon ratings for books in current lists and attach them to data/<slug>.json.

Per book and platform: URL from data/rating_overrides.json, else OpenAI web search proposes
candidates → we fetch each page politely → verify title/author ourselves → read structured
rating. Results are cached in data/ratings/<kobo_id>.json with a TTL per status.

Env: OPENAI_API_KEY (needed for discovery; without it only overrides/cached URLs are refreshed),
OPENAI_MODEL (optional), ENRICH_REFRESH=1 (ignore cache TTLs), ENRICH_MAX_BOOKS,
ENRICH_PLATFORMS (comma list; default all), ENRICH_TIME_BUDGET_MINUTES. Platforms still unrated fall back to kobo99.com's numbers.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import kobo99_fallback
from rating_discover import PLATFORMS, DiscoveryError, canonical_url, discover
from rating_extract import authors_match, extract_rating, page_identity, titles_match
from rating_fetch import Blocked, Disallowed, NotFound, PoliteFetcher
from store import ebook_id

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RATINGS_DIRNAME = "ratings"
OVERRIDES_NAME = "rating_overrides.json"
TAIPEI = ZoneInfo("Asia/Taipei")
# Goodreads first: it finds the original title/author that later platforms (Amazon) search with.
PLATFORM_ORDER = ("goodreads", "readmoo", "books", "amazon", "kobo")
DISPLAY_ORDER = ("kobo", "books", "readmoo", "goodreads", "amazon")
# Days before a cached result is retried, by status.
TTL_DAYS = {"ok": 30, "search_reported": 7, "no_rating": 14, "not_found": 14, "low_confidence": 14,
            "disallowed": 30, "blocked": 7}
# Accept a model-proposed page whose title the model reported faithfully, at this confidence or above.
MIN_MODEL_CONFIDENCE = 0.85
SHOWN_STATUSES = frozenset({"ok", "search_reported"})
ACTIVE_WINDOW_DAYS = 14
MAX_BOOKS_PER_RUN = 40
WORKERS = 6  # books looked up at once; each book's platforms stay sequential (hints flow between them)
TIME_BUDGET_MINUTES = 40  # stop starting books after this, so the workflow can still commit

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


def needs_update(entry: dict | None, platform: str, override: str | None, today: dt.date, refresh: bool = False) -> bool:
    if override and (not entry or entry.get("url") != canonical_url(PLATFORMS[platform], override)):
        return True
    return refresh or not is_fresh(entry, today)


def _verified(book: dict, platform: str, identity: dict, hints: dict, candidate: dict | None) -> bool:
    """Deterministic title/author check, or a confident model match whose title the page confirms."""
    if platform == "readmoo":
        # Same Chinese edition: the page must match on its own; no model judgement.
        return titles_match(book.get("title", ""), identity["title"]) and authors_match(book.get("author", ""), identity["authors"])
    title_ok = any(titles_match(t, identity["title"]) for t in (hints.get("original_title"), book.get("title")) if t)
    if title_ok and authors_match(hints.get("original_author") or book.get("author", ""), identity["authors"]):
        return True
    # Translations: trust a confident model match only if the page shows the title the model reported.
    return bool(candidate) and candidate["confidence"] >= MIN_MODEL_CONFIDENCE and titles_match(candidate["title"], identity["title"])


def _search_reported(candidates: list[dict], unreadable: set[str], base: dict) -> dict | None:
    """Fallback for pages we could not read: the rating search results showed for a confident match.

    Candidates whose page we did read (and which failed verification) are never used.
    """
    for c in candidates:
        if c["url"] not in unreadable:
            continue
        if c["confidence"] >= MIN_MODEL_CONFIDENCE and c.get("rating") is not None and c.get("rating_count"):
            return {**base, "status": "search_reported", "url": c["url"], "matched_title": c["title"],
                    "score": c["rating"], "count": c["rating_count"]}
    return None


def _own_page_entry(book: dict, candidates: list[dict], base: dict) -> dict:
    """Unfetchable platform whose page we already know (Kobo): only a rating reported for that exact page counts."""
    own_id = ebook_id(book.get("kobo_url", ""))
    own = [{**c, "confidence": 1.0} for c in candidates if own_id and ebook_id(c["url"]) == own_id]
    if not own:
        return {**base, "status": "not_found"}
    return _search_reported(own, {c["url"] for c in own}, base) or {**base, "status": "no_rating", "url": own[0]["url"]}


def _title_checked_entry(book: dict, candidates: list[dict], hints: dict, base: dict) -> dict:
    """Unfetchable platform found by search: the reported title/author must match ours (or the original's)."""
    titles = [t for t in (book.get("title"), hints.get("original_title")) if t]
    authors = [a for a in (book.get("author"), hints.get("original_author")) if a]
    matching = [c for c in candidates
                if any(titles_match(t, c["title"]) for t in titles)
                and (not c["author"] or any(authors_match(a, [c["author"]]) for a in authors))]
    if not matching:
        tried = [{"url": c["url"], "title": c["title"], "authors": [c["author"]], "confidence": c["confidence"]}
                 for c in candidates]
        return {**base, "status": "low_confidence", "tried": tried} if candidates else {**base, "status": "not_found"}
    return (_search_reported(matching, {c["url"] for c in matching}, base)
            or {**base, "status": "no_rating", "url": matching[0]["url"]})


def _candidates_for(book: dict, platform: str, find: Discover | None, override: str | None,
                    hints: dict) -> tuple[list[dict] | None, dict]:
    if override:
        url = canonical_url(PLATFORMS[platform], override)
        return ([{"url": url, "title": "", "author": "", "confidence": 1.0, "rating": None, "rating_count": None}]
                if url else []), hints
    if find is None:
        return None, hints
    try:
        found = find(book, platform)
    except DiscoveryError as error:
        print(f"[warn] discovery {platform} for {book.get('title')}: {error}", file=sys.stderr)
        return None, hints
    new_hints = {k: found.get(k) for k in ("original_title", "original_author") if found.get(k)}
    return found["candidates"], {**hints, **new_hints}


def rate_platform(book: dict, platform: str, fetcher: PoliteFetcher, find: Discover | None,
                  override: str | None, hints: dict, now: str) -> tuple[dict | None, dict]:
    """Return (cache entry or None when discovery is unavailable, updated hints)."""
    candidates, hints = _candidates_for(book, platform, find, override, hints)
    if candidates is None:
        return None, hints
    base = {"platform": platform, "fetched_at": now}
    if not PLATFORMS[platform].fetchable:
        if platform == "kobo":
            return _own_page_entry(book, candidates, base), hints
        return _title_checked_entry(book, candidates, hints, base), hints
    if not candidates:
        return {**base, "status": "not_found"}, hints
    tried, problem = [], None
    unreadable = {c["url"] for c in candidates}  # shrinks as pages are actually read
    for candidate in candidates:
        url = candidate["url"]
        try:
            page = fetcher.get(url)
        except NotFound:
            unreadable.discard(url)
            continue
        except Disallowed:
            problem = problem or {"status": "disallowed", "url": url}
            continue
        except Blocked as error:
            problem = {"status": "blocked", "url": url, "detail": str(error)}
            break  # the host refuses us for the rest of this run
        unreadable.discard(url)
        identity = page_identity(page)
        if not override and not _verified(book, platform, identity, hints, candidate):
            tried.append({"url": url, "title": identity["title"], "authors": identity["authors"],
                          "confidence": candidate["confidence"]})
            continue
        rating = extract_rating(page)
        if not rating:
            return {**base, "status": "no_rating", "url": url}, hints
        return {**base, "status": "ok", "url": url, "matched_title": identity["title"], **rating}, hints
    fallback = None if override else _search_reported(candidates, unreadable, base)
    if fallback:
        return fallback, hints
    if problem:
        return {**base, **problem}, hints
    return {**base, "status": "low_confidence", "tried": tried}, hints


def platforms_from_env(value: str) -> tuple[str, ...]:
    """ENRICH_PLATFORMS="a,b" → known platforms in that order; empty → all."""
    names = [name.strip() for name in value.split(",") if name.strip()]
    return tuple(name for name in names if name in PLATFORMS) if names else PLATFORM_ORDER


def enrich_book(book: dict, cache: dict, fetcher: PoliteFetcher, find: Discover | None,
                overrides: dict, today: dt.date, now: str, refresh: bool = False,
                only: tuple[str, ...] = PLATFORM_ORDER) -> dict:
    platforms = dict(cache.get("platforms", {}))
    hints = dict(cache.get("hints", {}))
    for platform in only:
        override = overrides.get(platform)
        if not needs_update(platforms.get(platform), platform, override, today, refresh):
            continue
        entry, hints = rate_platform(book, platform, fetcher, find, override, hints, now)
        if entry:
            platforms[platform] = entry
            print(f"{book.get('title', '')[:24]} | {platform}: {entry['status']} {entry.get('score', '')} {entry.get('url', '')}")
    return {"kobo_id": ebook_id(book["kobo_url"]), "title": book.get("title", ""), "hints": hints, "platforms": platforms}


def _public(key: str, entry: dict, source: str) -> dict:
    return {"platform": key, "name": PLATFORMS[key].name.split(" ")[0], "score": entry["score"],
            "count": entry["count"], "url": entry["url"], "source": source}


def public_ratings(cache: dict) -> list[dict]:
    """What the page shows per platform: read from the page ("page"), from search results ("search"),
    or failing both, kobo99.com's number ("kobo99")."""
    fallback = cache.get("kobo99", {}).get("ratings", {})
    shown = []
    for key in DISPLAY_ORDER:
        entry = cache.get("platforms", {}).get(key)
        if entry and entry.get("status") in SHOWN_STATUSES:
            shown.append(_public(key, entry, "page" if entry["status"] == "ok" else "search"))
        elif key in fallback:
            shown.append(_public(key, fallback[key], "kobo99"))
    return shown


def with_fallback(cache: dict, ratings: dict | None, now: str) -> dict:
    """Keep the latest kobo99 numbers in the cache, so they survive weeks kobo99 is unreachable."""
    if not ratings or cache.get("kobo99", {}).get("ratings") == ratings:
        return cache
    return {**cache, "kobo99": {"fetched_at": now, "ratings": ratings}}


def _stale_books(lists: list[dict], caches: dict, overrides: dict, today: dt.date, refresh: bool,
                 platforms: tuple[str, ...]) -> list[dict]:
    """Unique books (by Kobo id) with at least one platform due for an update."""
    due: dict[str, dict] = {}
    for book_list in lists:
        for book in book_list["books"]:
            kobo_id = ebook_id(book.get("kobo_url", ""))
            if not kobo_id or kobo_id in due:
                continue
            entries, book_overrides = caches[kobo_id].get("platforms", {}), overrides.get(kobo_id, {})
            if any(needs_update(entries.get(p), p, book_overrides.get(p), today, refresh) for p in platforms):
                due[kobo_id] = book
    return list(due.values())


def _enrich_all(books: list[dict], caches: dict, enrich_one: Callable[[dict, dict], dict],
                deadline: float | None, workers: int) -> dict[str, dict]:
    """Enrich books in parallel (OpenAI search is the slow part); books not started by the deadline are skipped."""
    def task(book: dict) -> dict | None:
        if deadline is not None and time.monotonic() > deadline:
            return None
        return enrich_one(book, caches[ebook_id(book["kobo_url"])])

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(task, books))
    skipped = sum(r is None for r in results)
    if skipped:
        print(f"[warn] time budget reached: {skipped} books left for the next run", file=sys.stderr)
    return {r["kobo_id"]: r for r in results if r is not None}


def run(data_dir: Path, today: dt.date, fetcher: PoliteFetcher, find: Discover | None, max_books: int,
        refresh: bool = False, platforms: tuple[str, ...] = PLATFORM_ORDER, fallback: dict | None = None,
        deadline: float | None = None, workers: int = WORKERS) -> dict:
    """Update the given platforms; ratings of every platform already cached are still published."""
    ratings_dir = data_dir / RATINGS_DIRNAME
    overrides = _read_json(data_dir / OVERRIDES_NAME, {})
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    lists = [(path, data) for path in active_lists(data_dir, today) if (data := _read_json(path, None))]
    ids = {ebook_id(b.get("kobo_url", "")) for _path, data in lists for b in data["books"]} - {""}
    caches = {kobo_id: _read_json(ratings_dir / f"{kobo_id}.json", {}) for kobo_id in ids}

    due = _stale_books([data for _path, data in lists], caches, overrides, today, refresh, platforms)[:max_books]
    enrich_one = lambda book, cache: enrich_book(book, cache, fetcher, find, overrides.get(ebook_id(book["kobo_url"]), {}),
                                                 today, now, refresh, platforms)
    stats = {"enriched": 0, "cached": len(ids) - len(due), "lists_updated": 0}
    for kobo_id, updated in _enrich_all(due, caches, enrich_one, deadline, workers).items():
        old = caches[kobo_id]
        if updated["platforms"] != old.get("platforms", {}) or updated["hints"] != old.get("hints", {}):
            caches[kobo_id] = {**old, **updated}
            _write_json(ratings_dir / f"{kobo_id}.json", caches[kobo_id])
            stats["enriched"] += 1

    titles = {ebook_id(b.get("kobo_url", "")): b.get("title", "") for _path, data in lists for b in data["books"]}
    for kobo_id in ids:
        merged = with_fallback(caches[kobo_id], (fallback or {}).get(kobo_id), now)
        if merged is not caches[kobo_id]:
            caches[kobo_id] = {"kobo_id": kobo_id, "title": titles[kobo_id], **merged}
            _write_json(ratings_dir / f"{kobo_id}.json", caches[kobo_id])

    for list_path, book_list in lists:
        books = [{**b, "ratings": public_ratings(caches.get(ebook_id(b.get("kobo_url", "")), {}))}
                 for b in book_list["books"]]
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
    refresh = os.getenv("ENRICH_REFRESH", "").lower() in ("1", "true", "yes")
    fetcher = PoliteFetcher()
    fallback = kobo99_fallback.load(fetcher)
    print(f"kobo99 fallback: {len(fallback)} books")
    deadline = time.monotonic() + float(os.getenv("ENRICH_TIME_BUDGET_MINUTES", TIME_BUDGET_MINUTES)) * 60
    stats = run(DATA_DIR, today, fetcher, find, int(os.getenv("ENRICH_MAX_BOOKS", MAX_BOOKS_PER_RUN)), refresh,
                platforms_from_env(os.getenv("ENRICH_PLATFORMS", "")), fallback, deadline)
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
