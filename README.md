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
   otherwise Claude's web search tool, restricted to that domain, proposes up to 3 candidate URLs
   (each must have appeared in the search results).
   The model never supplies a rating; its URLs must match an allowlist
   (`readmoo.com/book/<id>`, `goodreads.com/book/show/<id>`).
   The page kobo99.com matched to the book is added as a last candidate; it gets no trust and has
   to pass the check in step 3 like any other.
2. `src/rating_fetch.py` fetches the page: robots.txt and Crawl-delay are obeyed (≥10 s between
   requests per host), the User-Agent is honest, and any bot challenge (Cloudflare, AWS WAF) or
   403/429 marks the platform `blocked` for this run. Our own fetcher never tries to get around it;
   the optional Apify path below is the one exception, and it is off unless `APIFY_TOKEN` is set.
3. Title/author on the page must match the Kobo book (overrides skip this check); the score comes
   from the page's JSON-LD / schema.org data.

When a candidate page cannot be read (blocked), a rating the search results showed for a
confident match is kept as `search_reported` and shown as "≈x.x（…・搜尋）".

Platforms: Kobo, 博客來, 讀墨, Goodreads, Amazon (shown in that order), all from GitHub Actions.
Goodreads and Readmoo pages are read when reachable. Kobo (Cloudflare), 博客來 and Amazon are never
fetched by us: search is asked for the book's page, and only a rating reported for the exact Kobo URL, or
for a 博客來 / Amazon page whose title and author match ours (or the original's), is used.
Readmoo answers 403 to Actions IPs and blocks AI crawlers, so it is mostly search-reported or empty.

### Reading pages through Apify (optional)

With the secret `APIFY_TOKEN` set, `src/apify_fetch.py` reads the pages we cannot fetch ourselves
through [Apify](https://apify.com), a paid service that gets past bot protection on our behalf.
This is a deliberate step away from "never work around a block"; leave the secret unset to keep
the old behaviour.

- **Kobo**: the book's own page, rendered in a browser (the ratings widget is filled in by
  JavaScript). No web search is needed, so Kobo joins the default platforms.
- **博客來**: candidate pages from search or kobo99.com; the rating comes from the page's JSON-LD.
- **Amazon**: candidate pages go to a product Actor that returns title, author, stars and count.
- **讀墨**: only when our own fetch was blocked.
- **Goodreads**: never; it stays on our own fetcher.

Rules that still hold: a page robots.txt disallows is not sent to Apify (when robots.txt can be
read); title/author on the page must match exactly as in step 3; at most 2 pages per book and
platform. A rating read this way is stored with `"via": "apify"` and shown like any page rating.

Cost: about US$0.0015 per page and US$0.005 per Amazon product (free-tier prices). Each run stops
using Apify once its estimate reaches `APIFY_BUDGET_USD` (variable, default 0.25) and falls back
to search-reported ratings; the run log ends with `apify_calls` and `apify_usd_estimate`.
`APIFY_PLATFORMS` (variable, comma list; default `kobo,books,amazon,readmoo`) narrows it.

Fallback: `src/kobo99_fallback.py` reads kobo99.com's public JSON (`data/latest.json` and the
本週精選 theme file; robots.txt allows it) and fills in platforms we could not rate, shown as
"≈x.x（…・kobo99）". Only score and count are kept; its tracking/affiliate links are never stored.

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

- Secret `ANTHROPIC_API_KEY` (needed for URL discovery; without it only overrides are refreshed).
- Optional secret `APIFY_TOKEN` (Apify Console → Settings → API & Integrations), with optional variables
  `APIFY_BUDGET_USD` and `APIFY_PLATFORMS`; see "Reading pages through Apify".
- Optional variable `CLAUDE_MODEL` (default `claude-haiku-5-5`; must support the web search tool).
  Web search costs $10 per 1,000 searches plus tokens; at most 3 searches per book and platform; Goodreads, 博客來 and Amazon are searched by default (`ENRICH_PLATFORMS`), Kobo and 讀墨 come from the kobo99 fallback.
  All workers share one rate limiter (`src/rate_limit.py`): input tokens per minute stay under
  `CLAUDE_INPUT_TPM` (variable, default 400000), a 429 pauses every worker until its retry-after,
  and waits are jittered so workers do not retry on the same tick.

Run it by hand from the Actions tab ("build-data" → Run workflow); it also runs on every push
to `data/**` and on Tuesday and Friday (after the Monday and Thursday lists) to retry blocked/not-found ratings and pick up kobo99.com numbers once it has updated.

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
