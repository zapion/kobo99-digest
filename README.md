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

Pipeline:

```
Apps Script (Thu/Mon ~10:30)        bookmarklet + scripts/update.sh (backup)
  Gmail → parse + Drive OCR                 blog post → src/ingest.py
  → data/inbox/<slug>.json                  → data/<slug>.json
              \                              /
               └─ push to data/** ─ GitHub Actions (.github/workflows/build-data.yml)
                    1. src/build_data.py  merge inbox → data/<slug>.json + manifest (src/store.py)
                    2. src/enrich.py      讀墨 / Goodreads ratings → data/ratings/<kobo_id>.json
                    3. commit → GitHub Pages redeploys
```

Merging is per book by Kobo product id: the email contributes prices, sale dates and the promo
code; the bookmarklet contributes full titles, author, publisher and HK links.

### Ratings (`src/enrich.py`)

For each book in lists ending within the last 14 days, per platform:

1. URL from `data/rating_overrides.json` (`{"<kobo_id>": {"readmoo": "...", "goodreads": "..."}}`),
   otherwise OpenAI web search restricted to that domain proposes up to 3 candidate URLs.
   The model never supplies a rating; its URLs must match an allowlist
   (`readmoo.com/book/<id>`, `goodreads.com/book/show/<id>`).
2. `src/rating_fetch.py` fetches the page: robots.txt and Crawl-delay are obeyed (≥10 s between
   requests per host), the User-Agent is honest, and any bot challenge (Cloudflare, AWS WAF) or
   403/429 marks the platform `blocked` for this run. No attempt is made to get around it.
3. Title/author on the page must match the Kobo book (overrides skip this check); the score comes
   from the page's JSON-LD / schema.org data.

When a candidate page cannot be read (blocked), a rating the search results showed for a
confident match is kept as `search_reported` and shown as "≈x.x（…・搜尋）".

Kobo's own rating: kobo.com is behind a Cloudflare challenge, so its page is never fetched. Search
is asked for the book's exact Kobo Taiwan URL, and only a rating reported for that URL is used
(always shown as "搜尋").

Readmoo's robots.txt blocks AI crawlers (GPTBot etc.) and `/search/`, so OpenAI search rarely
finds its book pages, and its WAF answers 403 to GitHub Actions IPs. Add Readmoo URLs by hand in
`data/rating_overrides.json` (`{"<kobo_id>": {"readmoo": "https://readmoo.com/book/<id>"}}`).

Results are cached per book with a TTL by status (ok 30 d, blocked 7 d, not found 14 d). 博客來 and Amazon are not fetched (bot
protection / robots.txt); the page links to a 博客來 search instead. Goodreads often answers with
an AWS WAF challenge, so expect it to be `blocked` some weeks; fix a wrong match with an override.

## Apps Script setup

1. https://script.google.com → New project → Project Settings → tick "Show appsscript.json".
2. Paste `appsscript/appsscript.json` and `appsscript/Code.gs` (replace the defaults).
   It only writes `data/inbox/`; merging and ratings happen in GitHub Actions.
   Scopes are limited to `gmail.readonly`, `drive.file` (only its own temp OCR docs),
   external requests, and triggers.
3. Project Settings → Script properties → `GITHUB_TOKEN` = fine-grained PAT with
   Contents read/write on `zapion/kobo99-digest` only.
4. Run `runIngest` once (authorise), check the log, then run `installTriggers` once.
5. Triggers page → each trigger → failure notifications: "Notify me immediately".

## GitHub Actions setup

Repository → Settings → Secrets and variables → Actions:

- Secret `OPENAI_API_KEY` (needed for URL discovery; without it only overrides are refreshed).
- Optional variable `OPENAI_MODEL` (default `gpt-5-mini`; must support the `web_search` tool).

Run it by hand from the Actions tab ("build-data" → Run workflow); it also runs on every push
to `data/**` and weekly on Wednesday to retry blocked/not-found ratings.

## Bookmarklet backup (Brave)

Install once: open https://zapion.github.io/kobo99-digest/bookmarklet/install.html and drag the button
to the bookmarks bar (`⌘⇧B`). On the Kobo blog post click it, then run `scripts/update.sh`.

## Development

```bash
python3 -m pytest tests --cov=src          # ingest, store, inbox merge, ratings
node --test appsscript/test                 # Apps Script logic (fake Apps Script globals)
python3 scripts/build_bookmarklet.py       # rebuild install.html after editing extract.js
python3 -m http.server 8099                # preview; add ?date=YYYY-MM-DD to simulate a day
```

Local git push uses a repo-local credential helper that reads `GITHUB_TOKEN` from `.env`
(fine-grained PAT, Contents read/write on this repo only).
