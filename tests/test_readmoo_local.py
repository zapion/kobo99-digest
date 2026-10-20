import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import enrich
import readmoo_local
from test_ratings import BOOK, READMOO_HTML, READMOO_URL, TODAY, fetcher_with, seed_data, write


def brave_payload(*urls):
    return {"web": {"results": [{"url": u, "title": "t"} for u in urls]}}


def test_brave_query_keeps_only_readmoo_book_urls():
    sent = {}

    def get_json(url, headers):
        sent.update(url=url, headers=headers)
        return brave_payload("https://readmoo.com/search/keyword?q=x", READMOO_URL + "?utm=1",
                             "https://www.books.com.tw/x", READMOO_URL)
    urls = readmoo_local.brave_search(BOOK, "secret", get_json)
    assert urls == [READMOO_URL]
    assert "readmoo.com" in sent["url"] and sent["headers"]["X-Subscription-Token"] == "secret"


def test_cached_url_is_tried_before_search(tmp_path):
    data = seed_data(tmp_path)
    write(data / "ratings" / "ai-146.json", {"kobo_id": "ai-146", "title": "x", "hints": {}, "platforms": {
        "readmoo": {"platform": "readmoo", "status": "blocked", "url": READMOO_URL, "fetched_at": "2026-10-19"}}})
    searched = []
    find = readmoo_local.make_find(data, lambda book: searched.append(book) or [])
    result = find(BOOK, "readmoo")
    assert [c["url"] for c in result["candidates"]] == [READMOO_URL] and searched == []


def test_search_used_when_nothing_cached(tmp_path):
    data = seed_data(tmp_path)
    find = readmoo_local.make_find(data, lambda book: [READMOO_URL])
    assert [c["url"] for c in find(BOOK, "readmoo")["candidates"]] == [READMOO_URL]
    assert readmoo_local.make_find(data, None)(BOOK, "readmoo")["candidates"] == []


def test_local_run_rates_readmoo_only_and_keeps_other_platforms(tmp_path):
    data = seed_data(tmp_path)
    goodreads = {"platform": "goodreads", "status": "ok", "url": "https://www.goodreads.com/book/show/1",
                 "score": 4.3, "count": 9, "fetched_at": "2026-10-19"}
    write(data / "ratings" / "ai-146.json", {"kobo_id": "ai-146", "title": "x", "hints": {},
                                             "platforms": {"goodreads": goodreads}})
    calls = []
    fetcher = fetcher_with({READMOO_URL: (200, READMOO_HTML, {})}, calls)
    stats = readmoo_local.run(data, TODAY, fetcher, readmoo_local.make_find(data, lambda b: [READMOO_URL]))
    assert stats["enriched"] == 1
    platforms = json.loads((data / "ratings" / "ai-146.json").read_text())["platforms"]
    assert platforms["readmoo"]["status"] == "ok" and platforms["readmoo"]["score"] == 4.7
    assert platforms["goodreads"] == goodreads
    assert not any("goodreads" in c for c in calls)
    names = [r["platform"] for r in json.loads((data / "weekly-dd99-2026-w42.json").read_text())["books"][0]["ratings"]]
    assert names == ["readmoo", "goodreads"]


def test_actions_run_skips_platforms_not_listed(tmp_path):
    data = seed_data(tmp_path)
    calls = []

    def find(book, platform):
        calls.append(platform)
        return {"candidates": [], "original_title": "", "original_author": ""}
    enrich.run(data, TODAY, fetcher_with({}), find, 40, platforms=("goodreads", "kobo"))
    assert calls == ["goodreads", "kobo"]


def test_platforms_from_env():
    assert enrich.platforms_from_env("") == ("goodreads", "kobo")
    assert enrich.platforms_from_env("readmoo, bogus,kobo") == ("readmoo", "kobo")
