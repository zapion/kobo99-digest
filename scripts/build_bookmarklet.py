#!/usr/bin/env python3
"""Build bookmarklet/install.html (drag the link to the Brave bookmarks bar) from extract.js."""

from __future__ import annotations

import re
import urllib.parse
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "bookmarklet" / "extract.js"
TARGET = ROOT / "bookmarklet" / "install.html"


def build_url(source: str) -> str:
    body = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    body = re.sub(r"\s*\n\s*", " ", body).strip()
    return "javascript:" + urllib.parse.quote(body, safe="(){}[]=>;:,.'!?/+*&|<-_$")


def render(url: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kobo 書單擷取</title>
<style>
:root {{ --bg:#fafafa; --text:#1a1a1a; --accent:#2563eb; --meta:#6b7280; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#111827; --text:#f3f4f6; --accent:#60a5fa; --meta:#9ca3af; }} }}
body {{ font-family:-apple-system,BlinkMacSystemFont,"Noto Sans TC",sans-serif; background:var(--bg); color:var(--text);
  max-width:640px; margin:2rem auto; padding:0 1rem; line-height:1.7; }}
a.bm {{ display:inline-block; padding:.6rem 1rem; border-radius:8px; background:var(--accent); color:#fff;
  text-decoration:none; font-weight:700; }}
li {{ margin:.4rem 0; }} code {{ font-size:.9em; }} .meta {{ color:var(--meta); font-size:.85rem; }}
</style>
</head>
<body>
<h1>Kobo 書單擷取（Brave）</h1>
<p><a class="bm" href="{escape(url)}">📚 擷取 Kobo 書單</a></p>
<ol>
<li>按 <code>⌘⇧B</code> 顯示書籤列，把上面的按鈕拖到書籤列。</li>
<li>每週四開「一週99書單」、每週一開「本週精選書單」文章，點書籤，會下載 <code>weekly-*.json</code>。</li>
<li>執行 <code>~/workspace/kobo99/scripts/update.sh</code>，會發佈到 GitHub Pages。</li>
</ol>
<p class="meta">若 Brave 詢問是否允許下載，請選允許。</p>
</body>
</html>
"""


def main() -> int:
    TARGET.write_text(render(build_url(SOURCE.read_text(encoding="utf-8"))), encoding="utf-8")
    print(f"Wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
