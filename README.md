# kobo99 daily digest

Fetches the weekly Kobo NT$99 list from [kobo99.com](https://kobo99.com/) every day, writes a Markdown digest
(`output/YYYY-MM-DD-kobo99.md`) and publishes a static page to GitHub Pages. Modelled on `morning-news-digest`.

## How it works

kobo99.com renders client-side from `https://kobo99.com/data/latest.json`, so no HTML scraping is needed.

- `src/kobo99.py` — fetch + parse (immutable dataclasses) + Markdown. Splits books into 今日 99 / 接下來 / 本週已過 by `sale_start`, Asia/Taipei date.
- `src/render_html.py` — day page and index HTML (escaped, dark-mode aware).
- `src/publisher.py` — pushes `YYYY-MM-DD.html` and `index.html` to `GITHUB_REPO` via the GitHub contents API (retries on 429/5xx).
- `scripts/run_daily.sh` — waits for network, then fetch → publish.
- `scripts/install_launchagent.sh` — installs a launchd job, daily 08:30 (Kobo updates Thursday; the list is re-fetched daily so "today" stays correct).

## Setup

```bash
cp .env.example .env     # fill GITHUB_TOKEN and GITHUB_REPO
python3 src/kobo99.py    # fetch only
python3 src/publisher.py # publish
scripts/install_launchagent.sh
```

Create the Pages repo first (Settings → Pages → deploy from `main` / root). Token: fine-grained PAT, Contents read/write on that repo only.

## Tests

```bash
python3 -m pytest tests --cov=src
```
