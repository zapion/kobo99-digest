import datetime as dt
import io
import json
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

import build_data
import enrich
import rating_discover
import rating_extract
import rating_fetch

READMOO_HTML = """<html><head><title>AI科學家李飛飛的視界之旅 - 李飛飛 | Readmoo 讀墨電子書</title>
<meta property="og:title" content="AI科學家李飛飛的視界之旅 - 李飛飛 | Readmoo 讀墨電子書" /></head><body>
<div class="quick-btn-star" itemprop="aggregateRating" itemscope itemtype="http://schema.org/AggregateRating">
<div itemprop="ratingValue" content="4.7" id="star" data-score="4.7"></div> 共 <span itemprop="ratingCount">141</span> 人評分
</div></body></html>"""

GOODREADS_HTML = """<html><head><title>The Worlds I See by Fei-Fei Li | Goodreads</title>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Book",
"name":"The Worlds I See: Curiosity, Exploration, and Discovery at the Dawn of AI",
"author":[{"@type":"Person","name":"Fei-Fei Li"}],
"aggregateRating":{"@type":"AggregateRating","ratingValue":4.3,"ratingCount":5715,"reviewCount":740}}</script>
</head><body></body></html>"""

BOOK = {"title": "AI科學家李飛飛的視界之旅", "author": "李飛飛（Fei-Fei Li）", "kobo_url": "https://www.kobo.com/tw/zh/ebook/ai-146"}
READMOO_URL = "https://readmoo.com/book/210305021000101"
GOODREADS_URL = "https://www.goodreads.com/book/show/144405196"


# ── extract ──────────────────────────────────────────────────────────────────

def test_extract_readmoo_microdata_and_identity():
    assert rating_extract.extract_rating(READMOO_HTML) == {"score": 4.7, "count": 141}
    assert rating_extract.page_identity(READMOO_HTML) == {"title": "AI科學家李飛飛的視界之旅", "authors": ["李飛飛"]}


def test_extract_goodreads_json_ld_and_identity():
    assert rating_extract.extract_rating(GOODREADS_HTML) == {"score": 4.3, "count": 5715}
    identity = rating_extract.page_identity(GOODREADS_HTML)
    assert identity["authors"] == ["Fei-Fei Li"]
    assert identity["title"].startswith("The Worlds I See")


def test_extract_handles_missing_and_broken_data():
    assert rating_extract.extract_rating("<html>no rating</html>") is None
    broken = '<script type="application/ld+json">{not json</script>'
    assert rating_extract.extract_rating(broken) is None
    assert rating_extract.page_identity("<html></html>") == {"title": "", "authors": []}
    graph = '<script type="application/ld+json">{"@graph":[{"aggregateRating":{"ratingValue":"3.9","reviewCount":"1,204"}}]}</script>'
    assert rating_extract.extract_rating(graph) == {"score": 3.9, "count": 1204}


@pytest.mark.parametrize(
    "expected, found, ok",
    [
        ("AI科學家李飛飛的視界之旅", "AI科學家李飛飛的視界之旅", True),
        ("柳林風聲：年度暢銷書《蛤蟆先生去看心理師》故事原型", "柳林風聲", True),
        ("霍金傳", "霍金传", False),  # simplified vs traditional: not the same edition
        ("思考致富", "思考致富：全球銷售七千萬冊", True),
        ("A", "A", False),  # too short to trust
        ("The Worlds I See", "The Worlds I See: Curiosity, Exploration", True),
        ("中年理財", "老年理財", False),
    ],
)
def test_titles_match(expected, found, ok):
    assert rating_extract.titles_match(expected, found) is ok


def test_authors_match_handles_latin_names_and_empty_expectation():
    assert rating_extract.authors_match("李飛飛（Fei-Fei Li）", ["Fei-Fei Li"])
    assert rating_extract.authors_match("李飛飛（Fei-Fei Li）", ["李飛飛"])
    assert rating_extract.authors_match("", ["anyone"])
    assert not rating_extract.authors_match("村田裕之", ["Napoleon Hill"])


