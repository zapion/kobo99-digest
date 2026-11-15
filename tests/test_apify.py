import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

import apify_fetch
import enrich
import rating_extract
import rating_fetch
from test_ratings import fetcher_with

NOW = "2026-11-16T00:00:00+00:00"
TODAY = dt.date(2026, 11, 16)
KOBO_URL = "https://www.kobo.com/tw/zh/ebook/ai-146"
BOOKS_URL = "https://www.books.com.tw/products/E050123456"
AMAZON_URL = "https://www.amazon.com/dp/B0C1234567"
READMOO_URL = "https://readmoo.com/book/210305021000101"
BOOK = {"title": "AI科學家李飛飛的視界之旅", "author": "李飛飛（Fei-Fei Li）", "kobo_url": KOBO_URL}
HINTS = {"original_title": "The Worlds I See", "original_author": "Fei-Fei Li"}

# Kobo keeps schema.org JSON as strings inside an HTML-escaped JSON attribute.
KOBO_BOOK_LD = {"@type": "Book", "name": "AI科學家李飛飛的視界之旅", "author": {"@type": "Person", "name": "李飛飛"},
                "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.00", "reviewCount": "1", "ratingCount": "7"}}
KOBO_STATIC_HTML = ('<div data-kobo-gizmo="RatingAndReviewWidget" data-kobo-gizmo-config="'
                    + json.dumps({"googleBook": json.dumps(KOBO_BOOK_LD), "showKoboRatingsWidget": True}).replace('"', "&quot;")
                    + '"></div>')
KOBO_RENDERED_HTML = """<h1 class="title">AI科學家李飛飛的視界之旅</h1>
<div class="kobo star-rating rating-4" role="img" aria-label="Rated 4 out of 5 stars"></div>
<section class="overall-rating-container" data-rating-value="4.33" data-total-ratings="3"><h2>整體評分</h2></section>"""
KOBO_UNRATED_HTML = '<section class="overall-rating-container" data-rating-value="0" data-total-ratings="0"></section>'
BOOKS_LD = {"@type": "Book", "name": "AI科學家李飛飛的視界之旅 (電子書)", "author": [{"@type": "Person", "name": "李飛飛"}],
            "aggregateRating": {"@type": "AggregateRating", "ratingValue": 5, "reviewCount": 20}}
READMOO_BODY = """<div itemprop="aggregateRating" itemscope itemtype="http://schema.org/AggregateRating">
<div itemprop="ratingValue" content="4.7" id="star" data-score="4.7"></div> 共 <span itemprop="ratingCount">141</span> 人評分</div>"""


def payload(html, status=200, title="", og_title="", json_ld=None):
    """A Web Fetch response: the HTML arrives without <head>; title, og tags and JSON-LD are in metadata."""
    return {"url": "u", "fetch": {"httpStatusCode": status}, "html": html,
            "metadata": {"title": title, "jsonLd": json_ld,
                         "openGraph": [{"property": "og:title", "content": og_title}] if og_title else []}}


def reader_with(responses, calls=None, **kwargs):
    """ApifyReader whose HTTP layer answers from {page url or 'amazon': response or Exception}."""
    def post(url, body, token):
        key = "amazon" if "run-sync-get-dataset-items" in url else body["url"]
        if calls is not None:
            calls.append((url, body))
        response = responses[key]
        if isinstance(response, Exception):
            raise response
        return response
    return apify_fetch.ApifyReader("token", post=post, **kwargs)


def never_find(book, platform):
    raise AssertionError(f"unexpected search for {platform}")


def candidate(url, title="", author="", confidence=0.9, rating=None, rating_count=None):
    return {"url": url, "title": title, "author": author, "confidence": confidence, "rating": rating, "rating_count": rating_count}


def finder(candidates, **hints):
    return lambda book, platform: {"candidates": candidates, "original_title": hints.get("original_title", ""),
                                   "original_author": hints.get("original_author", "")}


def rate(platform, apify, find=never_find, fetcher=None, hints=None, extra_url=None, override=None):
    entry, _hints = enrich.rate_platform(BOOK, platform, fetcher or fetcher_with({}), find, override, hints or {},
                                         NOW, apify, extra_url)
    return entry


# ── extract ──────────────────────────────────────────────────────────────────

def test_extract_kobo_rating_from_widget_config_and_rendered_widget():
    assert rating_extract.extract_rating(KOBO_STATIC_HTML) == {"score": 4.0, "count": 7}
    assert rating_extract.page_identity(KOBO_STATIC_HTML) == {"title": "AI科學家李飛飛的視界之旅", "authors": ["李飛飛"]}
    assert rating_extract.extract_rating(KOBO_RENDERED_HTML) == {"score": 4.33, "count": 3}
    assert rating_extract.extract_rating(KOBO_UNRATED_HTML) is None
    assert rating_extract.extract_rating('<div data-kobo-gizmo-config="{broken"></div>') is None


