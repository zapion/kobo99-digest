"""Find candidate book-page URLs on rating sites with Claude's web search tool (Messages API).

The model proposes candidate URLs, says how sure it is that each is the same book, and reports the
rating it saw in search results. enrich.py fetches and verifies each page itself; the reported
rating is only used (and labelled "search") when the page cannot be fetched.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-5-5"
WEB_SEARCH_TOOL = "web_search_20250305"
REPORT_TOOL = "report_candidates"
MAX_SEARCHES = 4  # per book and platform; $10 per 1,000 searches
MAX_TOKENS = 2048
MAX_TURNS = 4  # pause_turn continuations plus one nudge to report
TIMEOUT_SECONDS = 120
MAX_ATTEMPTS = 3
RETRY_BASE_SECONDS = 5
RETRYABLE = frozenset({429, 500, 502, 503, 504})
MAX_CANDIDATES = 3
MAX_HINT_CHARS = 120
# Models sometimes stuff explanations into fields; such hints are dropped.
HINT_NOISE = ("readmoo", "goodreads", "not found", "未在", "無法", "找不到", "unknown", "n/a")


@dataclass(frozen=True)
class Platform:
    key: str
    name: str
    domains: tuple[str, ...]
    url_re: re.Pattern
    hint: str  # "{kobo_url}" is replaced with the book's Kobo page
    # False: never fetch the page ourselves (bot protection); only a search-reported rating is used.
    fetchable: bool = True
    # Canonical URL built from url_re groups; None keeps the URL minus query/fragment.
    canonical: str | None = None


PLATFORMS = {
    "readmoo": Platform(
        "readmoo", "讀墨 Readmoo", ("readmoo.com",), re.compile(r"^https://readmoo\.com/book/(\d{6,})$"),
        "the Traditional Chinese ebook edition on readmoo.com (URL form https://readmoo.com/book/<digits>)"),
    "goodreads": Platform(
        "goodreads", "Goodreads", ("goodreads.com",), re.compile(r"^https://www\.goodreads\.com/book/show/(\d+)"),
        "the book on goodreads.com, preferring the original-language edition with the most ratings "
        "(URL form https://www.goodreads.com/book/show/<id>)"),
    "kobo": Platform(
        "kobo", "Kobo", ("kobo.com",), re.compile(r"^https://www\.kobo\.com/tw/zh/ebook/([^/?#]+)$"),
        "the Kobo Taiwan page {kobo_url} itself and the star rating and number of ratings shown on it",
        fetchable=False),
    "books": Platform(
        "books", "博客來", ("books.com.tw",),
        re.compile(r"^https://www\.books\.com\.tw/(?:[^?#]*/)?products/([A-Z0-9]{10})(?:/|$)"),
        "the ebook (or else print) edition of this Traditional Chinese book on books.com.tw "
        "(URL form https://www.books.com.tw/products/<id>)",
        fetchable=False, canonical="https://www.books.com.tw/products/{0}"),
    "amazon": Platform(
        "amazon", "Amazon", ("amazon.com", "amazon.co.jp"),
        re.compile(r"^https://www\.amazon\.(com|co\.jp)/(?:[^?#]*/)?(?:dp|gp/product)/([A-Z0-9]{10})(?:/|$)"),
        "the book on amazon.com (or amazon.co.jp for Japanese originals): the original-language edition for "
        "translations, else the Chinese edition (URL form https://www.amazon.com/dp/<ASIN>)",
        fetchable=False, canonical="https://www.amazon.{0}/dp/{1}"),
}

SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "title": {"type": "string", "description": "title exactly as shown on that page"},
                    "author": {"type": "string", "description": "author exactly as shown on that page"},
                    "same_book_confidence": {"type": "number", "description": "0-1: same work as the Kobo book"},
                    "rating": {"type": ["number", "null"], "description": "average rating shown for that page, or null"},
                    "rating_count": {"type": ["integer", "null"], "description": "number of ratings shown, or null"},
                },
                "required": ["url", "title", "author", "same_book_confidence", "rating", "rating_count"],
                "additionalProperties": False,
            },
        },
        "original_title": {"type": "string", "description": "title in the original language/script, or empty"},
        "original_author": {"type": "string", "description": "author in the original language/script, or empty"},
    },
    "required": ["candidates", "original_title", "original_author"],
    "additionalProperties": False,
}


class DiscoveryError(RuntimeError):
    """The Claude call failed or returned something unusable."""


def canonical_url(platform: Platform, url: str) -> str | None:
    """Strip query/fragment and keep only allowlisted book URLs."""
    base = url.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    match = platform.url_re.match(base)
    if not match:
        return None
    return platform.canonical.format(*match.groups()) if platform.canonical else base


def _prompt(book: dict, platform: Platform) -> str:
    main_title = re.split(r"[：:（(【]", book.get("title", ""), maxsplit=1)[0].strip()
    return (
        f"Find {platform.hint.replace('{kobo_url}', book.get('kobo_url', ''))}.\n"
        f"Book (Traditional Chinese edition sold on Kobo Taiwan):\n"
        f"- title: {book.get('title', '')}\n- short title: {main_title}\n- author: {book.get('author', '')}\n"
        f"- publisher: {book.get('publisher', '')}\n"
        f"Search with the short title and the author. Translated books often use a different title on "
        f"{' / '.join(platform.domains)}; search the original title too.\n"
        f"Return up to {MAX_CANDIDATES} book-page URLs exactly as found, best first. For each: the title and author "
        f"shown on that page, your confidence (0-1) that it is the same work, and the average rating and number of "
        f"ratings shown for it in the search results or page (null if you did not see them; never estimate).\n"
        f"original_title / original_author: the book's original title and author in the original language and "
        f"script (e.g. English or Japanese for translations, the Chinese title for books written in Chinese); "
        f"empty string if unsure. Put no notes or explanations in any field. Return no candidates rather than guessing.\n"
        f"Use only URLs that appeared in your search results. When done, call {REPORT_TOOL} once."
    )


def _clean_hint(value: object) -> str:
    text = str(value or "").strip()
    if not text or len(text) > MAX_HINT_CHARS or any(noise in text.lower() for noise in HINT_NOISE):
        return ""
    return text


def _number_or_none(value: object, kind=float):
    try:
        number = kind(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _report(content: list) -> dict | None:
    for block in content:
        if block.get("type") == "tool_use" and block.get("name") == REPORT_TOOL:
            return block.get("input") if isinstance(block.get("input"), dict) else None
    return None


def _result_urls(platform: Platform, content: list) -> set[str]:
    """Canonical URLs of every search result Claude was shown."""
    urls = set()
    for block in content:
        results = block.get("content") if block.get("type") == "web_search_tool_result" else None
        for result in results if isinstance(results, list) else []:
            if canonical := canonical_url(platform, str(result.get("url", ""))):
                urls.add(canonical)
    return urls


def _post(body: dict, api_key: str) -> dict:
    data = json.dumps(body).encode()
    last: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        request = urllib.request.Request(API_URL, data=data, method="POST", headers={
            "x-api-key": api_key, "anthropic-version": API_VERSION, "content-type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:300]
            last = DiscoveryError(f"Claude API HTTP {error.code}: {detail}")
            if error.code not in RETRYABLE:
                raise last from error
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as error:
            # dropped/partial responses (IncompleteRead, resets, truncated JSON) are worth another try
            last = DiscoveryError(f"Claude API unreachable: {error!r}")
        if attempt < MAX_ATTEMPTS:
            time.sleep(RETRY_BASE_SECONDS * attempt)
    raise last or DiscoveryError("Claude API call failed")


def _ask(platform: Platform, prompt: str, api_key: str, model: str, post) -> tuple[dict, set[str]]:
    """Let Claude search, then collect its report_candidates call and the URLs it was shown."""
    tools = [
        {"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": MAX_SEARCHES, "allowed_domains": list(platform.domains)},
        {"name": REPORT_TOOL, "description": "Report the book pages found.", "input_schema": SCHEMA, "strict": True},
    ]
    messages, seen, forced = [{"role": "user", "content": prompt}], set(), False
    for _turn in range(MAX_TURNS):
        body = {"model": model, "max_tokens": MAX_TOKENS, "messages": messages, "tools": tools}
        if forced:
            body["tool_choice"] = {"type": "tool", "name": REPORT_TOOL}
        response = post(body, api_key)
        content = response.get("content") if isinstance(response.get("content"), list) else []
        seen |= _result_urls(platform, content)
        report = _report(content)
        if report is not None:
            return report, seen
        messages = [*messages, {"role": "assistant", "content": content}]
        if response.get("stop_reason") != "pause_turn":  # finished without reporting: ask for the report
            messages.append({"role": "user", "content": f"Call {REPORT_TOOL} now (empty list if nothing was found)."})
            forced = True
    raise DiscoveryError(f"Claude did not call {REPORT_TOOL}")


def discover(book: dict, platform_key: str, api_key: str, model: str | None = None, post=_post) -> dict:
    """Return {"candidates": [{url, title, author, confidence, rating, rating_count}], "original_title", "original_author"}.

    Candidate URLs are allowlisted, canonical and must have appeared in the search results;
    hints that look like notes are dropped.
    """
    platform = PLATFORMS[platform_key]
    result, seen = _ask(platform, _prompt(book, platform), api_key,
                        model or os.getenv("CLAUDE_MODEL") or DEFAULT_MODEL, post)
    candidates: list[dict] = []
    for raw in result.get("candidates", [])[:MAX_CANDIDATES]:
        if not isinstance(raw, dict):
            continue
        url = canonical_url(platform, str(raw.get("url", "")))
        if not url or url not in seen or any(c["url"] == url for c in candidates):
            continue
        rating = _number_or_none(raw.get("rating"))
        candidates.append({
            "url": url,
            "title": str(raw.get("title", "")).strip(),
            "author": str(raw.get("author", "")).strip(),
            "confidence": min(max(_number_or_none(raw.get("same_book_confidence")) or 0.0, 0.0), 1.0),
            "rating": rating if rating is not None and rating <= 5 else None,
            "rating_count": _number_or_none(raw.get("rating_count"), int),
        })
    return {
        "candidates": candidates,
        "original_title": _clean_hint(result.get("original_title")),
        "original_author": _clean_hint(result.get("original_author")),
    }