# ── fetch ────────────────────────────────────────────────────────────────────

class FakeResponse(io.BytesIO):
    def __init__(self, status, body, headers=None):
        super().__init__(body.encode())
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def make_opener(routes, calls):
    def opener(request, timeout):
        url = request.full_url
        calls.append(url)
        status, body, headers = routes.get(url, (404, "", {}))
        if status >= 400:
            raise urllib.error.HTTPError(url, status, "err", headers, io.BytesIO(body.encode()))
        return FakeResponse(status, body, headers)
    return opener


ROBOTS = {
    "https://readmoo.com/robots.txt": (200, "User-agent: *\nCrawl-delay: 10\nDisallow: /search/\n", {}),
    "https://www.goodreads.com/robots.txt": (200, "User-agent: *\nDisallow: /search\n", {}),
}


def fetcher_with(routes, calls=None, sleeps=None):
    clock = iter(range(0, 10_000, 1))
    return rating_fetch.PoliteFetcher(
        opener=make_opener({**ROBOTS, **routes}, calls if calls is not None else []),
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
        clock=lambda: float(next(clock)),
    )


def test_fetch_obeys_robots_and_crawl_delay():
    calls, sleeps = [], []
    fetcher = fetcher_with({READMOO_URL: (200, READMOO_HTML, {}), "https://readmoo.com/book/2": (200, "x", {})}, calls, sleeps)
    assert "ratingValue" in fetcher.get(READMOO_URL)
    fetcher.get("https://readmoo.com/book/2")
    with pytest.raises(rating_fetch.Disallowed):
        fetcher.get("https://readmoo.com/search/keyword?q=x")
    assert calls.count("https://readmoo.com/robots.txt") == 1
    assert sleeps and sleeps[0] > 8  # waited out most of the 10s crawl delay


@pytest.mark.parametrize(
    "status, body, headers",
    [
        (202, "", {"x-amzn-waf-action": "challenge"}),
        (200, "", {}),
        (403, "<title>Just a moment...</title>", {"cf-mitigated": "challenge"}),
        (429, "slow down", {}),
        (200, "<html><title>Robot Check</title></html>", {}),
    ],
)
def test_fetch_treats_bot_challenges_as_blocked_and_stops_for_host(status, body, headers):
    calls = []
    fetcher = fetcher_with({GOODREADS_URL: (status, body, headers)}, calls)
    with pytest.raises(rating_fetch.Blocked):
        fetcher.get(GOODREADS_URL)
    with pytest.raises(rating_fetch.Blocked):
        fetcher.get("https://www.goodreads.com/book/show/1")
    assert "https://www.goodreads.com/book/show/1" not in calls


def test_fetch_normal_waf_allow_header_is_not_blocked():
    fetcher = fetcher_with({GOODREADS_URL: (200, GOODREADS_HTML, {"x-amzn-waf-action": "allow"})})
    assert "aggregateRating" in fetcher.get(GOODREADS_URL)


def test_fetch_404_and_non_https():
    fetcher = fetcher_with({})
    with pytest.raises(rating_fetch.NotFound):
        fetcher.get("https://readmoo.com/book/999")
    with pytest.raises(rating_fetch.Disallowed):
        fetcher.get("http://readmoo.com/book/1")


def test_fetch_robots_server_error_means_stay_away():
    fetcher = rating_fetch.PoliteFetcher(opener=make_opener({"https://readmoo.com/robots.txt": (500, "", {})}, []),
                                         sleep=lambda s: None)
    with pytest.raises(rating_fetch.Disallowed):
        fetcher.get(READMOO_URL)


# ── discover ─────────────────────────────────────────────────────────────────

def openai_payload(result: dict) -> dict:
    return {"output": [{"type": "web_search_call"},
                       {"type": "message", "content": [{"type": "output_text", "text": json.dumps(result)}]}]}


def cand(url, title="t", author="a", conf=0.9, rating=None, count=None):
    return {"url": url, "title": title, "author": author, "same_book_confidence": conf, "rating": rating, "rating_count": count}