# ── ApifyReader ──────────────────────────────────────────────────────────────

def test_page_restores_title_and_json_ld_from_metadata():
    reader = reader_with({BOOKS_URL: payload("<h1>x</h1>", json_ld=[{"@type": "BreadcrumbList"}, BOOKS_LD])})
    assert reader.read("books", BOOKS_URL) == {"title": "AI科學家李飛飛的視界之旅 (電子書)", "authors": ["李飛飛"],
                                               "rating": {"score": 5.0, "count": 20}}
    og = "AI科學家李飛飛的視界之旅 - 李飛飛 | Readmoo 讀墨電子書"
    reader = reader_with({READMOO_URL: payload(READMOO_BODY, og_title=og)})
    assert reader.read("readmoo", READMOO_URL) == {"title": "AI科學家李飛飛的視界之旅", "authors": ["李飛飛"],
                                                   "rating": {"score": 4.7, "count": 141}}


def test_json_ld_cannot_close_its_script_element():
    tricky = {"@type": "Book", "name": "A</script><b>", "aggregateRating": {"ratingValue": 4, "ratingCount": 2}}
    reader = reader_with({BOOKS_URL: payload("", json_ld=tricky)})
    assert reader.read("books", BOOKS_URL)["title"] == "A</script><b>"


def test_kobo_page_is_rendered_in_a_browser_and_others_are_not():
    calls = []
    reader = reader_with({KOBO_URL: payload(KOBO_RENDERED_HTML), BOOKS_URL: payload("", json_ld=BOOKS_LD)}, calls)
    assert reader.read("kobo", KOBO_URL)["rating"] == {"score": 4.33, "count": 3}
    reader.read("books", BOOKS_URL)
    assert calls[0] == (apify_fetch.WEB_FETCH_URL, {"url": KOBO_URL, "formats": ["html"], "wait": 8000})
    assert calls[1][1] == {"url": BOOKS_URL, "formats": ["html"]}


@pytest.mark.parametrize(
    "response, error",
    [
        (payload("", status=404), rating_fetch.NotFound),
        (payload("<p>denied</p>", status=403), rating_fetch.Blocked),
        (payload("<p>wait</p>", title="Just a moment..."), rating_fetch.Blocked),
        ({"fetch": {"httpStatusCode": 200}}, rating_fetch.Blocked),  # no html came back
        ("not an object", apify_fetch.ApifyError),
    ],
)
def test_page_reports_missing_blocked_and_broken_responses(response, error):
    with pytest.raises(error):
        reader_with({BOOKS_URL: response}).page(BOOKS_URL)


def test_amazon_reads_fields_from_the_product_actor():
    calls = []
    item = {"title": "The Worlds I See", "author": "Fei-Fei Li", "stars": 4.6, "reviewsCount": 2511}
    reader = reader_with({"amazon": [item]}, calls)
    assert reader.read("amazon", AMAZON_URL) == {"title": "The Worlds I See", "authors": ["Fei-Fei Li"],
                                                 "rating": {"score": 4.6, "count": 2511}}
    url, body = calls[0]
    assert url.startswith("https://api.apify.com/v2/acts/junglee~Amazon-crawler/run-sync-get-dataset-items?")
    assert "maxTotalChargeUsd=0.02" in url and "fields=title%2Cauthor%2Cstars%2CreviewsCount" in url
    assert body == {"categoryOrProductUrls": [{"url": AMAZON_URL}], "maxItemsPerStartUrl": 1, "maxOffers": 0,
                    "scrapeSellers": False}


def test_amazon_without_reviews_or_without_a_product():
    unrated = {"title": "就算長大了", "author": "", "stars": None, "reviewsCount": None}
    assert reader_with({"amazon": [unrated]}).read("amazon", AMAZON_URL) == {"title": "就算長大了", "authors": [], "rating": None}
    with pytest.raises(rating_fetch.NotFound):
        reader_with({"amazon": []}).read("amazon", AMAZON_URL)


def test_budget_stops_calls_before_they_are_made():
    calls = []
    reader = reader_with({BOOKS_URL: payload("", json_ld=BOOKS_LD)}, calls, budget_usd=0.004)
    reader.page(BOOKS_URL)
    reader.page(BOOKS_URL)
    assert reader.has_budget("books") is False and reader.has_budget("amazon") is False
    with pytest.raises(apify_fetch.BudgetExceeded):
        reader.page(BOOKS_URL)
    assert len(calls) == 2 and reader.calls == 2 and reader.spent == pytest.approx(0.003)


