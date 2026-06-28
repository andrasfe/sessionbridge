"""Shared HTML shell for API-rendered pages.

Adapters fetch a site's official API and return a self-contained HTML page
(inline CSS, no external assets) that the runner serves into the live browser.
The page the user sees is clean, ad/tracker-free, and trivial for the agent to
read — and it never touched the bot-walled DOM.
"""
from __future__ import annotations

import html as _html


def esc(s: str | None) -> str:
    return _html.escape(s or "", quote=True)


def shell(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title><style>
:root{{color-scheme:dark}}
*{{box-sizing:border-box}}
body{{margin:0;background:#0f1115;color:#e8eaed;font:15px/1.55 system-ui,sans-serif}}
.wrap{{max-width:900px;margin:0 auto;padding:18px 26px}}
.banner{{background:#173a2a;border:1px solid #1f7a3d;color:#7fe0a3;font-size:12px;
  padding:6px 12px;border-radius:6px;display:inline-block;margin-bottom:16px}}
h1{{font-size:19px;margin:0 0 14px}}
.item{{padding:9px 0;border-bottom:1px solid #20242d}}
a.title{{color:#dbeafe;text-decoration:none;font-size:16px}}
a.title:hover{{text-decoration:underline}}
.meta{{color:#9aa0a6;font-size:12.5px;margin-top:3px}}
.meta a{{color:#9aa0a6;text-decoration:none}}
.meta a:hover{{text-decoration:underline}}
.body{{white-space:pre-wrap;margin:10px 0;color:#cfd3d8}}
.c{{border-left:2px solid #262a33;padding:4px 0 4px 12px;margin:10px 0}}
a{{color:#6db3ff}}
</style></head><body><div class="wrap">{body}</div></body></html>"""