def test_discover_filters_to_allowlisted_canonical_urls_and_cleans_fields():
    sent = {}

    def post(body, key):
        sent.update(body)
        return openai_payload({"candidates": [
            cand(READMOO_URL + "?utm=x", rating=4.7, count=141, conf=1.7),
            cand("https://readmoo.com/search/keyword?q=x"),
            cand("https://evil.example/book/1"),
            cand(READMOO_URL, title="dup"),
        ], "original_title": "The Worlds I See", "original_author": "Fei-Fei Li"})

    result = rating_discover.discover(BOOK, "readmoo", "key", model="m", post=post)
    assert result["candidates"] == [{"url": READMOO_URL, "title": "t", "author": "a", "confidence": 1.0,
                                     "rating": 4.7, "rating_count": 141}]
    assert (result["original_title"], result["original_author"]) == ("The Worlds I See", "Fei-Fei Li")
    assert sent["tools"] == [{"type": "web_search", "filters": {"allowed_domains": ["readmoo.com"]}}]
    assert sent["text"]["format"]["strict"] is True
    assert "short title: AI科學家李飛飛的視界之旅" in sent["input"]


@pytest.mark.parametrize(
    "hint",
    ["（未在 readmoo.com 找到原文書名；本次搜尋未發現）", "(not found on readmoo.com)", "x" * 200, "unknown", ""],
)
def test_discover_drops_noise_hints(hint):
    post = lambda body, key: openai_payload({"candidates": [], "original_title": hint, "original_author": hint})
    result = rating_discover.discover(BOOK, "goodreads", "k", post=post)
    assert result == {"candidates": [], "original_title": "", "original_author": ""}


def test_discover_rejects_impossible_ratings():
    post = lambda body, key: openai_payload({"candidates": [cand(GOODREADS_URL, rating=8.5, count=-3)],
                                             "original_title": "", "original_author": ""})
    candidate = rating_discover.discover(BOOK, "goodreads", "k", post=post)["candidates"][0]
    assert (candidate["rating"], candidate["rating_count"]) == (None, None)


def test_discover_rejects_unusable_responses():
    with pytest.raises(rating_discover.DiscoveryError):
        rating_discover.discover(BOOK, "goodreads", "k", post=lambda b, k: {"output": []})
    bad = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "not json"}]}]}
    with pytest.raises(rating_discover.DiscoveryError):
        rating_discover.discover(BOOK, "goodreads", "k", post=lambda b, k: bad)


# ── enrich ───────────────────────────────────────────────────────────────────

TODAY = dt.date(2026, 10, 19)


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def seed_data(tmp_path: Path, books=None) -> Path:
    data = tmp_path / "data"
    write(data / "manifest.json", {"lists": [
        {"slug": "weekly-dd99-2026-w42", "kind": "dd99", "period_start": "2026-10-15", "period_end": "2026-10-21"},
        {"slug": "weekly-dd99-2026-w30", "kind": "dd99", "period_start": "2026-07-23", "period_end": "2026-07-29"},
    ]})
    write(data / "weekly-dd99-2026-w42.json", {"slug": "weekly-dd99-2026-w42", "books": books or [BOOK]})
    write(data / "weekly-dd99-2026-w30.json", {"slug": "weekly-dd99-2026-w30", "books": [BOOK]})
    return data


def fake_find(results):
    calls = []

    def find(book, platform):
        calls.append(platform)
        return results.get(platform, found(None))
    return find, calls


def found(url, title="", author="", conf=0.9, rating=None, count=None, original_title="", original_author=""):
    candidates = [{"url": url, "title": title, "author": author, "confidence": conf, "rating": rating,
                   "rating_count": count}] if url else []
    return {"candidates": candidates, "original_title": original_title, "original_author": original_author}


FOUND = {
    "readmoo": found(READMOO_URL),
    # Goodreads runs first and supplies the original title/author hints.
    "goodreads": found(GOODREADS_URL, original_title="The Worlds I See", original_author="Fei-Fei Li"),
}


