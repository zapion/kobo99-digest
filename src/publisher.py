#!/usr/bin/env python3
"""Publish the daily Kobo 99 page to GitHub Pages via the contents API."""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from kobo99 import DEFAULT_OUTPUT_DIR, load_digest, taipei_today
from render_html import render_day_page, render_index

ROOT = Path(__file__).resolve().parent.parent
GITHUB_API = "https://api.github.com"
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 3
REQUEST_TIMEOUT_SECONDS = 30
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
PAGE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}\.html$")


def log(message: str) -> None:
    print(message, file=sys.stderr)


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines without overriding variables already set."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def gh_request(method: str, path: str, token: str, body: dict | None = None) -> dict | list:
    request = urllib.request.Request(
        f"{GITHUB_API}{path}",
        data=json.dumps(body).encode() if body else None,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as error:
            payload = _error_payload(error)
            if error.code == 404:
                return payload
            if error.code not in RETRYABLE_STATUS:
                raise RuntimeError(f"GitHub {method} {path}: HTTP {error.code} {payload.get('message')}") from error
            last_error = error
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            last_error = error
        if attempt < MAX_RETRIES:
            delay = RETRY_DELAY_SECONDS * 2 ** (attempt - 1)
            log(f"[warn] {method} {path} failed ({last_error}); retry {attempt}/{MAX_RETRIES} in {delay}s")
            time.sleep(delay)
    raise RuntimeError(f"GitHub {method} {path} failed after {MAX_RETRIES} attempts: {last_error}")


def _error_payload(error: urllib.error.HTTPError) -> dict:
    try:
        payload = json.loads(error.read().decode(errors="replace"))
    except json.JSONDecodeError:
        return {"message": error.reason}
    return payload if isinstance(payload, dict) else {"message": str(payload)}


def put_file(repo: str, path: str, content: str, token: str, message: str) -> None:
    existing = gh_request("GET", f"/repos/{repo}/contents/{path}", token)
    body: dict = {"message": message, "content": base64.b64encode(content.encode()).decode()}
    if isinstance(existing, dict) and existing.get("sha"):
        body["sha"] = existing["sha"]
    resp = gh_request("PUT", f"/repos/{repo}/contents/{path}", token, body)
    if not isinstance(resp, dict) or "content" not in resp:
        raise RuntimeError(f"GitHub put failed for {path}: {resp}")


def collect_entries(repo: str, token: str) -> list[tuple[str, str]]:
    listing = gh_request("GET", f"/repos/{repo}/contents/", token)
    if not isinstance(listing, list):
        return []
    return [(item["name"][:10], item["name"]) for item in listing if PAGE_PATTERN.match(item.get("name", ""))]


def publish(output_dir: Path, repo: str, token: str) -> None:
    today = taipei_today()
    digest = load_digest(output_dir, today)
    if digest is None:
        raise RuntimeError(f"No fetched data in {output_dir}; run the fetcher first.")

    page_name = f"{today.isoformat()}.html"
    log(f"Publishing {page_name} to {repo}...")
    put_file(repo, page_name, render_day_page(digest), token, f"digest: {today.isoformat()}")

    entries = collect_entries(repo, token)
    if not any(name == page_name for _, name in entries):
        entries = [*entries, (today.isoformat(), page_name)]
    put_file(repo, "index.html", render_index(entries), token, f"index: update for {today.isoformat()}")

    owner, name = repo.split("/", 1)
    log(f"Published -> https://{owner}.github.io/{name}/")


def main() -> int:
    load_env_file(Path(os.getenv("KOBO99_ENV_PATH", ROOT / ".env")))
    token = os.getenv("GITHUB_TOKEN", "")
    repo = os.getenv("GITHUB_REPO", "")
    if not token or "/" not in repo:
        log("[warn] GITHUB_TOKEN or GITHUB_REPO (owner/name) not set, skipping publish.")
        return 1
    output_dir = Path(os.getenv("KOBO99_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR)))
    try:
        publish(output_dir, repo, token)
    except RuntimeError as error:
        log(f"[error] Publish failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
