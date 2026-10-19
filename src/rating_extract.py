"""Pull ratings and book identity out of book pages via structured data, plus title matching.

JSON-LD (Goodreads) is tried first, then schema.org microdata (Readmoo). Both are far more
stable than CSS selectors; if neither is present the page is treated as having no rating.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import unicodedata

LD_JSON_RE = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)
MICRO_VALUE_RE = re.compile(r'itemprop="ratingValue"[^>]*?(?:content|data-score)="([\d.]+)"', re.I)
MICRO_COUNT_RE = re.compile(r'itemprop="ratingCount"[^>]*>\s*([\d,]+)\s*<', re.I)
OG_TITLE_RE = re.compile(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', re.I)
READMOO_OG_RE = re.compile(r"^(.*?)\s+-\s+(.*?)\s*\|\s*Readmoo", re.S)
SUBTITLE_SPLIT_RE = re.compile(r"[：:（(【\[—–\-]")
NON_WORD_RE = re.compile(r"[\W_]+", re.UNICODE)
MIN_TITLE_CHARS = 2


def _ld_objects(html: str) -> list[dict]:
    objects: list[dict] = []
    for block in LD_JSON_RE.findall(html):
        try:
            data = json.loads(html_lib.unescape(block.strip()))
        except json.JSONDecodeError:
            continue
        stack = data if isinstance(data, list) else [data]
        for item in stack:
            if isinstance(item, dict):
                objects.append(item)
                objects.extend(x for x in item.get("@graph", []) if isinstance(x, dict))
    return objects


def _number(value: object) -> float | None:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def extract_rating(html: str) -> dict | None:
    """Return {"score": float, "count": int} or None."""
    for obj in _ld_objects(html):
        agg = obj.get("aggregateRating")
        if isinstance(agg, dict) and _number(agg.get("ratingValue")) is not None:
            count = _number(agg.get("ratingCount")) or _number(agg.get("reviewCount")) or 0
            return {"score": _number(agg["ratingValue"]), "count": int(count)}
    value = MICRO_VALUE_RE.search(html)
    if value:
        count = MICRO_COUNT_RE.search(html)
        return {"score": float(value[1]), "count": int(count[1].replace(",", "")) if count else 0}
    return None


def page_identity(html: str) -> dict:
    """Best-effort {"title": str, "authors": [str]} of the book on the page."""
    for obj in _ld_objects(html):
        if obj.get("@type") == "Book" and obj.get("name"):
            raw = obj.get("author") or []
            authors = [a.get("name", "") for a in (raw if isinstance(raw, list) else [raw]) if isinstance(a, dict)]
            return {"title": html_lib.unescape(str(obj["name"])), "authors": [a for a in authors if a]}
    og = OG_TITLE_RE.search(html)
    if og:
        text = html_lib.unescape(og[1])
        readmoo = READMOO_OG_RE.match(text)
        if readmoo:
            return {"title": readmoo[1].strip(), "authors": [readmoo[2].strip()]}
        return {"title": text.strip(), "authors": []}
    return {"title": "", "authors": []}


def normalize(text: str) -> str:
    """Width-fold, lowercase, drop punctuation/spaces: '《霍金傳》 ' -> '霍金傳'."""
    return NON_WORD_RE.sub("", unicodedata.normalize("NFKC", text or "").lower())


def main_title(text: str) -> str:
    """Title without subtitle: 'AI科學家：視界之旅' -> 'AI科學家'."""
    head = SUBTITLE_SPLIT_RE.split(text or "", maxsplit=1)[0]
    return normalize(head) or normalize(text)


def titles_match(expected: str, found: str) -> bool:
    a, b = main_title(expected), main_title(found)
    if len(a) < MIN_TITLE_CHARS or len(b) < MIN_TITLE_CHARS:
        return False
    return a in normalize(found) or b in normalize(expected)


def authors_match(expected: str, found: list[str]) -> bool:
    """True when any found author name appears in the expected author string or vice versa."""
    exp = normalize(expected)
    if not exp:
        return True  # nothing to contradict
    for name in found:
        cand = normalize(name)
        if cand and (cand in exp or exp in cand):
            return True
        # 'Fei-Fei Li' vs '李飛飛（Fei-Fei Li）': compare the latin part too
        latin = normalize(re.sub(r"[^A-Za-z ]", " ", name))
        if latin and len(latin) > 3 and latin in exp:
            return True
    return False
