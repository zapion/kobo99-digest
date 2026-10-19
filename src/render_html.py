"""Render the digest as static HTML pages for GitHub Pages."""

from __future__ import annotations

from html import escape

from kobo99 import Book, Digest

SITE_TITLE = "Kobo 每週 99 日報"

_STYLE = """
:root { --accent:#2563eb; --bg:#fafafa; --card:#fff; --text:#1a1a1a; --meta:#6b7280; --hr:#e5e7eb; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#111827; --card:#1f2937; --text:#f3f4f6; --meta:#9ca3af; --hr:#374151; --accent:#60a5fa; }
}
* { box-sizing:border-box; margin:0; padding:0; }
body { font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans TC",sans-serif;
  background:var(--bg); color:var(--text); line-height:1.7; padding:1rem; }
.container { max-width:720px; margin:0 auto; }
h1 { font-size:1.5rem; margin:1.5rem 0 .5rem; color:var(--accent); }
h2 { font-size:1.15rem; margin:2rem 0 .6rem; }
.card { display:flex; gap:1rem; background:var(--card); border:1px solid var(--hr);
  border-radius:8px; padding:1rem; margin:.8rem 0; }
.card img { width:96px; height:auto; object-fit:cover; border-radius:4px; align-self:flex-start; }
.card h3 { font-size:1.05rem; }
.meta { font-size:.85rem; color:var(--meta); }
.price { color:#dc2626; font-weight:700; }
.ratings { font-size:.8rem; margin-top:.3rem; }
.ratings a { margin-right:.7rem; }
.desc { font-size:.9rem; margin-top:.4rem; }
a { color:var(--accent); }
.nav { margin:1rem 0; font-size:.85rem; }
ul.index { list-style:none; }
ul.index li { margin:.6rem 0; }
footer { margin-top:2rem; font-size:.75rem; color:var(--meta); }
"""

_SECTIONS = (
    ("🔥 今日 99", "today_books"),
    ("⏳ 接下來", "upcoming_books"),
    ("✅ 本週已過", "past_books"),
)


def _page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(title)}</title>
<style>{_STYLE}</style>
</head>
<body>
<div class="container">
{body}
<footer>資料來源：<a href="https://kobo99.com/">kobo99.com</a> · 評分與價格僅供參考，以各平台官網為準。</footer>
</div>
</body>
</html>"""


def _book_card(book: Book) -> str:
    cover = f'<img src="{escape(book.cover_url)}" alt="" loading="lazy">' if book.cover_url else ""
    title = escape(book.title)
    if book.kobo_url:
        title = f'<a href="{escape(book.kobo_url)}" target="_blank" rel="noopener">{title}</a>'
    byline = " ｜ ".join(escape(x) for x in (book.author, book.publisher) if x)
    avg = f" ｜ 綜合 {book.avg_score:.2f}" if book.avg_score else ""
    ratings = "".join(
        f'<a href="{escape(r.url)}" target="_blank" rel="noopener">{escape(r.platform)} {r.score:.1f}</a>'
        if r.url
        else f"<span>{escape(r.platform)} {r.score:.1f} </span>"
        for r in book.ratings
    )
    desc = f'<p class="desc">{escape(book.description)}</p>' if book.description else ""
    return f"""<div class="card">{cover}<div>
<h3>{title}</h3>
<p class="meta">{byline}</p>
<p class="meta">{book.sale_date.isoformat()} · <span class="price">{escape(book.sale_price)}</span>（原價 {escape(book.list_price)}）{avg}</p>
<p class="ratings">{ratings}</p>
{desc}
</div></div>"""


def render_day_page(digest: Digest) -> str:
    parts = [
        '<div class="nav"><a href="index.html">← 首頁</a></div>',
        f"<h1>{SITE_TITLE} · {digest.date.isoformat()}</h1>",
        f'<p class="meta">{digest.year} 第 {digest.week} 週 ｜ {escape(digest.sale_label)}</p>',
    ]
    for heading, attr in _SECTIONS:
        books = getattr(digest, attr)
        if books:
            parts.append(f"<h2>{heading}</h2>")
            parts.extend(_book_card(b) for b in books)
    return _page(f"{SITE_TITLE} · {digest.date.isoformat()}", "\n".join(parts))


def render_index(entries: list[tuple[str, str]]) -> str:
    """entries: (date_str, page_filename) pairs."""
    items = "\n".join(
        f'<li><a href="{escape(fname)}">📚 {escape(date)}</a></li>'
        for date, fname in sorted(entries, reverse=True)
    )
    body = f'<h1>📚 {SITE_TITLE}</h1>\n<ul class="index">\n{items}\n</ul>'
    return _page(SITE_TITLE, body)
