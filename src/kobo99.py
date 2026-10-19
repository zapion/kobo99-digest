#!/usr/bin/env python3
"""Fetch the Kobo weekly NT$99 list from kobo99.com and build a daily digest.

kobo99.com renders its page client-side from ``data/latest.json``, so we read
that file directly instead of scraping HTML.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

SITE_URL = "https://kobo99.com"
LATEST_URL = f"{SITE_URL}/data/latest.json"
USER_AGENT = "Mozilla/5.0 (compatible; kobo99-digest/1.0)"
REQUEST_TIMEOUT_SECONDS = 30
TAIPEI = ZoneInfo("Asia/Taipei")

# Platform key in the upstream JSON -> display name, in display order.
PLATFORMS = (
    ("kobo", "Kobo"),
    ("books_com", "博客來"),
    ("readmoo", "讀墨"),
    ("goodreads", "Goodreads"),
    ("amazon_com", "Amazon"),
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = ROOT / "output"
RAW_NAME = "latest.json"


@dataclass(frozen=True)
class Rating:
    platform: str
    score: float
    count: int
    url: str


@dataclass(frozen=True)
class Book:
    title: str
    author: str
    sale_date: dt.date
    sale_price: str
    list_price: str
    avg_score: float | None
    description: str
    publisher: str
    kobo_url: str
    cover_url: str
    ratings: tuple[Rating, ...]


@dataclass(frozen=True)
class Digest:
    date: dt.date
    year: int
    week: int
    sale_label: str
    source_url: str
    today_books: tuple[Book, ...]
    upcoming_books: tuple[Book, ...]
    past_books: tuple[Book, ...]

    @property
    def is_empty(self) -> bool:
        return not (self.today_books or self.upcoming_books or self.past_books)


def fetch_latest(url: str = LATEST_URL) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Failed to fetch {url}: {error}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("books"), list):
        raise RuntimeError(f"Unexpected payload shape from {url}")
    return payload


def _parse_ratings(raw: object) -> tuple[Rating, ...]:
    if not isinstance(raw, dict):
        return ()
    ratings = []
    for key, name in PLATFORMS:
        entry = raw.get(key)
        if not isinstance(entry, dict) or not entry.get("score"):
            continue
        ratings.append(
            Rating(
                platform=name,
                score=float(entry["score"]),
                count=int(entry.get("count") or 0),
                url=str(entry.get("raw_url") or entry.get("url") or ""),
            )
        )
    return tuple(ratings)


def _absolute_cover(path: str) -> str:
    if not path or path.startswith("http"):
        return path
    return f"{SITE_URL}/{path.lstrip('/')}"


def parse_book(raw: dict, fallback_date: dt.date | None = None) -> Book | None:
    """Return None for entries that lack a title or a usable sale date."""
    title = str(raw.get("title") or "").strip()
    if not title:
        return None
    try:
        sale_date = dt.date.fromisoformat(str(raw.get("sale_start") or ""))
    except ValueError:
        if fallback_date is None:
            return None
        sale_date = fallback_date
    avg = raw.get("avg_score")
    return Book(
        title=title,
        author=str(raw.get("author") or "").strip(),
        sale_date=sale_date,
        sale_price=str(raw.get("sale_price") or "NT$99"),
        list_price=str(raw.get("kobo_price") or ""),
        avg_score=float(avg) if avg else None,
        description=str(raw.get("description") or "").strip(),
        publisher=str(raw.get("publisher") or "").strip(),
        kobo_url=str(raw.get("short_affiliate_url") or raw.get("kobo_url") or ""),
        cover_url=_absolute_cover(str(raw.get("cover_url") or "")),
        ratings=_parse_ratings(raw.get("ratings")),
    )


def build_digest(payload: dict, today: dt.date) -> Digest:
    books = [b for b in (parse_book(raw) for raw in payload["books"]) if b]
    ordered = sorted(books, key=lambda b: b.sale_date)
    return Digest(
        date=today,
        year=int(payload.get("year") or today.year),
        week=int(payload.get("week") or 0),
        sale_label=str(payload.get("sale_label") or ""),
        source_url=str(payload.get("kobo_url") or SITE_URL),
        today_books=tuple(b for b in ordered if b.sale_date == today),
        upcoming_books=tuple(b for b in ordered if b.sale_date > today),
        past_books=tuple(b for b in ordered if b.sale_date < today),
    )


# ── Markdown ──────────────────────────────────────────────────────────────────

def _rating_line(book: Book) -> str:
    return " · ".join(f"{r.platform} {r.score:.1f}({r.count})" for r in book.ratings)


def _book_markdown(book: Book, heading: str) -> list[str]:
    lines = [f"{heading} {book.title}", ""]
    meta = [f"作者：{book.author}"] if book.author else []
    if book.publisher:
        meta.append(f"出版社：{book.publisher}")
    lines.append(f"- {' ｜ '.join(meta)}" if meta else "- 作者：未知")
    lines.append(f"- 特價日：{book.sale_date.isoformat()} ｜ {book.sale_price}（原價 {book.list_price}）")
    if book.avg_score:
        lines.append(f"- 綜合評分：{book.avg_score:.2f}")
    if book.ratings:
        lines.append(f"- 各平台：{_rating_line(book)}")
    if book.kobo_url:
        lines.append(f"- Link: {book.kobo_url}")
    if book.description:
        lines += ["", book.description]
    lines.append("")
    return lines


def render_markdown(digest: Digest) -> str:
    lines = [f"# Kobo 每週 99 · {digest.date.isoformat()}", ""]
    lines.append(f"- 本週：{digest.year} 第 {digest.week} 週 ｜ {digest.sale_label}")
    lines += [f"- 來源：{digest.source_url}", "", "---", ""]
    sections = (
        ("## 🔥 今日 99", digest.today_books),
        ("## ⏳ 接下來", digest.upcoming_books),
        ("## ✅ 本週已過", digest.past_books),
    )
    for title, books in sections:
        if not books:
            continue
        lines += [title, ""]
        for book in books:
            lines += _book_markdown(book, "###")
    if digest.is_empty:
        lines.append("本週書單目前為空。")
    return "\n".join(lines).rstrip() + "\n"


def run(output_dir: Path, today: dt.date) -> Path:
    payload = fetch_latest()
    digest = build_digest(payload, today)
    if digest.is_empty:
        raise RuntimeError("Upstream returned no books; refusing to write an empty digest.")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / RAW_NAME).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = output_dir / f"{today.isoformat()}-kobo99.md"
    md_path.write_text(render_markdown(digest), encoding="utf-8")
    return md_path


def load_digest(output_dir: Path, today: dt.date) -> Digest | None:
    raw_path = output_dir / RAW_NAME
    if not raw_path.exists():
        return None
    return build_digest(json.loads(raw_path.read_text(encoding="utf-8")), today)


def taipei_today() -> dt.date:
    return dt.datetime.now(TAIPEI).date()


def main() -> int:
    try:
        path = run(DEFAULT_OUTPUT_DIR, taipei_today())
    except RuntimeError as error:
        print(f"[error] {error}", file=sys.stderr)
        return 1
    print(f"Wrote {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
