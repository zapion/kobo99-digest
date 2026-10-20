"""Polite HTTP fetching for rating pages.

Rules: obey robots.txt (incl. Crawl-delay), identify honestly, one request at a time per host,
and treat any bot challenge / 403 / 429 as "blocked" for this run instead of working around it.
"""

from __future__ import annotations

import http.client
import re
import threading
import time
import urllib.error
import urllib.request
import urllib.robotparser
from typing import Callable
from urllib.parse import urlsplit

USER_AGENT = "kobo99-digest/1.0 (+https://github.com/zapion/kobo99-digest)"
ROBOTS_AGENT = "kobo99-digest"
TIMEOUT_SECONDS = 30
DEFAULT_DELAY_SECONDS = 10.0
MAX_BYTES = 3_000_000
BLOCK_STATUSES = frozenset({401, 403, 429, 503})
CHALLENGE_TITLES = ("just a moment", "challenged", "robot check", "captcha", "attention required")
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


class Blocked(Exception):
    """The site refused us (status or challenge page). Do not retry this run."""


class Disallowed(Exception):
    """robots.txt disallows this URL for us."""


class NotFound(Exception):
    """The page does not exist (404/410)."""


Opener = Callable[[urllib.request.Request, float], object]


def _default_opener(request: urllib.request.Request, timeout: float):
    return urllib.request.urlopen(request, timeout=timeout)


# header -> values meaning "bot challenge" (None = any value). Cloudflare, AWS WAF (Goodreads).
CHALLENGE_HEADERS = {"cf-mitigated": None, "x-amzn-waf-action": frozenset({"challenge", "captcha", "block"})}


def _is_challenge(headers: dict, body: str) -> bool:
    for key, value in headers.items():
        expected = CHALLENGE_HEADERS.get(key.lower(), ())
        if expected is None or str(value).strip().lower() in expected:
            return True
    if not body.strip():
        return True  # e.g. AWS WAF answers 202 with an empty body
    title = TITLE_RE.search(body[:20000])
    return bool(title) and any(phrase in title[1].lower() for phrase in CHALLENGE_TITLES)


class PoliteFetcher:
    def __init__(self, opener: Opener = _default_opener, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._opener = opener
        self._sleep = sleep
        self._clock = clock
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._last_request: dict[str, float] = {}
        self._blocked_hosts: set[str] = set()
        self._lock = threading.Lock()  # one request at a time, even when books are enriched in parallel

    def _raw_get(self, url: str) -> tuple[int, dict, str]:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-TW,en;q=0.8"})
        try:
            with self._opener(request, TIMEOUT_SECONDS) as resp:
                body = resp.read(MAX_BYTES).decode("utf-8", errors="replace")
                return resp.status, dict(resp.headers), body
        except urllib.error.HTTPError as error:
            body = error.read(MAX_BYTES).decode("utf-8", errors="replace") if error.fp else ""
            return error.code, dict(error.headers or {}), body
        except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
            # timeouts, resets, partial reads: treat like a refusal and leave the host alone this run
            raise Blocked(f"{request.host} unreachable: {error!r}") from error

    def _robots_for(self, origin: str) -> urllib.robotparser.RobotFileParser:
        if origin not in self._robots:
            parser = urllib.robotparser.RobotFileParser(f"{origin}/robots.txt")
            status, _headers, body = self._raw_get(f"{origin}/robots.txt")
            if status >= 400:
                # No robots.txt (4xx) means allowed; a server error means stay away.
                parser.parse([] if status < 500 else ["User-agent: *", "Disallow: /"])
            else:
                parser.parse(body.splitlines())
            self._robots[origin] = parser
        return self._robots[origin]

    def _wait_turn(self, host: str, delay: float) -> None:
        last = self._last_request.get(host)
        if last is not None:
            remaining = delay - (self._clock() - last)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request[host] = self._clock()

    def get(self, url: str) -> str:
        with self._lock:
            return self._get(url)

    def _get(self, url: str) -> str:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise Disallowed(f"not an https URL: {url}")
        host = parts.hostname
        if host in self._blocked_hosts:
            raise Blocked(f"{host} already blocked us this run")
        robots = self._robots_for(f"https://{host}")
        if not robots.can_fetch(ROBOTS_AGENT, url):
            raise Disallowed(f"robots.txt disallows {url}")
        delay = robots.crawl_delay(ROBOTS_AGENT)
        self._wait_turn(host, float(delay) if delay else DEFAULT_DELAY_SECONDS)
        status, headers, body = self._raw_get(url)
        if status in (404, 410):
            raise NotFound(url)
        challenged = _is_challenge(headers, body)
        if status in BLOCK_STATUSES or challenged or status >= 400:
            self._blocked_hosts.add(host)
            raise Blocked(f"{host} returned {status}{' (challenge page)' if challenged else ''}")
        return body