def test_enrich_verifies_and_attaches_ratings_then_uses_cache(tmp_path):
    data = seed_data(tmp_path)
    fetcher = fetcher_with({READMOO_URL: (200, READMOO_HTML, {}), GOODREADS_URL: (200, GOODREADS_HTML, {})})
    find, calls = fake_find(FOUND)
    stats = enrich.run(data, TODAY, fetcher, find, 40)
    assert stats["enriched"] == 1 and stats["lists_updated"] == 1
    book = json.loads((data / "weekly-dd99-2026-w42.json").read_text())["books"][0]
    assert book["ratings"] == [
        {"platform": "readmoo", "name": "讀墨", "score": 4.7, "count": 141, "url": READMOO_URL, "source": "page"},
        {"platform": "goodreads", "name": "Goodreads", "score": 4.3, "count": 5715, "url": GOODREADS_URL, "source": "page"},
    ]
    cache = json.loads((data / "ratings" / "ai-146.json").read_text())
    assert cache["hints"]["original_title"] == "The Worlds I See"
    # old list outside the active window is untouched
    assert "ratings" not in json.loads((data / "weekly-dd99-2026-w30.json").read_text())["books"][0]
    # second run: everything fresh, no discovery or fetching
    assert enrich.run(data, TODAY, fetcher, find, 40)["enriched"] == 0
    assert calls == list(enrich.PLATFORM_ORDER)


def test_enrich_rejects_unverified_pages(tmp_path):
    data = seed_data(tmp_path, books=[{**BOOK, "title": "中年理財", "author": "雨果"}])
    fetcher = fetcher_with({READMOO_URL: (200, READMOO_HTML, {}), GOODREADS_URL: (200, GOODREADS_HTML, {})})
    find, _ = fake_find({"readmoo": found(READMOO_URL, title="AI科學家", conf=0.95), "goodreads": found(GOODREADS_URL, conf=0.4)})
    enrich.run(data, TODAY, fetcher, find, 40)
    cache = json.loads((data / "ratings" / "ai-146.json").read_text())
    assert cache["platforms"]["readmoo"]["status"] == "low_confidence"
    assert cache["platforms"]["goodreads"]["status"] == "low_confidence"
    assert json.loads((data / "weekly-dd99-2026-w42.json").read_text())["books"][0]["ratings"] == []


def test_enrich_records_blocked_and_not_found(tmp_path):
    data = seed_data(tmp_path)
    fetcher = fetcher_with({READMOO_URL: (200, READMOO_HTML, {}), GOODREADS_URL: (202, "", {"x-amzn-waf-action": "challenge"})})
    find, _ = fake_find({"readmoo": found(None), "goodreads": found(GOODREADS_URL, conf=0.95)})
    enrich.run(data, TODAY, fetcher, find, 40)
    platforms = json.loads((data / "ratings" / "ai-146.json").read_text())["platforms"]
    assert platforms["readmoo"]["status"] == "not_found"
    assert platforms["goodreads"]["status"] == "blocked"


def test_blocked_page_falls_back_to_search_reported_rating(tmp_path):
    data = seed_data(tmp_path)
    fetcher = fetcher_with({READMOO_URL: (403, "nope", {}), GOODREADS_URL: (202, "", {"x-amzn-waf-action": "challenge"})})
    find, _ = fake_find({
        "readmoo": found(READMOO_URL, title="AI科學家李飛飛的視界之旅", conf=0.9, rating=4.7, count=141),
        "goodreads": found(GOODREADS_URL, title="The Worlds I See", conf=0.5, rating=4.3, count=5715),  # not confident
    })
    enrich.run(data, TODAY, fetcher, find, 40)
    platforms = json.loads((data / "ratings" / "ai-146.json").read_text())["platforms"]
    assert platforms["readmoo"]["status"] == "search_reported"
    assert platforms["goodreads"]["status"] == "blocked"
    book = json.loads((data / "weekly-dd99-2026-w42.json").read_text())["books"][0]
    assert book["ratings"] == [{"platform": "readmoo", "name": "讀墨", "score": 4.7, "count": 141,
                                "url": READMOO_URL, "source": "search"}]


