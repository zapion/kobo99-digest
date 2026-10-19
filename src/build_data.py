#!/usr/bin/env python3
"""Merge lists dropped in data/inbox/ (by the Apps Script) into data/<slug>.json + manifest.

Each valid inbox file is merged with store.write_list and then deleted. Invalid files are left in
place and reported, so a bad email parse never overwrites good data. Prints merged slugs.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from store import write_list

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
INBOX_DIR = DATA_DIR / "inbox"
SLUG_RE = re.compile(r"^weekly-(dd99|8585)-\d{4}-w\d{1,2}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
KOBO_EBOOK_PREFIX = "https://www.kobo.com/tw/zh/ebook/"


class InboxError(ValueError):
    """An inbox file does not look like a parsed Kobo list."""


def validate(book_list: object) -> dict:
    if not isinstance(book_list, dict):
        raise InboxError("not a JSON object")
    slug = str(book_list.get("slug", ""))
    match = SLUG_RE.match(slug)
    if not match or book_list.get("kind") != match[1]:
        raise InboxError(f"bad slug/kind: {slug!r}")
    if not all(DATE_RE.match(str(book_list.get(k, ""))) for k in ("period_start", "period_end")):
        raise InboxError("bad period dates")
    books = book_list.get("books")
    if not isinstance(books, list) or not books:
        raise InboxError("no books")
    for book in books:
        if not isinstance(book, dict) or not book.get("title") or not str(book.get("kobo_url", "")).startswith(KOBO_EBOOK_PREFIX):
            raise InboxError(f"bad book entry: {book!r}"[:200])
    return book_list


def process_inbox(inbox_dir: Path, data_dir: Path) -> tuple[list[str], list[str]]:
    """Return (merged slugs, error messages)."""
    merged, errors = [], []
    for path in sorted(inbox_dir.glob("*.json")):
        try:
            book_list = validate(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, InboxError) as error:
            errors.append(f"{path.name}: {error}")
            continue
        write_list(book_list, data_dir)
        path.unlink()
        merged.append(book_list["slug"])
    return merged, errors


def main() -> int:
    merged, errors = process_inbox(INBOX_DIR, DATA_DIR)
    for slug in merged:
        print(slug)
    for error in errors:
        print(f"[error] inbox {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
