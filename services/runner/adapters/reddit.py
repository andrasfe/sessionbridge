"""Reddit API-render adapter (OAuth — userless / application-only).

Reddit hard-blocks headless browsers with a JS challenge ("blocked by network
security") AND gates the public .json (403). The sanctioned path is OAuth: a
registered app (REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET) yields an
application-only token used against oauth.reddit.com.

Without credentials this adapter returns None, so navigation falls through to the
real (blocked) DOM — no fabrication, no pretending. Drop the creds in and it
activates.
"""
from __future__ import annotations

import os
import time
from urllib.parse import urlparse

import httpx

from .render import shell, esc

UA = "SessionBridge/0.1 (API render adapter)"
_token = {"value": None, "exp": 0.0}


def matches(url: str) -> bool:
    h = (urlparse(url).hostname or "").lower()
    return h == "reddit.com" or h.endswith(".reddit.com")


async def _bearer(client: httpx.AsyncClient) -> str | None:
    cid, sec = os.getenv("REDDIT_CLIENT_ID"), os.getenv("REDDIT_CLIENT_SECRET")
    if not cid or not sec:
        return None
    if _token["value"] and time.time() < _token["exp"] - 30:
        return _token["value"]
    try:
        r = await client.post(
            "https://www.reddit.com/api/v1/access_token",
            data={"grant_type": "client_credentials"},
            auth=(cid, sec), headers={"User-Agent": UA},
        )
        if r.status_code != 200:
            return None
        j = r.json()
        _token["value"] = j["access_token"]
        _token["exp"] = time.time() + j.get("expires_in", 3600)
        return _token["value"]
    except Exception:
        return None


def _api_path(url: str) -> str:
    # /r/foo -> /r/foo/.json ; permalink -> permalink/.json ; / -> /.json
    p = urlparse(url)
    path = (p.path or "/").rstrip("/")
    q = f"?{p.query}" if p.query else ""
    return f"https://oauth.reddit.com{path}/.json{q}"


async def render(url: str) -> str | None:
    async with httpx.AsyncClient(timeout=15) as client:
        token = await _bearer(client)
        if not token:
            return None  # no creds -> fall back to the real (blocked) DOM
        hdrs = {"Authorization": f"bearer {token}", "User-Agent": UA}
        try:
            r = await client.get(_api_path(url), headers=hdrs)
            if r.status_code != 200:
                return None
            data = r.json()
        except Exception:
            return None

    # Comment thread: [post_listing, comments_listing]
    if isinstance(data, list) and len(data) == 2:
        post = data[0]["data"]["children"][0]["data"]
        head = (f'<h1>{esc(post.get("title"))}</h1>'
                f'<div class="meta">r/{esc(post.get("subreddit"))} · {post.get("score",0)} pts · '
                f'u/{esc(post.get("author"))}</div>')
        if post.get("selftext"):
            head += f'<div class="body">{esc(post["selftext"])}</div>'
        rows = ""
        for c in data[1]["data"]["children"]:
            cd = c.get("data", {})
            if cd.get("body"):
                rows += (f'<div class="c"><div class="meta">u/{esc(cd.get("author"))} · '
                         f'{cd.get("score",0)} pts</div><div class="body">{esc(cd["body"])}</div></div>')
        return shell(post.get("title") or "Reddit", _banner() + head + rows)

    # Listing
    children = data.get("data", {}).get("children", [])
    rows = ""
    for c in children:
        d = c.get("data", {})
        perma = f'https://www.reddit.com{d.get("permalink","")}'
        rows += (f'<div class="item"><a class="title" href="{esc(perma)}">{esc(d.get("title"))}</a>'
                 f'<div class="meta">{d.get("score",0)} pts · u/{esc(d.get("author"))} · '
                 f'<a href="{esc(perma)}">{d.get("num_comments",0)} comments</a></div></div>')
    return shell("Reddit", _banner() + rows)


def _banner() -> str:
    return '<div class="banner">rendered from the Reddit API (browser DOM is bot-blocked)</div>'