def test_confident_model_match_accepted_when_page_title_confirms(tmp_path):
    visual_detox = {"title": "視覺排毒：辨識視覺語言背後隱藏的訊息", "author": "瑪琳．唐吉",
                    "kobo_url": "https://www.kobo.com/tw/zh/ebook/1mb76"}
    page = GOODREADS_HTML.replace("The Worlds I See: Curiosity, Exploration, and Discovery at the Dawn of AI",
                                  "The Visual Detox").replace("Fei-Fei Li", "Marine Tanguy")
    data = seed_data(tmp_path, books=[visual_detox])
    fetcher = fetcher_with({GOODREADS_URL: (200, page, {})})
    find, _ = fake_find({"readmoo": found(None),
                         "goodreads": found(GOODREADS_URL, title="The Visual Detox", author="Marine Tanguy", conf=0.9)})
    enrich.run(data, TODAY, fetcher, find, 40)
    entry = json.loads((data / "ratings" / "1mb76.json").read_text())["platforms"]["goodreads"]
    assert entry["status"] == "ok" and entry["score"] == 4.3


def test_model_match_rejected_when_page_title_differs(tmp_path):
    data = seed_data(tmp_path, books=[{**BOOK, "title": "視覺排毒", "author": "瑪琳．唐吉"}])
    fetcher = fetcher_with({GOODREADS_URL: (200, GOODREADS_HTML, {})})
    find, _ = fake_find({"readmoo": found(None),
                         "goodreads": found(GOODREADS_URL, title="The Visual Detox", conf=0.99, rating=4.1, count=9)})
    enrich.run(data, TODAY, fetcher, find, 40)
    entry = json.loads((data / "ratings" / "ai-146.json").read_text())["platforms"]["goodreads"]
    # the page says "The Worlds I See": the model's claim is contradicted, so its search number is not used
    assert entry["status"] == "low_confidence"
    assert json.loads((data / "weekly-dd99-2026-w42.json").read_text())["books"][0]["ratings"] == []


def test_refresh_ignores_ttl(tmp_path):
    data = seed_data(tmp_path)
    fetcher = fetcher_with({READMOO_URL: (200, READMOO_HTML, {}), GOODREADS_URL: (200, GOODREADS_HTML, {})})
    find, calls = fake_find(FOUND)
    enrich.run(data, TODAY, fetcher, find, 40)
    enrich.run(data, TODAY, fetcher, find, 40, refresh=True)
    assert calls == list(enrich.PLATFORM_ORDER) * 2


def test_enrich_without_api_key_uses_overrides_only(tmp_path):
    data = seed_data(tmp_path)
    write(data / "rating_overrides.json", {"ai-146": {"readmoo": READMOO_URL + "?x=1"}})
    calls = []
    fetcher = fetcher_with({READMOO_URL: (200, READMOO_HTML, {})}, calls)
    enrich.run(data, TODAY, fetcher, None, 40)
    platforms = json.loads((data / "ratings" / "ai-146.json").read_text())["platforms"]
    assert platforms == {"readmoo": {**platforms["readmoo"], "status": "ok", "url": READMOO_URL}}
    enrich.run(data, TODAY, fetcher, None, 40)
    assert calls.count(READMOO_URL) == 1  # override cached, not refetched


def test_enrich_discovery_error_leaves_cache_for_retry(tmp_path):
    data = seed_data(tmp_path)

    def failing(book, platform):
        raise rating_discover.DiscoveryError("boom")

    stats = enrich.run(data, TODAY, fetcher_with({}), failing, 40)
    assert stats["enriched"] == 0
    assert not (data / "ratings").exists()


def test_is_fresh_ttl():
    entry = {"status": "blocked", "fetched_at": "2026-10-15T00:00:00+00:00"}
    assert enrich.is_fresh(entry, TODAY)
    assert not enrich.is_fresh(entry, TODAY + dt.timedelta(days=3))
    assert not enrich.is_fresh({"status": "weird", "fetched_at": "2026-10-19"}, TODAY)


# ── build_data ───────────────────────────────────────────────────────────────

