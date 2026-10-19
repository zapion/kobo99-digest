import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

import kobo99
from render_html import render_day_page, render_index

TODAY = dt.date(2026, 10, 16)


def raw_book(title: str, sale_start: str, **extra) -> dict:
    return {
        "title": title,
        "author": "A",
        "sale_start": sale_start,
        "sale_price": "NT$99",
        "kobo_price": "NT$300",
        "cover_url": "covers/x.jpg",
        "avg_score": 4.2,
        "ratings": {"kobo": {"score": 4.5, "count": 3, "url": "https://k"}, "goodreads": {"score": None}},
        **extra,
    }


@pytest.fixture
def payload() -> dict:
    return {
        "year": 2026,
        "week": 42,
        "sale_label": "label",
        "books": [
            raw_book("future", "2026-10-18"),
            raw_book("today", "2026-10-16"),
            raw_book("past", "2026-10-15"),
            {"title": "", "sale_start": "2026-10-16"},
            raw_book("baddate", "nope"),
        ],
    }


def test_build_digest_splits_books_by_sale_date(payload):
    digest = kobo99.build_digest(payload, TODAY)
    assert [b.title for b in digest.today_books] == ["today"]
    assert [b.title for b in digest.upcoming_books] == ["future"]
    assert [b.title for b in digest.past_books] == ["past"]


def test_parse_book_skips_entries_without_title_or_valid_date():
    assert kobo99.parse_book({"title": "", "sale_start": "2026-10-16"}) is None
    assert kobo99.parse_book(raw_book("x", "bad")) is None


def test_ratings_skip_platforms_without_score_and_cover_is_absolute():
    book = kobo99.parse_book(raw_book("x", "2026-10-16"))
    assert [r.platform for r in book.ratings] == ["Kobo"]
    assert book.cover_url == "https://kobo99.com/covers/x.jpg"


def test_render_markdown_has_today_section_first(payload):
    md = kobo99.render_markdown(kobo99.build_digest(payload, TODAY))
    assert md.index("今日 99") < md.index("接下來") < md.index("本週已過")


def test_digest_with_no_books_is_empty():
    assert kobo99.build_digest({"books": []}, TODAY).is_empty


def test_run_refuses_to_write_empty_digest(monkeypatch, tmp_path):
    monkeypatch.setattr(kobo99, "fetch_latest", lambda: {"books": []})
    with pytest.raises(RuntimeError):
        kobo99.run(tmp_path, TODAY)
    assert not list(tmp_path.iterdir())


def test_run_writes_markdown_and_raw_json(monkeypatch, tmp_path, payload):
    monkeypatch.setattr(kobo99, "fetch_latest", lambda: payload)
    md_path = kobo99.run(tmp_path, TODAY)
    assert md_path.name == "2026-10-16-kobo99.md"
    assert kobo99.load_digest(tmp_path, TODAY).today_books[0].title == "today"


def test_html_escapes_untrusted_text(payload):
    payload["books"][1]["title"] = "<script>alert(1)</script>"
    html = render_day_page(kobo99.build_digest(payload, TODAY))
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_index_lists_newest_first():
    html = render_index([("2026-10-15", "2026-10-15.html"), ("2026-10-16", "2026-10-16.html")])
    assert html.index("2026-10-16") < html.index("2026-10-15")
