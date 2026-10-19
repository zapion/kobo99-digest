# Kobo 每週書單

Static GitHub Pages site for Kobo Taiwan's two weekly lists, sourced from the official Kobo blog:

| List | Blog slug | Published (Taipei) | Period |
|---|---|---|---|
| 一週99書單 | `weekly-dd99-YYYY-wNN` | Thu 06:00 | Thu–Wed, one NT$99 book per day |
| 本週精選書單 | `weekly-8585-YYYY-wNN` | Mon 06:00 | Mon–Sun, checkout promo code |

Site: https://zapion.github.io/kobo99-digest/

## How it works

There is no scheduled job. The page (`index.html`) loads `data/manifest.json`, picks the list whose
period contains today's Taipei date, and highlights today's NT$99 book in the browser. When a list's
period has passed and no newer one exists, it shows an "expired" notice linking to the Kobo blog.

Kobo blog posts are behind a Cloudflare browser challenge, so data is captured in your own browser:

1. `bookmarklet/extract.js` runs on the post and downloads `weekly-*.json` (raw book blocks).
2. `src/ingest.py` validates and normalises it into `data/<slug>.json` + `data/manifest.json`,
   and archives the raw file in `data/raw/`.
3. `scripts/update.sh` = pull → ingest `~/Downloads/weekly-*.json` → commit → push (Pages redeploys).

## Weekly routine (Brave)

Install once: open https://zapion.github.io/kobo99-digest/bookmarklet/install.html and drag the button
to the bookmarks bar (`⌘⇧B` shows the bar).

Every Thursday (99) and Monday (精選):

1. Open the new post on https://www.kobo.com/zh/blog and click the bookmark.
2. Run:

```bash
~/workspace/kobo99/scripts/update.sh
```

## Development

```bash
python3 -m pytest tests --cov=src          # ingest tests
python3 scripts/build_bookmarklet.py       # rebuild install.html after editing extract.js
python3 -m http.server 8099                # preview; add ?date=YYYY-MM-DD to simulate a day
```

Git push auth uses a repo-local credential helper that reads `GITHUB_TOKEN` from `.env`
(fine-grained PAT, Contents read/write on this repo only).
