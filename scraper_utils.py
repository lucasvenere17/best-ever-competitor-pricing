"""Shared utilities for all retailer scrapers."""

import logging
import os
import random
import re
import time

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By

from config import HEADLESS, TIMEOUT_MS

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------
SIZE_PATTERN = re.compile(r"(\d+\.?\d*)\s*(ml|l|oz|fl\.?\s*oz|g|kg)\b", re.IGNORECASE)
PRICE_PATTERN = re.compile(r"\$?\s?(\d+\.?\d{0,2})")


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_price(text: str) -> float | None:
    """Extract a dollar price from text like '$12.99' or '12.99'."""
    if not text:
        return None
    m = PRICE_PATTERN.search(text.replace(",", ""))
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def extract_size(text: str) -> str | None:
    """Extract a product size string like '300 ml' or '8 oz' from text."""
    if not text:
        return None
    m = SIZE_PATTERN.search(text)
    return m.group(0).strip() if m else None


def human_delay(lo: float = 2, hi: float = 5):
    """Sleep for a random duration between lo and hi seconds."""
    time.sleep(random.uniform(lo, hi))


# ---------------------------------------------------------------------------
# Selenium element helpers
# ---------------------------------------------------------------------------

_selector_cache: dict[str, str] = {}


def find_elements(parent, selectors: list[str], cache_key: str = None):
    """Try each CSS selector and return the first list of matches."""
    if cache_key and cache_key in _selector_cache:
        elems = parent.find_elements(By.CSS_SELECTOR, _selector_cache[cache_key])
        if elems:
            return elems

    for sel in selectors:
        try:
            elems = parent.find_elements(By.CSS_SELECTOR, sel)
            if elems:
                if cache_key:
                    _selector_cache[cache_key] = sel
                    log.info("Selector cache [%s] = %s  (%d hits)", cache_key, sel, len(elems))
                return elems
        except Exception:
            continue
    return []


def find_one_text(parent, selectors: list[str], cache_key: str = None) -> str | None:
    """Return text of the first element matched by any selector."""
    if cache_key and cache_key in _selector_cache:
        try:
            el = parent.find_element(By.CSS_SELECTOR, _selector_cache[cache_key])
            txt = el.text.strip()
            if txt:
                return txt
        except Exception:
            pass

    for sel in selectors:
        try:
            el = parent.find_element(By.CSS_SELECTOR, sel)
            txt = el.text.strip()
            if txt:
                if cache_key:
                    _selector_cache[cache_key] = sel
                return txt
        except Exception:
            continue
    return None


def find_one_attr(parent, selectors: list[str], attr: str, cache_key: str = None) -> str | None:
    """Return an attribute of the first element matched by any selector."""
    if cache_key and cache_key in _selector_cache:
        try:
            el = parent.find_element(By.CSS_SELECTOR, _selector_cache[cache_key])
            val = el.get_attribute(attr)
            if val:
                return val
        except Exception:
            pass

    for sel in selectors:
        try:
            el = parent.find_element(By.CSS_SELECTOR, sel)
            val = el.get_attribute(attr)
            if val:
                if cache_key:
                    _selector_cache[cache_key] = sel
                return val
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Chrome driver setup
# ---------------------------------------------------------------------------

def create_chrome_driver(version_main: int = 145) -> uc.Chrome:
    """Create and return an undetected Chrome driver instance."""
    options = uc.ChromeOptions()
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    if HEADLESS:
        options.add_argument("--headless=new")

    driver = uc.Chrome(options=options, use_subprocess=True, version_main=version_main)
    driver.set_page_load_timeout(TIMEOUT_MS // 1000)
    return driver


# ---------------------------------------------------------------------------
# Debug helper
# ---------------------------------------------------------------------------

def dump_page(driver, label: str):
    """Save the current page source to a debug HTML file."""
    from datetime import datetime
    debug_dir = os.path.join(os.path.dirname(__file__), "debug")
    os.makedirs(debug_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r'[^\w\-]', '_', label)
    path = os.path.join(debug_dir, f"{safe_name}_{ts}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(driver.page_source)
    log.warning("Debug page dump saved to %s", path)
