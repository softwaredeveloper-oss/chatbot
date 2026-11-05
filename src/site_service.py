"""Direct reader for the official TCI Express website (tciexpress.in).

For questions the website itself answers (management, about, contact, services, infrastructure ...) the
page text is fetched straight from the site - no web search, so it is fast, exact and uses no Tavily credits.
Pages are cached for an hour. Results use the same shape as web-search results so the normal
answer chain (and its guardrails) can use them as the source.
"""
import re
import time

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.tciexpress.in/"
CACHE_SECONDS = 900  # 15 min: people/leadership details should be current
_cache = {}  # path -> (timestamp, text)

# (page path, title, regex that selects it). The more specific pages come first.
PAGES = [
    ("top-management", "Top Management - TCI Express",
     r"top management|management team|leadership|\bleaders?\b|chairman|managing director|key people|who (runs|leads|heads)"),
    ("contact", "Contact - TCI Express",
     r"\bcontact\b|\bphone\b|helpline|customer care|toll[\s-]*free|\be-?mail\b|head office|corporate office|"
     r"registered office|office address|company address"),
    ("about-us", "About Us - TCI Express",
     r"about (tci|us|the company)|who (is|are) tci|\bhistory\b|established|founded|since when|"
     r"\bmission\b|\bvision\b|company profile|what is tci|tell me about tci"),
    ("surface-express", "Surface Express - TCI Express", r"surface\s+express|surface\s+service"),
    ("domestic-air-express", "Domestic Air Express - TCI Express", r"domestic\s+air"),
    ("international-air-express", "International Air Express - TCI Express", r"international\s+air|export"),
    ("rail-express", "Rail Express - TCI Express", r"rail\s+express|by rail"),
    ("e-commerce", "E-commerce Express - TCI Express", r"e-?\s?commerce"),
    ("c2c-express", "C2C Express - TCI Express", r"\bc2c\b|customer to customer"),
    ("cold-chain-express", "Cold Chain Express - TCI Express", r"cold\s*chain|temperature\s+controlled"),
    ("mbg-express", "Money Back Guarantee (MBG) - TCI Express", r"\bmbg\b|money\s*back"),
    ("value-added-services", "Value Added Services - TCI Express", r"value[\s-]*added|\bvas\b|\bcod\b|\bpod\b"),
    ("infrastructure", "Infrastructure - TCI Express", r"infrastructure|fleet|network of branches|warehouse"),
    ("quality-policy", "Quality Policy - TCI Express", r"quality\s+policy|\biso\b"),
]
# General "what services do you offer" questions, used only when no specific page matched
SERVICES_PAGES = [("express-services", "Express Services - TCI Express"),
                  ("value-added-services", "Value Added Services - TCI Express")]
SERVICES_PATTERN = re.compile(r"\b(services?|offerings?|solutions?|what do you (do|offer))\b", re.IGNORECASE)

_COMPILED = [(p, t, re.compile(rx, re.IGNORECASE)) for p, t, rx in PAGES]
_SPLIT_MARKERS = ("Subscribe to our latest updates", "Tags")


def matching_pages(msg: str, limit: int = 2):
    """[(path, title)] of the official pages that answer this message (best first)."""
    found = [(p, t) for p, t, rx in _COMPILED if rx.search(msg or "")]
    if not found and SERVICES_PATTERN.search(msg or ""):
        found = SERVICES_PAGES
    return found[:limit]


def matches(msg: str) -> bool:
    return bool(matching_pages(msg))


def _clean(lines):
    """Drop the site header/menu and the footer/sidebar, keep the page's own text."""
    for i, line in enumerate(lines):
        if line.startswith("Enter up to 50 tracking numbers"):
            lines = lines[i + 1:]
            break
    out = []
    for line in lines:
        if line.startswith(_SPLIT_MARKERS) or (line == "Express Services" and len(out) > 6):
            break
        out.append(line)
    return "\n".join(out).strip()


def fetch_page(path: str) -> str:
    """Readable text of https://www.tciexpress.in/<path> ('' if it cannot be read). Cached for an hour."""
    now = time.time()
    hit = _cache.get(path)
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        resp = requests.get(BASE_URL + path, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "lxml", from_encoding="utf-8")
        for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
            tag.decompose()
        root = soup.find("main") or soup.body or soup
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in root.get_text("\n").splitlines() if ln.strip()]
        text = _clean(lines)
    except Exception as e:
        print(f"[WARN] Official page fetch failed ({path}): {e}")
        return ""
    if text:
        _cache[path] = (now, text)
    return text


def site_results(msg: str, limit: int = 2):
    """Web-search-shaped results ({title, url, content}) built from the matching official pages."""
    results = []
    for path, title in matching_pages(msg, limit):
        text = fetch_page(path)
        if text:
            results.append({"title": title, "url": BASE_URL + path, "content": text, "official": True})
    return results


# ---------------------------------------------------------------------------
# Senior people (chairman, MD, executives, directors): answered ONLY from the official site
# ---------------------------------------------------------------------------
TOP_MGMT_PATH = "top-management"
BOARD_URL = "https://www.tciexpress.in/board-committee-composition.aspx"

