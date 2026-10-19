"""Persist book lists as data/<slug>.json + data/manifest.json, merging repeated ingests.

Lists can arrive from two sources for the same week: the Kobo newsletter email (prices, sale
dates) and the blog bookmarklet (full titles, author, publisher). Books are matched by Kobo
product id so neither source erases what the other contributed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

MANIFEST_NAME = "manifest.json"
MANIFEST_FIELDS = ("slug", "kind", "label", "title", "period_start", "period_end")
EBOOK_ID_RE = re.compile(r"/ebook/([^/?#]+)")
# Email titles/descriptions are truncated versions of the blog's, so keep the longer text.
LONGER_WINS_FIELDS = frozenset({"title", "description"})


def ebook_id(kobo_url: str) -> str:
    match = EBOOK_ID_RE.search(kobo_url or "")
    return match[1] if match else ""


def _is_empty(value: object) -> bool:
    return value is None or value == ""


def merge_book(old: dict, new: dict) -> dict:
    merged = {**old}
    for key, value in new.items():
        if _is_empty(value):
            continue
        current = merged.get(key)
        if key in LONGER_WINS_FIELDS and isinstance(current, str) and len(current) > len(str(value)):
            continue
        merged[key] = value
    return merged


def merge_lists(old: dict | None, new: dict) -> dict:
    if not old:
        return new
    old_books = {ebook_id(b.get("kobo_url", "")): b for b in old.get("books", [])}
    new_ids = {ebook_id(b.get("kobo_url", "")) for b in new["books"]}
    books = [merge_book(old_books.get(ebook_id(b["kobo_url"]), {}), b) for b in new["books"]]
    leftovers = [b for key, b in old_books.items() if key not in new_ids]
    header = merge_book({k: v for k, v in old.items() if k != "books"},
                        {k: v for k, v in new.items() if k != "books"})
    # Keep the first list title we saw (the blog post title is the more descriptive one).
    title = old.get("title") or new.get("title")
    return {**header, "title": title, "books": [*books, *leftovers]}


def update_manifest(manifest: dict, book_list: dict) -> dict:
    entry = {k: book_list[k] for k in MANIFEST_FIELDS}
    others = [e for e in manifest.get("lists", []) if e.get("slug") != entry["slug"]]
    lists = sorted([*others, entry], key=lambda e: e["period_start"], reverse=True)
    return {"lists": lists}


def _read_json(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_list(book_list: dict, data_dir: Path) -> dict:
    """Merge into any existing data/<slug>.json, update the manifest, return the stored list."""
    data_dir.mkdir(parents=True, exist_ok=True)
    list_path = data_dir / f"{book_list['slug']}.json"
    stored = merge_lists(_read_json(list_path), book_list)
    _write_json(list_path, stored)
    manifest_path = data_dir / MANIFEST_NAME
    _write_json(manifest_path, update_manifest(_read_json(manifest_path) or {}, stored))
    return stored
