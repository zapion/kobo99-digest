"""Find candidate book-page URLs on rating sites with OpenAI web search (Responses API).

The model only *proposes* URLs; nothing it says is stored as a rating. Every candidate must match
the platform's URL allowlist and is then fetched and verified by our own code (enrich.py).
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


@dataclass(frozen=True)
class Platform:
    key: str
    name: str
    domain: str
    url_re: re.Pattern
    hint: str


PLATFORMS = {
    "readmoo": Platform(
        "readmoo", "讀墨 Readmoo", "readmoo.com", re.compile(r"^https://readmoo\.com/book/(\d{6,})$"),
        "the Traditional Chinese ebook edition on readmoo.com (URL form https://readmoo.com/book/<digits>)"),
    "goodreads": Platform(
        "goodreads", "Goodreads", "goodreads.com", re.compile(r"^https://www\.goodreads\.com/book/show/(\d+)"),
        "the book on goodreads.com, preferring the original-language edition with the most ratings "
        "(URL form https://www.goodreads.com/book/show/<id>)"),
}

SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"url": {"type": "string"}, "title": {"type": "string"}, "author": {"type": "string"}},
                "required": ["url", "title", "author"],
                "additionalProperties": False,
            },
        },
        "original_title": {"type": "string"},
        "original_author": {"type": "string"},
    },
    "required": ["candidates", "original_title", "original_author"],
    "additionalProperties": False,
}


class DiscoveryError(RuntimeError):
    """OpenAI call failed or returned something unusable."""


def canonical_url(platform: Platform, url: str) -> str | None:
    """Strip query/fragment and keep only allowlisted book URLs."""
    base = url.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    return base if platform.url_re.match(base) else None


def _prompt(book: dict, platform: Platform) -> str:
    return (
        f"Find {platform.hint}.\n"
        f"Book (Traditional Chinese edition sold on Kobo Taiwan):\n"
        f"- title: {book.get('title', '')}\n- author: {book.get('author', '')}\n"
        f"- publisher: {book.get('publisher', '')}\n"
        f"Return up to {MAX_CANDIDATES} candidate book-page URLs exactly as found, best first, with the title and "
        f"author shown on that page. Also give the book's original title and author in the original language "
        f"(same as given if it was written in Chinese). Return no candidates rather than guessing."
    )


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
    """Return {"urls": [allowlisted canonical URLs], "original_title": str, "original_author": str}."""
    platform = PLATFORMS[platform_key]
    body = {
        "model": model or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL,
        "input": _prompt(book, platform),
        "tools": [{"type": "web_search", "filters": {"allowed_domains": [platform.domain]}}],
        "text": {"format": {"type": "json_schema", "name": "book_pages", "schema": SCHEMA, "strict": True}},
    }
    try:
        result = json.loads(_output_text(post(body, api_key)))
    except json.JSONDecodeError as error:
        raise DiscoveryError(f"OpenAI returned invalid JSON: {error}") from error
    urls: list[str] = []
    for candidate in result.get("candidates", [])[:MAX_CANDIDATES]:
        url = canonical_url(platform, str(candidate.get("url", "")))
        if url and url not in urls:
            urls.append(url)
    return {
        "urls": urls,
        "original_title": str(result.get("original_title", "")).strip(),
        "original_author": str(result.get("original_author", "")).strip(),
    }
