#!/usr/bin/env python3
"""Ingest the latest Kobo list emails (精選 + 99) into data/. Prints the updated slugs.

Usage:
  python3 src/mail_ingest.py                 # Gmail IMAP, needs GMAIL_USER / GMAIL_APP_PASSWORD in .env
  python3 src/mail_ingest.py --eml a.eml ... # offline, from saved .eml files
"""

from __future__ import annotations

import argparse
import email
import os
import sys
from email import policy
from pathlib import Path

from mail_fetch import MailFetchError, fetch_kobo_emails
from mail_ocr import OcrError, read_bars
from mail_parse import MailParseError, classify, parse_email, sent_date
from store import write_list

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
ENV_PATH = ROOT / ".env"
LOOKBACK_DAYS = 8


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines without overriding variables already set."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def latest_per_kind(raw_messages: list[bytes]) -> dict[str, bytes]:
    """Keep only the newest 精選 and 99 email; other Kobo mail is ignored."""
    newest: dict[str, tuple[str, bytes]] = {}
    for raw in raw_messages:
        message = email.message_from_bytes(raw, policy=policy.default)
        kind = classify(message)
        if kind is None:
            continue
        try:
            sent = sent_date(message).isoformat()
        except MailParseError:
            continue
        if kind not in newest or sent > newest[kind][0]:
            newest[kind] = (sent, raw)
    return {kind: raw for kind, (_sent, raw) in newest.items()}


def _load_messages(eml_paths: list[Path]) -> list[bytes]:
    if eml_paths:
        return [path.read_bytes() for path in eml_paths]
    load_env_file(Path(os.getenv("KOBO99_ENV_PATH", str(ENV_PATH))))
    user, password = os.getenv("GMAIL_USER", ""), os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
    if not user or not password:
        raise MailFetchError("GMAIL_USER / GMAIL_APP_PASSWORD missing in .env")
    return fetch_kobo_emails(user, password, LOOKBACK_DAYS)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--eml", nargs="+", type=Path, default=[], help="parse saved .eml files instead of Gmail")
    args = parser.parse_args(argv)
    try:
        latest = latest_per_kind(_load_messages(args.eml))
    except (MailFetchError, OSError) as error:
        print(f"[error] {error}", file=sys.stderr)
        return 1
    if not latest:
        print(f"[warn] No Kobo 精選/99 email in the last {LOOKBACK_DAYS} days.", file=sys.stderr)
        return 1
    failed = False
    for kind, raw in sorted(latest.items()):
        try:
            stored = write_list(parse_email(raw, read_bars), DATA_DIR)
        except (MailParseError, OcrError) as error:
            print(f"[error] {kind} email: {error}", file=sys.stderr)
            failed = True
            continue
        print(f"Ingested {stored['slug']} ({len(stored['books'])} books) from email", file=sys.stderr)
        print(stored["slug"])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
