"""Hacker News API-render adapter (open API, no auth).

Demonstrates the API-plane: the front page and item/comment pages are built from
the official Firebase API and served as clean HTML. Links stay on
news.ycombinator.com, so clicks are re-intercepted and rendered too (re-entrant).
"""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse, parse_qs

import httpx

from .render import shell, esc

API = "https://hacker-news.firebaseio.com/v0"
HOSTS = {"news.ycombinator.com"}


def matches(url: str) -> bool:
    try:
        return (urlparse(url).hostname or "") in HOSTS
    except Exception:
        return False


async def _item(client: httpx.AsyncClient, iid) -> dict | None:
    try:
        r = await client.get(f"{API}/item/{iid}.json")
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def _story_row(it: dict) -> str:
    iid = it.get("id")
    url = it.get("url") or f"https://news.ycombinator.com/item?id={iid}"
    comments = f"https://news.ycombinator.com/item?id={iid}"
    return (
        f'<div class="item"><a class="title" href="{esc(url)}">{esc(it.get("title"))}</a>'
        f'<div class="meta">{it.get("score",0)} points · {esc(it.get("by"))} · '
        f'<a href="{esc(comments)}">{it.get("descendants",0)} comments</a></div></div>'
    )


def _comment(it: dict) -> str:
    if not it or it.get("deleted") or it.get("dead") or not it.get("text"):
        return ""
    return (f'<div class="c"><div class="meta">{esc(it.get("by"))}</div>'
            f'<div class="body">{it.get("text")}</div></div>')


async def render(url: str) -> str | None:
    p = urlparse(url)
    async with httpx.AsyncClient(timeout=15) as client:
        if p.path.startswith("/item"):
            iid = (parse_qs(p.query).get("id") or [None])[0]
            if not iid:
                return None
            it = await _item(client, iid)
            if not it:
                return None
            kids = (it.get("kids") or [])[:25]
            comments = await asyncio.gather(*[_item(client, k) for k in kids])
            head = (f'<h1>{esc(it.get("title") or "Discussion")}</h1>'
                    f'<div class="meta">{it.get("score",0)} points · {esc(it.get("by"))}</div>')
            if it.get("text"):
                head += f'<div class="body">{it.get("text")}</div>'
            body = head + "".join(_comment(c) for c in comments if c)
            return shell(it.get("title") or "Hacker News", _banner() + body)
        # front page
        try:
            ids = (await client.get(f"{API}/topstories.json")).json()[:25]
        except Exception:
            return None
        items = await asyncio.gather(*[_item(client, i) for i in ids])
        rows = "".join(_story_row(i) for i in items if i)
        return shell("Hacker News", _banner() + "<h1>Hacker News — Top</h1>" + rows)


def _banner() -> str:
    return '<div class="banner">rendered from the Hacker News API</div>'