INBOX_LIST = {
    "slug": "weekly-8585-2026-w43", "kind": "8585", "label": "本週精選書單", "title": "t",
    "period_start": "2026-10-19", "period_end": "2026-10-25", "promo_code": "WK1",
    "books": [{"title": "A", "kobo_url": "https://www.kobo.com/tw/zh/ebook/A", "rank": 1}],
}


def test_build_data_merges_valid_inbox_and_keeps_invalid(tmp_path):
    data = tmp_path / "data"
    write(data / "inbox" / "weekly-8585-2026-w43.json", INBOX_LIST)
    write(data / "inbox" / "bad.json", {**INBOX_LIST, "slug": "../../etc"})
    write(data / "inbox" / "broken.json", {})
    (data / "inbox" / "junk.json").write_text("{", encoding="utf-8")
    merged, errors = build_data.process_inbox(data / "inbox", data)
    assert merged == ["weekly-8585-2026-w43"]
    assert len(errors) == 3
    assert not (data / "inbox" / "weekly-8585-2026-w43.json").exists()
    assert (data / "inbox" / "bad.json").exists()
    assert json.loads((data / "manifest.json").read_text())["lists"][0]["slug"] == "weekly-8585-2026-w43"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: {**d, "kind": "dd99"},
        lambda d: {**d, "period_end": "soon"},
        lambda d: {**d, "books": []},
        lambda d: {**d, "books": [{"title": "A", "kobo_url": "https://evil.example/ebook/A"}]},
        lambda d: [d],
    ],
)
def test_build_data_validation(mutate):
    with pytest.raises(build_data.InboxError):
        build_data.validate(mutate(INBOX_LIST))


KOBO_URL = "https://www.kobo.com/tw/zh/ebook/ai-146"


def test_kobo_uses_search_reported_rating_for_its_own_page_without_fetching(tmp_path):
    data = seed_data(tmp_path)
    calls = []
    fetcher = fetcher_with({}, calls)
    find, _ = fake_find({"kobo": {"candidates": [
        {"url": "https://www.kobo.com/tw/zh/ebook/other-book", "title": "x", "author": "", "confidence": 1.0,
         "rating": 2.0, "rating_count": 3},
        {"url": KOBO_URL, "title": "AI科學家", "author": "", "confidence": 0.5, "rating": 4.5, "rating_count": 12},
    ], "original_title": "", "original_author": ""}})
    enrich.run(data, TODAY, fetcher, find, 40)
    entry = json.loads((data / "ratings" / "ai-146.json").read_text())["platforms"]["kobo"]
    assert entry["status"] == "search_reported" and entry["score"] == 4.5 and entry["url"] == KOBO_URL
    assert not any("kobo.com" in url for url in calls)
    book = json.loads((data / "weekly-dd99-2026-w42.json").read_text())["books"][0]
    assert {"platform": "kobo", "name": "Kobo", "score": 4.5, "count": 12, "url": KOBO_URL,
            "source": "search"} in book["ratings"]


def test_kobo_without_rating_for_its_page_is_no_rating(tmp_path):
    data = seed_data(tmp_path)
    find, _ = fake_find({"kobo": found(KOBO_URL, rating=None, count=None)})
    enrich.run(data, TODAY, fetcher_with({}), find, 40)
    assert json.loads((data / "ratings" / "ai-146.json").read_text())["platforms"]["kobo"]["status"] == "no_rating"


def test_kobo_discovery_prompt_names_the_exact_page():
    sent = {}

    def post(body, key):
        sent.update(body)
        return {"output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(
            {"candidates": [], "original_title": "", "original_author": ""})}]}]}
    rating_discover.discover(BOOK, "kobo", "k", post=post)
    assert KOBO_URL in sent["input"]
    assert sent["tools"][0]["filters"]["allowed_domains"] == ["kobo.com"]


# ── 博客來 / Amazon (search only) ──────────────────────────────────────────────

BOOKS_URL = "https://www.books.com.tw/products/E050192498"
AMAZON_URL = "https://www.amazon.com/dp/B0BPQSLVL6"