# ── robots.txt for pages read through Apify ──────────────────────────────────

def test_robots_allow_refuses_only_on_a_readable_disallow():
    fetcher = fetcher_with({
        "https://www.books.com.tw/robots.txt": (200, "User-agent: *\nDisallow: /private/\n", {}),
        "https://www.kobo.com/robots.txt": (403, "<title>Just a moment...</title>", {"cf-mitigated": "challenge"}),
    })
    assert fetcher.robots_allow(BOOKS_URL) is True
    assert fetcher.robots_allow("https://www.books.com.tw/private/x") is False
    assert fetcher.robots_allow(KOBO_URL) is True  # robots.txt itself is behind the challenge
    assert fetcher.robots_allow("http://www.kobo.com/x") is False


# ── enrich ───────────────────────────────────────────────────────────────────

def test_kobo_own_page_is_read_through_apify_without_a_search():
    apify = reader_with({KOBO_URL: payload(KOBO_RENDERED_HTML, json_ld=[{"@type": "Book", "name": BOOK["title"]}])})
    assert rate("kobo", apify) == {"platform": "kobo", "fetched_at": NOW, "status": "ok", "url": KOBO_URL,
                                   "matched_title": BOOK["title"], "score": 4.33, "count": 3, "via": "apify"}


def test_kobo_page_without_ratings_and_page_for_another_book():
    unrated = payload(KOBO_UNRATED_HTML, json_ld=[{"@type": "Book", "name": BOOK["title"]}])
    assert rate("kobo", reader_with({KOBO_URL: unrated}))["status"] == "no_rating"
    other = payload(KOBO_RENDERED_HTML, json_ld=[{"@type": "Book", "name": "老年理財"}])
    assert rate("kobo", reader_with({KOBO_URL: other}))["status"] == "low_confidence"


def test_kobo_is_left_for_the_next_run_when_apify_fails():
    assert rate("kobo", reader_with({KOBO_URL: apify_fetch.ApifyError("boom")})) is None
    assert rate("kobo", reader_with({KOBO_URL: payload("", title="Just a moment...")})) is None


def test_amazon_page_is_verified_against_the_original_title():
    item = {"title": "The Worlds I See: Curiosity, Exploration, and Discovery", "author": "Fei-Fei Li", "stars": 4.6, "reviewsCount": 2511}
    find = finder([candidate(AMAZON_URL, "The Worlds I See", "Fei-Fei Li")])
    entry = rate("amazon", reader_with({"amazon": [item]}), find, hints=HINTS)
    assert (entry["status"], entry["score"], entry["count"], entry["via"]) == ("ok", 4.6, 2511, "apify")
    wrong = {"title": "Think and Grow Rich", "author": "Napoleon Hill", "stars": 4.7, "reviewsCount": 9}
    entry = rate("amazon", reader_with({"amazon": [wrong]}), find, hints=HINTS)
    assert entry["status"] == "low_confidence" and entry["tried"][0]["title"] == "Think and Grow Rich"


def test_apify_error_falls_back_to_the_search_reported_rating():
    find = finder([candidate(AMAZON_URL, "The Worlds I See", "Fei-Fei Li", rating=4.5, rating_count=2000)], **HINTS)
    entry = rate("amazon", reader_with({"amazon": apify_fetch.ApifyError("boom")}), find)
    assert (entry["status"], entry["score"]) == ("search_reported", 4.5) and "via" not in entry


def test_page_kobo99_points_at_is_read_and_checked_when_search_finds_nothing():
    apify = reader_with({BOOKS_URL: payload("", json_ld=BOOKS_LD)})
    entry = rate("books", apify, finder([]), extra_url=BOOKS_URL + "?utm_source=kobo99")
    assert (entry["status"], entry["url"], entry["count"]) == ("ok", BOOKS_URL, 20)
    other = {**BOOKS_LD, "name": "老年理財 (電子書)"}
    entry = rate("books", reader_with({BOOKS_URL: payload("", json_ld=other)}), finder([]), extra_url=BOOKS_URL)
    assert entry["status"] == "low_confidence"


def test_without_apify_the_kobo99_page_is_not_a_candidate_for_unfetchable_platforms():
    assert rate("books", None, finder([]), extra_url=BOOKS_URL)["status"] == "not_found"
    spent = reader_with({}, budget_usd=0)
    assert rate("books", spent, finder([]), extra_url=BOOKS_URL)["status"] == "not_found"


