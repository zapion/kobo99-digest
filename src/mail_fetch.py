"""Fetch recent Kobo newsletter emails from Gmail over IMAP (read-only, nothing is marked read)."""

from __future__ import annotations

import imaplib
import re

IMAP_HOST = "imap.gmail.com"
KOBO_SENDER = "news@shopjp.kobo.com"
ALL_MAIL_FLAG = "\\All"
MAILBOX_NAME_RE = re.compile(r'"([^"]+)"\s*$')


class MailFetchError(RuntimeError):
    """Gmail login, mailbox lookup or fetch failed."""


def _all_mail_mailbox(imap: imaplib.IMAP4_SSL) -> str:
    """Gmail's 'All Mail' folder name is localised, so find it by its \\All flag."""
    _typ, lines = imap.list()
    for raw in lines or []:
        line = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
        match = MAILBOX_NAME_RE.search(line)
        if ALL_MAIL_FLAG in line and match:
            return f'"{match[1]}"'
    return "INBOX"


def fetch_kobo_emails(user: str, app_password: str, days: int) -> list[bytes]:
    try:
        with imaplib.IMAP4_SSL(IMAP_HOST) as imap:
            imap.login(user, app_password)
            typ, _ = imap.select(_all_mail_mailbox(imap), readonly=True)
            if typ != "OK":
                raise MailFetchError("Cannot open the All Mail folder.")
            query = f'"from:{KOBO_SENDER} newer_than:{days}d"'
            typ, data = imap.search(None, "X-GM-RAW", query)
            if typ != "OK":
                raise MailFetchError(f"Gmail search failed: {data!r}")
            messages = []
            for message_id in data[0].split():
                typ, parts = imap.fetch(message_id, "(BODY.PEEK[])")
                if typ == "OK" and parts and isinstance(parts[0], tuple):
                    messages.append(parts[0][1])
            return messages
    except imaplib.IMAP4.error as error:
        raise MailFetchError(f"Gmail IMAP error: {error}") from error
    except OSError as error:
        raise MailFetchError(f"Cannot reach {IMAP_HOST}: {error}") from error
