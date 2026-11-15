"""Read book pages through Apify, for platforms whose bot protection refuses our own fetcher.

Used only when APIFY_TOKEN is set. Kobo, 博客來 and Readmoo pages go through the Web Fetch
Actor (HTML back, parsed by rating_extract like any other page); Amazon goes through a product
Actor that returns title, author and rating as fields. Every call is paid for, so each run has
a budget: once it is spent, callers fall back to what they did before.

Web Fetch API: https://apify.com/apify/web-fetch (POST {url, formats, wait} → {fetch, metadata, html}).
"""

from __future__ import annotations

import html as html_lib
import http.client
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from rating_extract import extract_rating, page_identity
from rating_fetch import Blocked, NotFound, is_challenge

WEB_FETCH_URL = "https://web-fetch.apify.actor/"
RUN_SYNC_URL = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
AMAZON_ACTOR = "junglee~Amazon-crawler"
AMAZON_FIELDS = "title,author,stars,reviewsCount"
# Free-tier price per successful call; an estimate used for the run budget, not an invoice.
COST_USD = {"page": 0.0015, "amazon": 0.005}
DEFAULT_BUDGET_USD = 0.25
DEFAULT_PLATFORMS = ("kobo", "books", "amazon", "readmoo")
# Kobo fills in its ratings widget with JavaScript: the page has to be rendered in a browser first.
RENDER_WAIT_MS = {"kobo": 8000}
TIMEOUT_SECONDS = 150
ACTOR_TIMEOUT_SECONDS = 120
MAX_BYTES = 6_000_000
MAX_SCORE = 5

Post = Callable[[str, dict, str], object]


class ApifyError(RuntimeError):
    """The Apify call failed or returned something unusable."""


class BudgetExceeded(ApifyError):
    """This run's Apify budget is spent."""


def _post_json(url: str, body: dict, token: str):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read(MAX_BYTES).decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as error:
        detail = error.read(2000).decode("utf-8", errors="replace")[:300]
        raise ApifyError(f"Apify HTTP {error.code}: {detail}") from error
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as error:
        raise ApifyError(f"Apify unreachable: {error!r}") from error


def _with_head(payload: dict, body: str) -> str:
    """Web Fetch returns HTML without <head> and <script>; put back what the extractors read.

    The title, og:title and JSON-LD come from the response's parsed `metadata` instead.
    """
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    head = []
    if isinstance(metadata.get("title"), str):
        head.append(f"<title>{html_lib.escape(metadata['title'])}</title>")
    for tag in metadata.get("openGraph") if isinstance(metadata.get("openGraph"), list) else []:
        if isinstance(tag, dict) and tag.get("property") == "og:title" and isinstance(tag.get("content"), str):
            head.append(f'<meta property="og:title" content="{html_lib.escape(tag["content"])}" />')
    json_ld = metadata.get("jsonLd")
    for block in json_ld if isinstance(json_ld, list) else [json_ld] if json_ld else []:
        # "</" must not end the script element early; "<\/" is the same string to a JSON parser.
        text = json.dumps(block, ensure_ascii=False).replace("</", "<\\/")
        head.append(f'<script type="application/ld+json">{text}</script>')
    return "\n".join([*head, body])


def _rating(score: object, count: object) -> dict | None:
    """{"score", "count"} when both are real numbers in range, else None."""
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 < score <= MAX_SCORE:
        return None
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        return None
    return {"score": float(score), "count": count}


class ApifyReader:
    def __init__(self, token: str, budget_usd: float = DEFAULT_BUDGET_USD,
                 platforms: tuple[str, ...] = DEFAULT_PLATFORMS, post: Post = _post_json) -> None:
        self._token = token
        self._budget = budget_usd
        self._post = post
        self._lock = threading.Lock()  # books are enriched in parallel
        self.platforms = frozenset(platforms)
        self.spent = 0.0
        self.calls = 0

    def handles(self, platform: str) -> bool:
        return platform in self.platforms

    def has_budget(self, platform: str) -> bool:
        """Whether one more call for this platform still fits in the run's budget."""
        with self._lock:
            return self.spent + COST_USD["amazon" if platform == "amazon" else "page"] <= self._budget + 1e-9

    def _charge(self, kind: str) -> None:
        """Reserve the cost before calling, so parallel workers cannot overshoot the budget."""
        with self._lock:
            if self.spent + COST_USD[kind] > self._budget + 1e-9:
                raise BudgetExceeded(f"Apify budget of ${self._budget:.2f} for this run is spent")
            self.spent += COST_USD[kind]
            self.calls += 1

    def page(self, url: str, wait_ms: int | None = None) -> str:
        """HTML of one page via the Web Fetch Actor; wait_ms renders it in a browser first."""
        self._charge("page")
        body = {"url": url, "formats": ["html"], **({"wait": wait_ms} if wait_ms else {})}
        payload = self._post(WEB_FETCH_URL, body, self._token)
        if not isinstance(payload, dict):
            raise ApifyError("Web Fetch returned no JSON object")
        fetch = payload.get("fetch") if isinstance(payload.get("fetch"), dict) else {}
        status = fetch.get("httpStatusCode")
        if status in (404, 410):
            raise NotFound(url)
        html = payload.get("html")
        if not isinstance(html, str) or (isinstance(status, int) and status >= 400):
            raise Blocked(f"Apify could not read {url} (status {status})")
        page = _with_head(payload, html)
        if is_challenge({}, page):
            raise Blocked(f"Apify got a challenge page for {url}")
        return page

    def _amazon(self, url: str) -> dict:
        self._charge("amazon")
        query = urllib.parse.urlencode({"fields": AMAZON_FIELDS, "clean": "true", "timeout": ACTOR_TIMEOUT_SECONDS,
                                        "maxTotalChargeUsd": COST_USD["amazon"] * 4})
        items = self._post(f"{RUN_SYNC_URL.format(actor=AMAZON_ACTOR)}?{query}",
                           {"categoryOrProductUrls": [{"url": url}], "maxItemsPerStartUrl": 1,
                            "maxOffers": 0, "scrapeSellers": False}, self._token)
        item = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else None
        if not item or not item.get("title"):
            raise NotFound(url)
        author = str(item.get("author") or "").strip()
        return {"title": str(item["title"]).strip(), "authors": [author] if author else [],
                "rating": _rating(item.get("stars"), item.get("reviewsCount"))}

    def read(self, platform: str, url: str) -> dict:
        """{"title", "authors", "rating": {"score", "count"} | None} for one book page.

        Raises NotFound, Blocked (challenge or error page came back), BudgetExceeded or ApifyError.
        """
        if platform == "amazon":
            return self._amazon(url)
        html = self.page(url, RENDER_WAIT_MS.get(platform))
        return {**page_identity(html), "rating": extract_rating(html)}