@pytest.mark.parametrize("platform, raw, expected", [
    ("books", "https://www.books.com.tw/exep/assp.php/books99/products/E050192498?utm_source=x", BOOKS_URL),
    ("books", BOOKS_URL + "/", BOOKS_URL),
    ("books", "https://www.books.com.tw/search?q=x", None),
    ("amazon", "https://www.amazon.com/-/zh_TW/Dr-Fei-Fei-Li-ebook/dp/B0BPQSLVL6/ref=sr_1_1?x=1", AMAZON_URL),
    ("amazon", "https://www.amazon.co.jp/gp/product/B079Y1KWSF/ref=x", "https://www.amazon.co.jp/dp/B079Y1KWSF"),
    ("amazon", "https://www.amazon.evil.com/dp/B0BPQSLVL6", None),
])
def test_canonical_urls_drop_tracking(platform, raw, expected):
    assert rating_discover.canonical_url(rating_discover.PLATFORMS[platform], raw) == expected


def test_search_only_platforms_need_matching_title_and_are_never_fetched(tmp_path):
    data = seed_data(tmp_path)
    calls = []
    find, _ = fake_find({
        "goodreads": found(None, original_title="The Worlds I See", original_author="Fei-Fei Li"),
        "books": found(BOOKS_URL, title="AI科學家李飛飛的視界之旅", author="李飛飛", conf=0.9, rating=4.5, count=12),
        "amazon": found(AMAZON_URL, title="Some Other Book", author="Fei-Fei Li", conf=0.95, rating=4.6, count=1518),
    })
    enrich.run(data, TODAY, fetcher_with({}, calls), find, 40)
    platforms = json.loads((data / "ratings" / "ai-146.json").read_text())["platforms"]
    assert platforms["books"]["status"] == "search_reported" and platforms["books"]["score"] == 4.5
    assert platforms["amazon"]["status"] == "low_confidence"
    assert not any("books.com.tw" in c or "amazon" in c for c in calls)


def test_amazon_matches_original_title_hint(tmp_path):
    data = seed_data(tmp_path)
    find, _ = fake_find({
        "goodreads": found(None, original_title="The Worlds I See", original_author="Fei-Fei Li"),
        "amazon": found(AMAZON_URL, title="The Worlds I See: Curiosity", author="Fei-Fei Li", conf=0.9,
                        rating=4.6, count=1518),
    })
    enrich.run(data, TODAY, fetcher_with({}), find, 40)
    entry = json.loads((data / "ratings" / "ai-146.json").read_text())["platforms"]["amazon"]
    assert entry["status"] == "search_reported" and entry["url"] == AMAZON_URL


# ── kobo99 fallback ───────────────────────────────────────────────────────────

import kobo99_fallback

K99_BOOK = {"title": "AI科學家", "kobo_url": "https://www.kobo.com/tw/zh/ebook/ai-146?sId=tracking", "ratings": {
    "kobo": {"score": 4.77, "count": 13, "url": "https://www.kobo.com/tw/zh/ebook/ai-146?sId=tracking"},
    "books_com": {"score": 4.5, "count": 12, "url": "https://www.books.com.tw/exep/assp.php/books99/products/E050192498?utm=1"},
    "readmoo": {"score": 4.7, "count": 141, "url": "https://moo.im/a/8eJMOU"},
    "goodreads": {"score": None, "count": 0, "url": ""},
    "amazon_com": {"score": 9, "count": 5, "url": ""},
}}


def test_kobo99_ratings_drop_tracking_links_and_bad_numbers():
    ratings = kobo99_fallback.ratings_by_book([K99_BOOK, {"kobo_url": ""}, "junk"])
    assert ratings == {"ai-146": {
        "kobo": {"score": 4.77, "count": 13, "url": KOBO_URL},
        "books": {"score": 4.5, "count": 12, "url": BOOKS_URL},
        "readmoo": {"score": 4.7, "count": 141, "url": "https://kobo99.com/"},
    }}


