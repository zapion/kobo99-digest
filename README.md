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

Data comes from two sources, merged per book by Kobo product id:

| Source | When | Gives |
|---|---|---|
| **Apps Script (automatic)** – `appsscript/Code.gs` | Google time triggers, Thu/Mon ~10:30 Taipei | books, list/sale prices, sale dates, promo code |
| **Bookmarklet (backup)** – `src/ingest.py` | manual | full titles, author, publisher, HK links |

The Apps Script reads the latest 【今日 99】/【本週精選】 newsletter from Gmail (read-only), reads the
99 email's weekday banners (「星期四限定」) with Drive OCR, merges into `data/*.json` through the
GitHub contents API, and commits only when something changed. Tracking links are never stored;
product links are rebuilt from the product id in each cover image. On failure the trigger throws,
and Google emails you: use the bookmarklet backup that week.

Merge rules live in both `appsscript/Code.gs` (`mergeLists`) and `src/store.py`; keep them in sync.

## Apps Script setup

1. https://script.google.com → New project → Project Settings → tick "Show appsscript.json".
2. Paste `appsscript/appsscript.json` and `appsscript/Code.gs` (replace the defaults).
   Scopes are limited to `gmail.readonly`, `drive.file` (only its own temp OCR docs),
   external requests, and triggers.
3. Project Settings → Script properties → `GITHUB_TOKEN` = fine-grained PAT with
   Contents read/write on `zapion/kobo99-digest` only.
4. Run `runIngest` once (authorise), check the log, then run `installTriggers` once.
5. Triggers page → each trigger → failure notifications: "Notify me immediately".

## Bookmarklet backup (Brave)

Install once: open https://zapion.github.io/kobo99-digest/bookmarklet/install.html and drag the button
to the bookmarks bar (`⌘⇧B`). On the Kobo blog post click it, then run `scripts/update.sh`.

## Development

```bash
python3 -m pytest tests --cov=src          # bookmarklet ingest + store
node --test appsscript/test                 # Apps Script logic (fake Apps Script globals)
python3 scripts/build_bookmarklet.py       # rebuild install.html after editing extract.js
python3 -m http.server 8099                # preview; add ?date=YYYY-MM-DD to simulate a day
```

Local git push (bookmarklet backup) uses a repo-local credential helper that reads `GITHUB_TOKEN` from `.env`
(fine-grained PAT, Contents read/write on this repo only).