def test_goodreads_page_kobo99_points_at_is_fetched_by_us():
    url = "https://www.goodreads.com/book/show/144405196"
    html = ('<script type="application/ld+json">{"@type":"Book","name":"The Worlds I See","author":[{"name":"Fei-Fei Li"}],'
            '"aggregateRating":{"ratingValue":4.3,"ratingCount":5715}}</script>')
    entry = rate("goodreads", None, finder([], **HINTS), fetcher_with({url: (200, html, {})}), extra_url=url)
    assert (entry["status"], entry["score"]) == ("ok", 4.3) and "via" not in entry


def test_readmoo_blocked_for_us_is_read_through_apify():
    og = "AI科學家李飛飛的視界之旅 - 李飛飛 | Readmoo 讀墨電子書"
    fetcher = fetcher_with({READMOO_URL: (403, "denied", {})})
    find = finder([candidate(READMOO_URL, BOOK["title"], "李飛飛")])
    entry = rate("readmoo", reader_with({READMOO_URL: payload(READMOO_BODY, og_title=og)}), find, fetcher)
    assert (entry["status"], entry["score"], entry["via"]) == ("ok", 4.7, "apify")
    assert rate("readmoo", None, find, fetcher_with({READMOO_URL: (403, "denied", {})}))["status"] == "blocked"


def test_robots_disallow_keeps_the_page_away_from_apify():
    calls = []
    fetcher = fetcher_with({"https://www.books.com.tw/robots.txt": (200, "User-agent: *\nDisallow: /products/\n", {})})
    apify = reader_with({BOOKS_URL: payload("", json_ld=BOOKS_LD)}, calls)
    entry = rate("books", apify, finder([candidate(BOOKS_URL, BOOK["title"], "李飛飛")]), fetcher)
    assert entry == {"platform": "books", "fetched_at": NOW, "status": "disallowed", "url": BOOKS_URL} and calls == []


def test_at_most_two_pages_per_platform_are_paid_for():
    calls = []
    urls = [f"https://www.books.com.tw/products/E05000000{i}" for i in range(3)]
    other = payload("", json_ld={**BOOKS_LD, "name": "老年理財"})
    apify = reader_with({url: other for url in urls}, calls)
    entry = rate("books", apify, finder([candidate(url, BOOK["title"], "李飛飛") for url in urls]))
    assert entry["status"] == "low_confidence" and len(calls) == 2


def test_platforms_and_reader_from_env():
    assert enrich.apify_from_env({}) is None and enrich.apify_from_env({"APIFY_TOKEN": " "}) is None
    reader = enrich.apify_from_env({"APIFY_TOKEN": "t"})
    assert reader.platforms == {"kobo", "books", "amazon", "readmoo"}
    assert enrich.platforms_from_env("", reader) == ("goodreads", "books", "amazon", "kobo")
    assert enrich.platforms_from_env("", None) == ("goodreads", "books", "amazon")
    assert enrich.platforms_from_env("readmoo,kobo", reader) == ("readmoo", "kobo")
    narrow = enrich.apify_from_env({"APIFY_TOKEN": "t", "APIFY_PLATFORMS": "amazon, nope", "APIFY_BUDGET_USD": "0.01"})
    assert narrow.platforms == {"amazon"} and narrow.has_budget("amazon") and not narrow.handles("kobo")
    assert enrich.platforms_from_env("", narrow) == ("goodreads", "books", "amazon")


def test_run_reads_kobo_for_every_book_and_publishes_it_as_a_page_rating(tmp_path):
    book_list = {"slug": "weekly-dd99-2026-w46", "kind": "dd99", "label": "一週99書單", "title": "t",
                 "period_start": "2026-11-12", "period_end": "2026-11-18", "books": [BOOK]}
    (tmp_path / "weekly-dd99-2026-w46.json").write_text(json.dumps(book_list), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"lists": [{k: book_list[k] for k in (
        "slug", "kind", "label", "title", "period_start", "period_end")}]}), encoding="utf-8")
    apify = reader_with({KOBO_URL: payload(KOBO_RENDERED_HTML, json_ld=[{"@type": "Book", "name": BOOK["title"]}])})
    stats = enrich.run(tmp_path, TODAY, fetcher_with({}), None, 10, platforms=("kobo",), apify=apify)
    assert stats["enriched"] == 1 and apify.calls == 1
    stored = json.loads((tmp_path / "weekly-dd99-2026-w46.json").read_text(encoding="utf-8"))
    assert stored["books"][0]["ratings"] == [{"platform": "kobo", "name": "Kobo", "score": 4.33, "count": 3,
                                              "url": KOBO_URL, "source": "page"}]
