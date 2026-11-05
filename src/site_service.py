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
CACHE_SECONDS = 3600
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