# Role words that mean "a senior person / leadership question"
ROLE_PATTERN = re.compile(
    r"\b(chairman|chairperson|vice[\s-]*chairman|managing director|md|ceo|cfo|coo|cbo|"
    r"chief\s+[a-z]+(\s+[a-z]+)?\s+officer|president|directors?|top management|senior management|"
    r"leadership|leaders?|key managerial|company secretary|promoters?|management team)\b",
    re.IGNORECASE,
)
# Words that are not part of a person's name (question words, titles, role words, company words)
_NOT_NAME = set("""
who is are was the a an of for to in on at and or about tell me show give details detail info information
what which whom whose current present latest new now today please list all any
mr mrs ms dr shri sri tci express ltd limited company group india
chairman chairperson vice managing director directors md ceo cfo coo cbo cio cto chief officer president
top senior management leadership leader leaders key managerial secretary promoter promoters team
member members board committee head heads
""".split())


def _norm_tokens(text: str):
    """'Mr. D.P. Agarwal' -> ['dp', 'agarwal'] (dots dropped, titles removed)."""
    words = re.findall(r"[a-z]+", re.sub(r"\.", "", (text or "").lower()))
    words = [w for w in words if w not in {"mr", "mrs", "ms", "dr", "shri", "sri"}]
    merged = []
    for w in words:  # initials written apart ("D. P.") join up like "D.P." -> "dp"
        if merged and len(w) == 1 and len(merged[-1]) == 1:
            merged[-1] += w
        else:
            merged.append(w)
    return merged


def _top_management_people(text: str):
    """Names on the Top Management page: short 'Mr./Mrs. Name' lines (the long bio lines are skipped)."""
    names = []
    for line in text.splitlines():
        if re.match(r"^(Mr|Mrs|Ms|Dr|Shri)\.?\s", line) and len(line) < 50 and " is " not in line:
            names.append(line.strip())
    return names


def _board_text_and_people():
    """(text of the Board of Directors table, [names of everyone on any board/committee table])."""
    try:
        from src.board_service import _fetch  # shares the board page cache
        sections = _fetch()
    except Exception as e:
        print(f"[WARN] Board page fetch failed: {e}")
        return "", []
    text, people = "", []
    for title, rows in sections:
        if "board" in title.lower() and not text:
            text = "Board of Directors (official TCI Express board page):\n" + "\n".join(
                f"{r[1]} - {r[2]} ({r[3]})" for r in rows[1:] if len(r) >= 4
            )
        people += [r[1] for r in rows[1:] if len(r) >= 2]
    return text, people


def is_senior_query(msg: str) -> bool:
    """Cheap pre-check (no network): the message has a leadership role word."""
    return bool(ROLE_PATTERN.search(msg or ""))


def senior_results(msg: str):
    """
    Official-site pages that answer a question about a SENIOR person: someone listed on the official
    Top Management / Board pages, or a leadership role (chairman, MD, CFO, directors ...).
    Returns [] when the message is not such a question, or the official site does not cover it - the
    normal flow (web search) then handles it.
    """
    msg = msg or ""
    words = re.findall(r"[a-z]+", re.sub(r"\.", "", msg.lower()))
    name_tokens = [w for w in words if len(w) >= 2 and w not in _NOT_NAME]
    role = bool(ROLE_PATTERN.search(msg))
    if not role and not name_tokens:
        return []

    top = fetch_page(TOP_MGMT_PATH)
    board_text, board_people = _board_text_and_people()
    top_people = _top_management_people(top)

    if name_tokens:
        # "DP Agarwal", "Chander", "Vineet Agarwal": every name word must be in the person's official name
        wanted = set(name_tokens)
        in_top = any(wanted <= set(_norm_tokens(n)) for n in top_people)
        in_board = any(wanted <= set(_norm_tokens(n)) for n in board_people)
        if not (in_top or in_board):
            return []  # not a senior person on the official site (e.g. an ordinary employee)
    else:
        # only a role word: use the pages that actually mention that role
        role_words = {w.lower() for m in ROLE_PATTERN.finditer(msg) for w in re.findall(r"[a-z]+", m.group(0).lower())}
        role_words -= {"top", "senior", "management", "leadership", "leaders", "leader", "team", "key", "managerial"}
        generic = bool(re.search(r"top management|senior management|leadership|leaders?|management team|key managerial", msg, re.I))
        in_top = bool(top) and (generic or any(w in top.lower() for w in role_words))
        in_board = bool(board_text) and (generic or any(w in board_text.lower() for w in role_words))
        if not (in_top or in_board):
            return []

    results = []
    if top and in_top:
        results.append({"title": "Top Management - TCI Express", "url": BASE_URL + TOP_MGMT_PATH,
                        "content": top, "official": True})
    if board_text and in_board:
        results.append({"title": "Board of Directors - TCI Express", "url": BOARD_URL,
                        "content": board_text, "official": True})
    return results
