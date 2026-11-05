"""
Standalone script: open the TCI ChromeX portal using YOUR OWN existing Chrome profile
(the one already signed in / remembered on this PC) and fetch
https://chromex1.tciexpress.in/Booking_Register_Data.aspx

Separate, stand-alone file. Does not import, modify, or get imported by app.py / src/.

Why this approach: the ChromeX login page is tied to a "remembered" session/cookie and
possibly machine identity, and crashes on a truly cookie-less request (confirmed both via
raw HTTP and in an Incognito browser window). Driving your REAL Chrome profile sidesteps
all of that - it reuses the exact cookies/session your normal browsing already has.

IMPORTANT: Close all open Chrome windows before running this (a Chrome profile can only
be used by one Chrome process at a time - including a Selenium-driven one).

Usage:
    venv313\\Scripts\\python fetch_booking_register_selenium.py

Optional env vars (only needed if NOT already logged in, i.e. a real login form appears):
    CHROMEX_USER_ID, CHROMEX_PASSWORD, CHROMEX_BRANCH_CODE
"""
import os
import shutil
import sys
import tempfile
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

CHROME_USER_DATA_DIR = (
    r"C:\Users\XCRP-IT-KAPIL SHARMA\AppData\Local\Google\Chrome\User Data"
)
# Detected on this PC: the profile folder named "Profile 1" is labeled "tciexpress.in"
# in Chrome's profile switcher - almost certainly the one already signed in to ChromeX.
CHROME_PROFILE_DIR = "Profile 1"

LOGIN_URL = "https://chromex1.tciexpress.in/Login_Module.aspx"
BOOKING_URL = "https://chromex1.tciexpress.in/Booking_Register_Data.aspx"
OUT_FILE = "booking_register_rendered.html"


def _prepare_profile_copy() -> str:
    """Copy just what's needed (cookies/session) from the real profile into a throwaway
    temp folder, so we never need your real Chrome closed and never touch the original."""
    # A fresh unique folder each run avoids fighting over a leftover lock file from a
    # previous run's Chrome process that hasn't fully released it yet.
    tmp_root = tempfile.mkdtemp(prefix="chromex_selenium_profile_")

    local_state_src = os.path.join(CHROME_USER_DATA_DIR, "Local State")
    if os.path.exists(local_state_src):
        shutil.copy2(local_state_src, os.path.join(tmp_root, "Local State"))

    src_profile = os.path.join(CHROME_USER_DATA_DIR, CHROME_PROFILE_DIR)
    dst_profile = os.path.join(tmp_root, CHROME_PROFILE_DIR)
    shutil.copytree(src_profile, dst_profile, ignore=shutil.ignore_patterns(
        "Cache", "Code Cache", "GPUCache", "DawnCache", "GrShaderCache", "*.log",
    ))
    return tmp_root


def build_driver() -> webdriver.Chrome:
    print("[INFO] Copying your Chrome profile's cookies/session to a temp folder ...")
    tmp_user_data_dir = _prepare_profile_copy()
    print(f"[INFO] Using temp profile copy at: {tmp_user_data_dir}")

    opts = Options()
    opts.add_argument(f"--user-data-dir={tmp_user_data_dir}")
    opts.add_argument(f"--profile-directory={CHROME_PROFILE_DIR}")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    # Keep it visible so you can see/intervene if something unexpected pops up
    opts.add_experimental_option("detach", True)
    return webdriver.Chrome(options=opts)


def looks_like_login_form(driver) -> bool:
    try:
        driver.find_element(By.ID, "txtpassword")
        return True
    except Exception:
        return False


