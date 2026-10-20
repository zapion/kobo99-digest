"""Find candidate book-page URLs on rating sites with OpenAI web search (Responses API).

The model proposes candidate URLs, says how sure it is that each is the same book, and reports the
rating it saw in search results. enrich.py fetches and verifies each page itself; the reported
rating is only used (and labelled "search") when the page cannot be fetched.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

OPENAI_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5-mini"
TIMEOUT_SECONDS = 180
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
    """OpenAI call failed or returned something unusable."""


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
        f"empty string if unsure. Put no notes or explanations in any field. Return no candidates rather than guessing."
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


def _output_text(payload: dict) -> str:
    for item in payload.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    return part.get("text", "")
    raise DiscoveryError("OpenAI response had no output_text")


def _post(body: dict, api_key: str) -> dict:
    data = json.dumps(body).encode()
    last: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        request = urllib.request.Request(OPENAI_URL, data=data, method="POST", headers={
            "Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:300]
            last = DiscoveryError(f"OpenAI HTTP {error.code}: {detail}")
            if error.code not in RETRYABLE:
                raise last from error
        except (urllib.error.URLError, TimeoutError) as error:
            last = DiscoveryError(f"OpenAI unreachable: {error}")
        if attempt < MAX_ATTEMPTS:
            time.sleep(RETRY_BASE_SECONDS * attempt)
    raise last or DiscoveryError("OpenAI call failed")


def discover(book: dict, platform_key: str, api_key: str, model: str | None = None, post=_post) -> dict:
    """Return {"candidates": [{url, title, author, confidence, rating, rating_count}], "original_title", "original_author"}.

    Candidate URLs are allowlisted and canonical; hints that look like notes are dropped.
    """
    platform = PLATFORMS[platform_key]
    body = {
        "model": model or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL,
        "input": _prompt(book, platform),
        "tools": [{"type": "web_search", "filters": {"allowed_domains": list(platform.domains)}}],
        "text": {"format": {"type": "json_schema", "name": "book_pages", "schema": SCHEMA, "strict": True}},
    }
    try:
        result = json.loads(_output_text(post(body, api_key)))
    except json.JSONDecodeError as error:
        raise DiscoveryError(f"OpenAI returned invalid JSON: {error}") from error
    candidates: list[dict] = []
    for raw in result.get("candidates", [])[:MAX_CANDIDATES]:
        url = canonical_url(platform, str(raw.get("url", "")))
        if not url or any(c["url"] == url for c in candidates):
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
