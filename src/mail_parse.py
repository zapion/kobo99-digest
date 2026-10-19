"""Parse Kobo Taiwan newsletter emails (【本週精選】 / 【今日 99】) into book lists.

The templates tag fields with ids like ``book3-title`` / ``book3-sell-price``. Personalised
tracking links (shopjp.kobo.com/pub/cc) are never stored; product links are rebuilt from the
Kobo product id embedded in each cover image file name.

The 99 email only states each book's sale weekday inside banner images (bar1.jpg: 「星期四限定」
「星期五限定」), one banner per row of two books, so an OCR callback reads them.
"""

from __future__ import annotations

import datetime as dt
import email
import re
from email import policy
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
SENDER_DOMAIN_RE = re.compile(r"@(?:[\w-]+\.)*kobo\.com$", re.I)
SUBJECT_KINDS = (("【本週精選】", "8585"), ("【今日 99】", "dd99"), ("【今日99】", "dd99"))
KIND_LABELS = {"dd99": "一週99書單", "8585": "本週精選書單"}
BOOK_FIELD_RE = re.compile(r"^book(\d+)-(title|list-price|sell-price|description|img-image-link)$")
COVER_RE = re.compile(r"^https://cdn\.kobo\.com/book-images/[\w-]+/\d+/\d+/\w+/([A-Za-z0-9_-]+)\.(?:jpg|png)$")
BAR_RE = re.compile(r"/images/bar\d+\.(?:jpg|png)$")
BAR_HOSTS = frozenset({"shopjp.kobo.com", "static.cdn.responsys.net"})
NUMBER_RE = re.compile(r"(\d[\d,]*)\s*$")
PERIOD_RE = re.compile(r"(\d{1,2})/(\d{1,2})\s*[-－~～]\s*(\d{1,2})/(\d{1,2})")
PROMO_RE = re.compile(r"輸入\s*([A-Za-z]+\d+)")
WEEKDAY_RE = re.compile(r"星期([一二三四五六日天])")
WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
KOBO_EBOOK_URL = "https://www.kobo.com/tw/zh/ebook/{}"
COVER_SIZE_RE = re.compile(r"/\d+/\d+/(\w+)/([^/]+)$")
COVER_SIZE = 300
BLOG_URL = "https://www.kobo.com/zh/blog/{}"

# OCR callback: bar image URLs -> recognised text per image.
BarReader = Callable[[list[str]], list[str]]


class MailParseError(ValueError):
    """The email is not a Kobo list email, or its layout changed."""


