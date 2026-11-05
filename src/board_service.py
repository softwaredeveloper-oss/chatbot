"""Board of Directors / committee composition, read directly from the official TCI Express page.

The page is the single source of truth, so every user on every device gets the same complete list
(no web search, no LLM summarising, nothing dropped or invented).
"""
import re
import time
from html import escape

import requests
from bs4 import BeautifulSoup

BOARD_URL = "https://www.tciexpress.in/board-committee-composition.aspx"
CACHE_SECONDS = 3600

_cache = {"ts": 0.0, "data": None}

BOARD_QUERY = re.compile(
    r"\b(board\s+(members?|of\s+directors?|composition)|directors?|committees?|chairman|chairperson"
    r"|independent\s+directors?|managing\s+director)\b",
    re.IGNORECASE,
)
# "director" alone also matches things like "director of operations" - require TCI/board context or a list cue
LIST_OR_BOARD = re.compile(r"\b(board|list|all|who|composition|committee|members?)\b", re.IGNORECASE)


def is_board_query(msg: str) -> bool:
    return bool(BOARD_QUERY.search(msg) and LIST_OR_BOARD.search(msg))


def _fetch():
    """Return [(section title, [rows])] where rows[0] is the header row."""
    now = time.time()
    if _cache["data"] and now - _cache["ts"] < CACHE_SECONDS:
        return _cache["data"]

    resp = requests.get(BOARD_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    sections = []
    for ul in soup.select("ul.boardcommitee_table"):
        rows = []
        for li in ul.find_all("li"):
            cells = [sp.get_text(" ", strip=True) for sp in li.find_all("span")]
            if any(cells):
                rows.append(cells)
        h = ul.find_previous("h4")
        if len(rows) >= 2:
            sections.append((h.get_text(" ", strip=True) if h else "Table", rows))

    if sections:
        _cache.update(ts=now, data=sections)
    return sections


def _table_html(rows):
    head = "".join(f"<th>{escape(c)}</th>" for c in rows[0])
    body = "".join("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in r) + "</tr>" for r in rows[1:])
    return (
        "<div class='chat-table-wrapper'><table class='dataframe table table-hover table-striped table-sm'>"
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def board_answer(msg: str):
    """Return (html, links), or ("", []) if the page could not be read."""
    try:
        sections = _fetch()
    except Exception as e:
        print(f"[WARN] Board page fetch failed: {e}")
        return "", []
    if not sections:
        return "", []

    wants_committees = bool(re.search(r"committee", msg, re.IGNORECASE))
    board = [s for s in sections if "board" in s[0].lower()]
    shown = sections if wants_committees else (board or sections[:1])

    parts = []
    for title, rows in shown:
        parts.append(f"<h4>{escape(title)} ({len(rows) - 1})</h4>" + _table_html(rows))
    parts.append(
        "<p style='margin:4px 0;'><em>Source: official TCI Express website - Board &amp; Committee Composition.</em></p>"
    )
    return "".join(parts), [{"title": "Board Committee Composition", "url": BOARD_URL}]
