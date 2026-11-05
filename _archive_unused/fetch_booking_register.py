"""
Standalone script: log in to the TCI ChromeX portal with YOUR OWN credentials and
fetch https://chromex1.tciexpress.in/Booking_Register_Data.aspx

This is a separate, stand-alone file. It does not import, modify, or get imported
by anything in app.py / src/. Run it on its own to test the login + fetch flow.

Credentials are read from environment variables ONLY (never hardcode them here,
never commit them):

    CHROMEX_USER_ID       your employee/staff code
    CHROMEX_PASSWORD      your portal password
    CHROMEX_BRANCH_CODE   your branch code

Usage (PowerShell), without writing secrets to any file:

    $env:CHROMEX_USER_ID="yourcode"
    $env:CHROMEX_PASSWORD="yourpassword"
    $env:CHROMEX_BRANCH_CODE="yourbranch"
    venv313\Scripts\python fetch_booking_register.py

Or load them from .env yourself if you prefer (python-dotenv is already a
dependency of this project) - this script will pick them up either way.
"""
import os
import re
import sys

import requests
from bs4 import BeautifulSoup

BASE = "https://chromex1.tciexpress.in"
LOGIN_URL = f"{BASE}/Login_Module.aspx"
BOOKING_URL = f"{BASE}/Booking_Register_Data.aspx"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
}


def _hidden_fields(soup: BeautifulSoup) -> dict:
    """Collect every <input type=hidden> name/value on the page (ASP.NET WebForms state)."""
    fields = {}
    for inp in soup.find_all("input", {"type": "hidden"}):
        name = inp.get("name")
        if name:
            fields[name] = inp.get("value", "")
    return fields


def login(session: requests.Session, user_id: str, password: str, branch_code: str):
    """POST the login form. Returns (ok, response)."""
    get_resp = session.get(LOGIN_URL, headers=HEADERS, timeout=20)
    get_resp.raise_for_status()
    soup = BeautifulSoup(get_resp.text, "lxml")

    # NOTE: the real login form's "txtuserid" field is disabled/readonly in the HTML, so a real
    # browser never submits it at all - sending it caused a server-side crash. The server already
    # knows the user from a prior session/cookie baked into __VIEWSTATE, so we must NOT send it.
    payload = _hidden_fields(soup)
    payload.update({
        "LASTFOCUS": "",
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "txtpassword": password,
        "txtbranch": branch_code,
        "btnlogin": "Login",
    })

    post_resp = session.post(LOGIN_URL, data=payload, headers=HEADERS, timeout=20)
    post_resp.raise_for_status()

    # Heuristics: a failed login usually re-shows the same login form (txtuserid field present again)
    still_on_login = "txtpassword" in post_resp.text and "btnlogin" in post_resp.text
    return (not still_on_login), post_resp


def fetch_booking_register(session: requests.Session) -> requests.Response:
    resp = session.get(BOOKING_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp


def main():
    user_id = os.getenv("CHROMEX_USER_ID")
    password = os.getenv("CHROMEX_PASSWORD")
    branch_code = os.getenv("CHROMEX_BRANCH_CODE")

    missing = [n for n, v in [
        ("CHROMEX_USER_ID", user_id),
        ("CHROMEX_PASSWORD", password),
        ("CHROMEX_BRANCH_CODE", branch_code),
    ] if not v]
    if missing:
        print(f"Missing environment variable(s): {', '.join(missing)}")
        print("Set them first, e.g. in PowerShell:")
        print('  $env:CHROMEX_USER_ID="..."; $env:CHROMEX_PASSWORD="..."; $env:CHROMEX_BRANCH_CODE="..."')
        sys.exit(1)

    session = requests.Session()

    print(f"[1] Logging in to {LOGIN_URL} as user '{user_id}', branch '{branch_code}' ...")
    ok, resp = login(session, user_id, password, branch_code)

    if not ok:
        print("[FAIL] Login did not succeed - the response still looks like the login page.")
        print("       Saving the raw response to login_response_debug.html for inspection.")
        with open("login_response_debug.html", "w", encoding="utf-8") as f:
            f.write(resp.text)
        # Try to surface any visible error/alert text from the page
        msgs = re.findall(r'alert\(["\']([^"\']{3,120})["\']\)', resp.text)
        if msgs:
            print("       Possible error message(s) found on page:", msgs)
        sys.exit(2)

    print("[2] Login looked successful. Cookies now held by the session:")
    for c in session.cookies:
        print(f"    {c.name} (domain={c.domain})")

    print(f"[3] Fetching {BOOKING_URL} ...")
    booking_resp = fetch_booking_register(session)

    if "window.close()" in booking_resp.text or "close this window" in booking_resp.text.lower():
        print("[FAIL] The booking page still rejected us (session not recognized by that page).")
        print("       Saving response to booking_response_debug.html for inspection.")
        with open("booking_response_debug.html", "w", encoding="utf-8") as f:
            f.write(booking_resp.text)
        sys.exit(3)

    out_file = "booking_register_raw.html"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(booking_resp.text)
    print(f"[OK] Booking register page fetched ({len(booking_resp.text)} bytes). Saved to {out_file}")

    soup = BeautifulSoup(booking_resp.text, "lxml")
    tables = soup.find_all("table")
    print(f"[INFO] Found {len(tables)} <table> element(s) on the page.")
    for i, t in enumerate(tables):
        rows = t.find_all("tr")
        if len(rows) > 1:
            print(f"    table[{i}]: {len(rows)} rows - preview: {rows[0].get_text(' ', strip=True)[:120]}")


if __name__ == "__main__":
    main()
