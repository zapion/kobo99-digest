import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import store

BLOG_BOOK = {
    "title": "不懂權謀的人，無法做大事：馬基維利教你如何在職場上出人頭地",
    "author": "馬克．鮑威爾",
    "publisher": "如果出版",
    "description": "完整的部落格介紹文字",
    "kobo_url": "https://www.kobo.com/tw/zh/ebook/HB7?utm_source=twblog",
    "kobo_hk_url": "https://www.kobo.com/hk/zh/ebook/HB7",
    "rank": 1,
}
EMAIL_BOOK = {
    "title": "不懂權謀的人，無法做大事",
    "description": "",
    "kobo_url": "https://www.kobo.com/tw/zh/ebook/HB7",
    "list_price": 255,
    "sale_price": 217,
    "rank": 1,
}


def book_list(books, **extra):
    return {
        "slug": "weekly-8585-2026-w43",
        "kind": "8585",
        "label": "本週精選書單",
        "title": "blog title",
        "period_start": "2026-10-19",
        "period_end": "2026-10-25",
        "promo_code": None,
        "books": books,
        **extra,
    }


def test_ebook_id_ignores_query_and_locale():
    assert store.ebook_id("https://www.kobo.com/hk/zh/ebook/ai-146?x=1") == "ai-146"
    assert store.ebook_id("https://example.com/") == ""


def test_email_after_blog_adds_prices_and_keeps_richer_text():
    merged = store.merge_lists(book_list([BLOG_BOOK]), book_list([EMAIL_BOOK], title="email subject",
                                                                  promo_code="WKSelect1019"))
    book = merged["books"][0]
    assert book["title"] == BLOG_BOOK["title"]
    assert book["author"] == "馬克．鮑威爾"
    assert book["description"] == "完整的部落格介紹文字"
    assert (book["list_price"], book["sale_price"]) == (255, 217)
    assert merged["title"] == "blog title"
    assert merged["promo_code"] == "WKSelect1019"


def test_blog_after_email_keeps_prices():
    merged = store.merge_lists(book_list([EMAIL_BOOK]), book_list([BLOG_BOOK]))
    book = merged["books"][0]
    assert book["sale_price"] == 217 and book["author"] == "馬克．鮑威爾"
    assert book["title"] == BLOG_BOOK["title"]


def test_unmatched_old_books_are_kept_and_inputs_not_mutated():
    other = {**BLOG_BOOK, "kobo_url": "https://www.kobo.com/tw/zh/ebook/OTHER"}
    old = book_list([other])
    new = book_list([EMAIL_BOOK])
    merged = store.merge_lists(old, new)
    assert [store.ebook_id(b["kobo_url"]) for b in merged["books"]] == ["HB7", "OTHER"]
    assert old["books"] == [other] and new["books"] == [EMAIL_BOOK]


def test_write_list_merges_with_file_on_disk(tmp_path):
    store.write_list(book_list([BLOG_BOOK]), tmp_path)
    stored = store.write_list(book_list([EMAIL_BOOK]), tmp_path)
    on_disk = json.loads((tmp_path / "weekly-8585-2026-w43.json").read_text(encoding="utf-8"))
    assert on_disk == stored
    assert on_disk["books"][0]["sale_price"] == 217
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert [e["slug"] for e in manifest["lists"]] == ["weekly-8585-2026-w43"]