def test_kobo99_ratings_accept_python_literal_strings():
    book = {**K99_BOOK, "ratings": str(K99_BOOK["ratings"])}
    assert kobo99_fallback.ratings_by_book([book])["ai-146"]["kobo"]["score"] == 4.77
    assert kobo99_fallback.ratings_by_book([{**K99_BOOK, "ratings": "not python"}]) == {}


def test_kobo99_load_reads_latest_and_promo_themes_only():
    themes = [{"file": "theme_1.json", "promo_code": "WK"}, {"file": "theme_2.json"},
              {"file": "../secret.json", "promo_code": "X"}, {"file": "theme_3.json", "promo_code": "Y", "hidden": True}]
    calls = []
    fetcher = fetcher_with({
        "https://kobo99.com/robots.txt": (200, "User-agent: *\nAllow: /\n", {}),
        "https://kobo99.com/data/latest.json": (200, json.dumps({"books": [K99_BOOK]}), {}),
        "https://kobo99.com/data/themes.json": (200, json.dumps(themes), {}),
        "https://kobo99.com/data/theme_1.json": (200, json.dumps({"books": [
            {**K99_BOOK, "kobo_url": "https://www.kobo.com/tw/zh/ebook/HB7"}]}), {}),
    }, calls)
    ratings = kobo99_fallback.load(fetcher)
    assert set(ratings) == {"ai-146", "HB7"}
    assert [c for c in calls if "theme_" in c] == ["https://kobo99.com/data/theme_1.json"]


def test_kobo99_load_survives_errors():
    assert kobo99_fallback.load(fetcher_with({"https://kobo99.com/data/latest.json": (200, "{", {})})) == {}


def test_fallback_fills_only_missing_platforms_and_persists(tmp_path):
    data = seed_data(tmp_path)
    fetcher = fetcher_with({GOODREADS_URL: (200, GOODREADS_HTML, {})})
    find, _ = fake_find({"goodreads": FOUND["goodreads"]})
    fallback = kobo99_fallback.ratings_by_book([{**K99_BOOK, "ratings": {
        **K99_BOOK["ratings"], "goodreads": {"score": 1.0, "count": 1, "url": GOODREADS_URL}}}])
    enrich.run(data, TODAY, fetcher, find, 40, fallback=fallback)
    ratings = json.loads((data / "weekly-dd99-2026-w42.json").read_text())["books"][0]["ratings"]
    assert [(r["platform"], r["source"]) for r in ratings] == [
        ("kobo", "kobo99"), ("books", "kobo99"), ("readmoo", "kobo99"), ("goodreads", "page")]
    assert next(r for r in ratings if r["platform"] == "goodreads")["score"] == 4.3  # ours wins
    # kobo99 unreachable next run: its last numbers are kept
    enrich.run(data, TODAY, fetcher, find, 40, fallback={})
    again = json.loads((data / "weekly-dd99-2026-w42.json").read_text())["books"][0]["ratings"]
    assert again == ratings


# ── run: parallel books and time budget ──────────────────────────────────────

def two_books(tmp_path):
    other = {**BOOK, "title": "中年理財", "kobo_url": "https://www.kobo.com/tw/zh/ebook/HB7"}
    return seed_data(tmp_path, books=[BOOK, other, BOOK])  # duplicate is looked up once


def test_run_enriches_each_book_once_in_parallel(tmp_path):
    data = two_books(tmp_path)
    find, calls = fake_find({})
    stats = enrich.run(data, TODAY, fetcher_with({}), find, 40, platforms=("books",), workers=4)
    assert stats["enriched"] == 2 and calls == ["books", "books"]
    assert sorted(p.name for p in (data / "ratings").iterdir()) == ["HB7.json", "ai-146.json"]


def test_run_skips_books_after_deadline_and_keeps_them_due(tmp_path):
    data = two_books(tmp_path)
    find, calls = fake_find({})
    stats = enrich.run(data, TODAY, fetcher_with({}), find, 40, platforms=("books",), deadline=0.0)
    assert stats["enriched"] == 0 and calls == []
    assert enrich.run(data, TODAY, fetcher_with({}), find, 40, platforms=("books",))["enriched"] == 2
