# Kobo 每週書單

Static GitHub Pages site for Kobo Taiwan's two weekly lists, sourced from the official Kobo blog:

| List | Blog slug | Published (Taipei) | Period |
|---|---|---|---|
| 一週99書單 | `weekly-dd99-YYYY-wNN` | Thu 06:00 | Thu–Wed, one NT$99 book per day |
| 本週精選書單 | `weekly-8585-YYYY-wNN` | Mon 06:00 | Mon–Sun, checkout promo code |

Site: https://zapion.github.io/kobo99-digest/

## How it works

The page (`index.html`) loads `data/manifest.json`, picks the list whose period contains today's
Taipei date, and highlights today's NT$99 book in the browser; no daily job is needed. When a list's
period has passed and nothing newer exists, it shows an "expired" notice linking to the Kobo blog.

Data comes from two sources, merged per book by Kobo product id (`src/store.py`):

| Source | When | Gives |
|---|---|---|
| **Gmail (automatic)** – `src/mail_ingest.py` | launchd Thu/Mon 10:30 (+ at login) | books, list/sale prices, sale dates, promo code |
| **Bookmarklet (backup)** – `src/ingest.py` | manual | full titles, author, publisher, HK links |

The 99 email only shows each book's weekday in banner images (「星期四限定」), so those are read with
macOS Vision OCR (`scripts/ocr_bars.swift`). Tracking links in the email are never stored; product
links are rebuilt from the product id in each cover image. If ingest fails, a macOS notification asks
you to use the bookmarklet.

## Setup

1. Gmail app password (needs 2-Step Verification): https://myaccount.google.com/apppasswords →
   name it `kobo99` → copy the 16 characters into `.env`:

```bash
GMAIL_USER=you@gmail.com
GMAIL_APP_PASSWORD=abcdefghijklmnop
```

2. Test once, then install the schedule (also compiles the OCR helper into `bin/`):

```bash
python3 src/mail_ingest.py && git status --short data
scripts/install_launchagent.sh
```

Logs: `logs/auto_update.log`.

## Bookmarklet backup (Brave)

Install once: open https://zapion.github.io/kobo99-digest/bookmarklet/install.html and drag the button
to the bookmarks bar (`⌘⇧B`). On the Kobo blog post click it, then run `scripts/update.sh`.

## Development

```bash
python3 -m pytest tests --cov=src          # all tests
python3 scripts/build_bookmarklet.py       # rebuild install.html after editing extract.js
python3 -m http.server 8099                # preview; add ?date=YYYY-MM-DD to simulate a day
python3 src/mail_ingest.py --eml a.eml      # parse saved emails without Gmail
```

Git push auth uses a repo-local credential helper that reads `GITHUB_TOKEN` from `.env`
(fine-grained PAT, Contents read/write on this repo only).
