#!/usr/bin/env python3
"""Turn bookmarklet downloads (weekly-*.json) into data/<slug>.json + data/manifest.json.

Usage: python3 src/ingest.py [FILE ...]   (default: ~/Downloads/weekly-{dd99,8585}-*.json)
Each ingested file is moved to data/raw/ so it can be re-parsed later; prints the slugs.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import sys
from pathlib import Path

from store import update_manifest, write_list  # noqa: F401  (update_manifest re-exported for callers)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DOWNLOADS_GLOB = "weekly-*-*-w*.json"

SLUG_RE = re.compile(r"/blog/(weekly-(dd99|8585)-(\d{4})-w(\d{1,2}))/?$")
PERIOD_RE = re.compile(r"[（(]\s*(\d{1,2})/(\d{1,2})\s*[-－~～]\s*(\d{1,2})/(\d{1,2})\s*[)）]")
SALE_DAY_RE = re.compile(r"^(\d{1,2})/(\d{1,2})")
RANK_RE = re.compile(r"^#(\d+)")
PROMO_RE = re.compile(r"折扣代碼[:：]\s*([A-Za-z0-9]+)")
AUTHOR_PREFIX_RE = re.compile(r"^由\s*")
AUTHOR_MAIN_ROLE_RE = re.compile(r"\s*[@＠]著")
AUTHOR_OTHER_ROLE_RE = re.compile(r"\s*[@＠](\S+?)(?=[、,，]|$)")
KOBO_HOST_PREFIX = "https://www.kobo.com/"
KIND_LABELS = {"dd99": "一週99書單", "8585": "本週精選書單"}
BLOG_URL_PREFIX = "https://www.kobo.com/zh/blog/"


class IngestError(ValueError):
    """Input from the bookmarklet is not a usable book list."""


def _parse_slug(url: str) -> tuple[str, str, int, int]:
    match = SLUG_RE.search(url)
    if not match or not url.startswith(KOBO_HOST_PREFIX):
        raise IngestError(f"Not a Kobo weekly list post: {url!r}")
    slug, kind, year, week = match.groups()
    return slug, kind, int(year), int(week)


def _parse_period(title: str, iso_year: int, iso_week: int) -> tuple[dt.date, dt.date]:
    match = PERIOD_RE.search(title)
    if not match:
        raise IngestError(f"No (M/D-M/D) period in title: {title!r}")
    m1, d1, m2, d2 = (int(x) for x in match.groups())
    # The slug carries the ISO-week year; a week-1 period can start in late December.
    start_year = iso_year - 1 if m1 == 12 and iso_week <= 1 else iso_year
    start = dt.date(start_year, m1, d1)
    end_year = start_year if (m2, d2) >= (m1, d1) else start_year + 1
    return start, dt.date(end_year, m2, d2)


def _date_in_period(month: int, day: int, start: dt.date, end: dt.date) -> dt.date:
    for year in {start.year, end.year}:
        candidate = dt.date(year, month, day)
        if start <= candidate <= end:
            return candidate
    raise IngestError(f"Sale date {month}/{day} is outside {start}..{end}")


def _clean_author(raw: str) -> str:
    """'由 甲@著、乙＠改寫' -> '甲、乙（改寫）'."""
    name = AUTHOR_MAIN_ROLE_RE.sub("", AUTHOR_PREFIX_RE.sub("", raw.strip()))
    return AUTHOR_OTHER_ROLE_RE.sub(r"（\1）", name).strip()


def _clean_title(raw: str) -> str:
    """Take the text inside the outer 《…》, which may itself contain 《…》."""
    text = raw.strip()
    if not text.startswith("《"):
        return text
    depth = 0
    for index, char in enumerate(text):
        depth += {"《": 1, "》": -1}.get(char, 0)
        if depth == 0:
            return text[1:index].strip()
    return text.lstrip("《").strip()


def _https_or_empty(url: object) -> str:
    value = str(url or "")
    return value if value.startswith("https://") else ""


def _parse_book(item: dict, kind: str, start: dt.date, end: dt.date) -> dict:
    title = _clean_title(str(item.get("title") or ""))
    kobo_url = str(item.get("kobo_url") or "")
    if not title or not kobo_url.startswith(KOBO_HOST_PREFIX):
        raise IngestError(f"Book entry missing title or Kobo link: {item!r}")
    heading = str(item.get("heading") or "")
    book = {
        "title": title,
        "author": _clean_author(str(item.get("author") or "")),
        "publisher": str(item.get("publisher") or "").strip(),
        "description": str(item.get("description") or "").strip(),
        "kobo_url": kobo_url,
        "cover_url": _https_or_empty(item.get("cover_url")),
    }
    if kind == "dd99":
        day = SALE_DAY_RE.match(heading)
        if not day:
            raise IngestError(f"No sale date in heading: {heading!r}")
        return {**book, "sale_date": _date_in_period(int(day[1]), int(day[2]), start, end).isoformat()}
    rank = RANK_RE.match(heading)
    return {**book, "rank": int(rank[1]) if rank else None}


def parse_payload(payload: object) -> dict:
    if not isinstance(payload, dict) or payload.get("source") != "kobo-blog-bookmarklet":
        raise IngestError("Clipboard does not contain bookmarklet output.")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise IngestError("Bookmarklet output has no books.")
    url = str(payload.get("url") or "")
    slug, kind, iso_year, iso_week = _parse_slug(url)
    title = str(payload.get("title") or "").strip()
    start, end = _parse_period(title, iso_year, iso_week)
    intro = str(payload.get("intro") or "")
    promo = PROMO_RE.search(intro)
    return {
        "slug": slug,
        "kind": kind,
        "label": KIND_LABELS[kind],
        "url": url,
        "title": title,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "promo_code": promo[1] if promo else None,
        "books": [_parse_book(item, kind, start, end) for item in items if isinstance(item, dict)],
    }


def ingest_file(path: Path, data_dir: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise IngestError(f"{path.name} is not valid JSON: {error}") from error
    book_list = parse_payload(payload)
    write_list(book_list, data_dir)
    raw_dir = data_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), raw_dir / f"{book_list['slug']}.json")
    return book_list


def default_inputs() -> list[Path]:
    return sorted((Path.home() / "Downloads").glob(DOWNLOADS_GLOB))


def main(argv: list[str]) -> int:
    paths = [Path(arg) for arg in argv] or default_inputs()
    if not paths:
        print("[error] No weekly-*.json found in ~/Downloads. Click the bookmarklet on the Kobo post first.",
              file=sys.stderr)
        return 1
    failed = False
    for path in paths:
        try:
            book_list = ingest_file(path, DATA_DIR)
        except (OSError, IngestError) as error:
            print(f"[error] {path}: {error}", file=sys.stderr)
            failed = True
            continue
        print(f"Ingested {book_list['slug']} ({len(book_list['books'])} books)", file=sys.stderr)
        print(book_list["slug"])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