class _EmailHTML(HTMLParser):
    """Collect id-tagged book fields, bar image order and visible text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: dict[tuple[int, str], str] = {}
        self.sequence: list[tuple[str, object]] = []
        self.text_parts: list[str] = []
        self._capture: list[tuple[str, tuple[int, str] | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        match = BOOK_FIELD_RE.match(attr.get("id") or "")
        key = (int(match[1]), match[2]) if match else None
        if tag == "img":
            src = attr.get("src") or ""
            if key:
                self.fields[key] = src
            elif BAR_RE.search(urlsplit(src).path):
                self.sequence.append(("bar", src))
            return
        if key and key[1] == "title":
            self.sequence.append(("book", key[0]))
        self._capture.append((tag, key))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._capture) - 1, -1, -1):
            if self._capture[index][0] == tag:
                del self._capture[index:]
                return

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)
        for _tag, key in self._capture:
            if key:
                self.fields[key] = (self.fields.get(key, "") + data)

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.text_parts))


def classify(message: email.message.EmailMessage) -> str | None:
    """Return 'dd99' / '8585' for Kobo list emails, else None."""
    sender = parseaddr(str(message.get("From", "")))[1]
    if not SENDER_DOMAIN_RE.search(sender):
        return None
    subject = str(message.get("Subject", ""))
    return next((kind for prefix, kind in SUBJECT_KINDS if subject.startswith(prefix)), None)


def sent_date(message: email.message.EmailMessage) -> dt.date:
    try:
        return parsedate_to_datetime(str(message["Date"])).astimezone(TAIPEI).date()
    except (TypeError, ValueError) as error:
        raise MailParseError(f"Unusable Date header: {message.get('Date')!r}") from error


def _number(text: str) -> int | None:
    match = NUMBER_RE.search(text.strip())
    return int(match[1].replace(",", "")) if match else None


def _books(page: _EmailHTML) -> list[dict]:
    numbers = sorted({n for n, _ in page.fields})
    books = []
    for n in numbers:
        cover = page.fields.get((n, "img-image-link"), "")
        cover_match = COVER_RE.match(cover)
        title = page.fields.get((n, "title"), "").strip()
        if not title or not cover_match:
            raise MailParseError(f"book{n}: missing title or Kobo cover URL ({cover[:80]!r})")
        books.append({
            "number": n,
            "title": title,
            "description": page.fields.get((n, "description"), "").strip(),
            "kobo_url": KOBO_EBOOK_URL.format(cover_match[1]),
            "cover_url": COVER_SIZE_RE.sub(rf"/{COVER_SIZE}/{COVER_SIZE}/\1/\2", cover),
            "list_price": _number(page.fields.get((n, "list-price"), "")),
            "sale_price": _number(page.fields.get((n, "sell-price"), "")),
        })
    if not books:
        raise MailParseError("No books found; the email template may have changed.")
    return books


def _bar_groups(page: _EmailHTML) -> list[tuple[str, list[int]]]:
    groups: list[tuple[str, list[int]]] = []
    for kind, value in page.sequence:
        if kind == "bar":
            groups.append((str(value), []))
        elif groups:
            groups[-1][1].append(int(value))
    for url, _ in groups:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in BAR_HOSTS:
            raise MailParseError(f"Unexpected banner image host: {url!r}")
    return groups


def _period_near(text: str, sent: dt.date) -> tuple[dt.date, dt.date]:
    match = PERIOD_RE.search(text)
    if not match:
        raise MailParseError("No M/D-M/D period in the 99 email.")
    m1, d1, m2, d2 = (int(x) for x in match.groups())
    start = min((dt.date(y, m1, d1) for y in (sent.year - 1, sent.year, sent.year + 1)),
                key=lambda d: abs((d - sent).days))
    end = dt.date(start.year if (m2, d2) >= (m1, d1) else start.year + 1, m2, d2)
    return start, end


def _sale_dates(page: _EmailHTML, start: dt.date, end: dt.date, read_bars: BarReader) -> dict[int, str]:
    groups = _bar_groups(page)
    if not groups:
        raise MailParseError("No date banners found in the 99 email.")
    texts = read_bars([url for url, _ in groups])
    dates: dict[int, str] = {}
    for (url, numbers), text in zip(groups, texts, strict=True):
        weekdays = [WEEKDAYS[c] for c in WEEKDAY_RE.findall(text)]
        if len(weekdays) != len(numbers):
            raise MailParseError(f"Banner {url.rsplit('/', 1)[-1]} read as {text!r}; expected {len(numbers)} weekdays")
        for n, weekday in zip(numbers, weekdays):
            day = start + dt.timedelta(days=(weekday - start.weekday()) % 7)
            if day > end:
                raise MailParseError(f"Weekday {weekday} falls outside {start}..{end}")
            dates[n] = day.isoformat()
    return dates


def _slug(kind: str, start: dt.date) -> str:
    iso = start.isocalendar()
    return f"weekly-{kind}-{iso.year}-w{iso.week}"


def _list(kind: str, start: dt.date, end: dt.date, subject: str, books: list[dict], promo: str | None) -> dict:
    slug = _slug(kind, start)
    return {
        "slug": slug,
        "kind": kind,
        "label": KIND_LABELS[kind],
        "url": BLOG_URL.format(slug),
        "title": subject,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "promo_code": promo,
        "books": books,
    }


def parse_email(raw: bytes, read_bars: BarReader) -> dict:
    message = email.message_from_bytes(raw, policy=policy.default)
    kind = classify(message)
    if kind is None:
        raise MailParseError(f"Not a Kobo list email: {message.get('Subject')!r}")
    body = message.get_body(("html",))
    if body is None:
        raise MailParseError("Email has no HTML part.")
    page = _EmailHTML()
    page.feed(body.get_content())
    books = _books(page)
    sent = sent_date(message)
    subject = str(message.get("Subject", ""))

    if kind == "8585":
        start = sent - dt.timedelta(days=sent.weekday())
        promo = PROMO_RE.search(page.text)
        ranked = [{**{k: v for k, v in b.items() if k != "number"}, "rank": b["number"]} for b in books]
        return _list(kind, start, start + dt.timedelta(days=6), subject, ranked, promo[1] if promo else None)

    start, end = _period_near(page.text, sent)
    dates = _sale_dates(page, start, end, read_bars)
    missing = [b["number"] for b in books if b["number"] not in dates]
    if missing:
        raise MailParseError(f"No sale date banner for books {missing}")
    dated = [{**{k: v for k, v in b.items() if k != "number"}, "sale_date": dates[b["number"]]} for b in books]
    return _list(kind, start, end, subject, dated, None)