def try_login(driver):
    user_id = os.getenv("CHROMEX_USER_ID")
    password = os.getenv("CHROMEX_PASSWORD")
    branch_code = os.getenv("CHROMEX_BRANCH_CODE")

    if not password or not branch_code:
        print("[FAIL] A login form appeared (session wasn't already remembered) but")
        print("       CHROMEX_PASSWORD / CHROMEX_BRANCH_CODE env vars aren't set.")
        print("       Either log in manually once in this same Chrome profile, or set:")
        print('         $env:CHROMEX_USER_ID="..."; $env:CHROMEX_PASSWORD="..."; $env:CHROMEX_BRANCH_CODE="..."')
        sys.exit(2)

    print(f"[INFO] Filling in the real login form (user={user_id}, branch={branch_code}) ...")
    try:
        uid_field = driver.find_element(By.ID, "txtuserid")
        if uid_field.get_attribute("disabled") is None and user_id:
            uid_field.clear()
            uid_field.send_keys(user_id)
    except Exception:
        pass

    driver.find_element(By.ID, "txtbranch").clear()
    driver.find_element(By.ID, "txtbranch").send_keys(branch_code)
    driver.find_element(By.ID, "txtpassword").clear()
    driver.find_element(By.ID, "txtpassword").send_keys(password)
    driver.find_element(By.ID, "btnlogin").click()

    WebDriverWait(driver, 20).until(lambda d: not looks_like_login_form(d))


def main():
    print("[1] Launching Chrome with your existing 'tciexpress.in' profile ...")
    print("    (Make sure all other Chrome windows are closed first.)")
    driver = build_driver()

    try:
        print(f"[2] Opening {LOGIN_URL} ...")
        driver.get(LOGIN_URL)

        # Dismiss any alert() dialog if one pops up (seen during testing)
        try:
            WebDriverWait(driver, 3).until(EC.alert_is_present())
            alert = driver.switch_to.alert
            print(f"[INFO] Dismissing an alert dialog: {alert.text}")
            alert.accept()
        except Exception:
            pass

        time.sleep(1)

        if looks_like_login_form(driver):
            try_login(driver)
        else:
            print("[INFO] Already logged in (session was remembered) - no login form shown.")

        print(f"[3] Navigating to {BOOKING_URL} ...")
        driver.get(BOOKING_URL)
        time.sleep(2)

        try:
            WebDriverWait(driver, 3).until(EC.alert_is_present())
            alert = driver.switch_to.alert
            print(f"[INFO] Dismissing an alert dialog: {alert.text}")
            alert.accept()
        except Exception:
            pass

        from_date = os.getenv("CHROMEX_FROM_DATE")
        to_date = os.getenv("CHROMEX_TO_DATE")
        try:
            if from_date:
                f = driver.find_element(By.ID, "txtfromdate")
                f.clear()
                f.send_keys(from_date)
            if to_date:
                t = driver.find_element(By.ID, "txttodate")
                t.clear()
                t.send_keys(to_date)
            print("[4] Clicking 'View' to load booking data for the selected date range ...")
            driver.find_element(By.ID, "btnview").click()
            time.sleep(2)
        except Exception as e:
            print(f"[WARN] Could not click 'View' (page layout may differ): {e}")

        print(f"[INFO] Current URL after click: {driver.current_url}")
        page_text = driver.page_source
        if "close this window" in page_text.lower():
            print("[FAIL] Booking page rejected the session (same 'close this window' message).")
        else:
            with open(OUT_FILE, "w", encoding="utf-8") as f:
                f.write(page_text)
            print(f"[OK] Saved rendered page to {OUT_FILE} ({len(page_text)} bytes).")

            tables = driver.find_elements(By.TAG_NAME, "table")
            print(f"[INFO] Found {len(tables)} <table> element(s) on the page.")
            for i, t in enumerate(tables):
                rows = t.find_elements(By.TAG_NAME, "tr")
                if len(rows) > 1:
                    preview = rows[0].text.replace("\n", " ")[:120]
                    print(f"    table[{i}]: {len(rows)} rows - preview: {preview}")

        print("\n[DONE] Browser window left open so you can look around / log in if needed.")
        print("       Close it manually when finished.")

    except Exception as e:
        print(f"[ERROR] {e}")
        raise


if __name__ == "__main__":
    main()
