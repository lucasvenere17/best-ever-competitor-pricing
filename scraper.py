"""Web scraper for Shoppers Drug Mart hair care product pricing.

Uses undetected-chromedriver to bypass Akamai bot detection and scrape
product names, prices, and sizes for configured competitor brands.
"""

import logging
import os
import random
import re
import time
from datetime import datetime
from urllib.parse import urlencode

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By

from config import (
    BASE_URL,
    BRAND_DELAY_MAX,
    BRAND_DELAY_MIN,
    BRAND_NAV_PARAM,
    BRANDS,
    HAIR_CARE_URL,
    HEADLESS,
    MAX_DELAY,
    MIN_DELAY,
    TIMEOUT_MS,
)
from database import init_db, insert_price, upsert_product

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(os.path.dirname(__file__), "scraper.log"), encoding="utf-8"
        ),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CSS selectors — site uses Chakra UI with data-testid attributes
# ---------------------------------------------------------------------------
PRODUCT_CARD_SELECTORS = [
    "[data-testid='product-grid'] .chakra-linkbox",
]

PRODUCT_NAME_SELECTORS = [
    "[data-testid='product-title']",
    "h3",
]

PRICE_SELECTORS = [
    "[data-testid='price']",
]

REGULAR_PRICE_SELECTORS = [
    "[data-testid='was-price']",
    "p[style*='line-through']",
    "span[style*='line-through']",
]

LINK_SELECTORS = [
    "a.chakra-linkbox__overlay",
    "a[href*='/p/']",
]

IMAGE_SELECTORS = [
    "[data-testid='product-image'] img",
    ".chakra-linkbox img",
    "img[src*='product']",
]

SIZE_SELECTORS = [
    "[data-testid='product-package-size']",
]

_selector_cache: dict[str, str] = {}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SIZE_PATTERN = re.compile(r"(\d+\.?\d*)\s*(ml|l|oz|fl\.?\s*oz|g|kg)\b", re.IGNORECASE)
PRICE_PATTERN = re.compile(r"\$?\s?(\d+\.?\d{0,2})")


def build_brand_url(brand_code: str, page: int = 1) -> str:
    params = urlencode({
        "nav": BRAND_NAV_PARAM,
        "brandName": brand_code,
        "page": page,
    })
    return f"{HAIR_CARE_URL}?{params}"


def parse_price(text: str) -> float | None:
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
    if not text:
        return None
    m = SIZE_PATTERN.search(text)
    return m.group(0).strip() if m else None


def human_delay(lo: float = MIN_DELAY, hi: float = MAX_DELAY):
    time.sleep(random.uniform(lo, hi))


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
# Debug helper
# ---------------------------------------------------------------------------

def dump_page(driver, brand_name: str):
    debug_dir = os.path.join(os.path.dirname(__file__), "debug")
    os.makedirs(debug_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r'[^\w\-]', '_', brand_name)
    path = os.path.join(debug_dir, f"{safe_name}_{ts}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(driver.page_source)
    log.warning("Debug page dump saved to %s", path)


# ---------------------------------------------------------------------------
# Core scraper
# ---------------------------------------------------------------------------

def scrape_brand(driver, brand_name: str, brand_code: str) -> int:
    """Scrape all products for a single brand. Returns count of products saved."""
    url = build_brand_url(brand_code, page=1)
    log.info("Scraping %s  →  %s", brand_name, url)

    driver.get(url)
    time.sleep(8)  # wait for JS rendering + Akamai challenge

    # Check if blocked
    if "Access Denied" in driver.page_source[:1000]:
        log.error("Access denied for %s — skipping", brand_name)
        return 0

    total_saved = 0
    seen_urls = set()  # track product URLs to detect duplicates
    current_page = 1
    max_pages = 10

    while current_page <= max_pages:
        # Scroll down to trigger lazy loading
        for _ in range(4):
            driver.execute_script("window.scrollBy(0, window.innerHeight)")
            time.sleep(0.8)

        # Find product cards
        cards = find_elements(driver, PRODUCT_CARD_SELECTORS, cache_key="product_card")

        if not cards:
            if current_page == 1:
                log.warning("No product cards found for %s — dumping page", brand_name)
                dump_page(driver, brand_name)
            break

        log.info("  Page %d: found %d product cards", current_page, len(cards))

        page_saved = 0
        for card in cards:
            try:
                name = find_one_text(card, PRODUCT_NAME_SELECTORS, cache_key="product_name")
                if not name:
                    continue  # skip cards with no product title

                href = find_one_attr(card, LINK_SELECTORS, "href", cache_key="product_link")
                if href and not href.startswith("http"):
                    href = BASE_URL + href
                if not href:
                    href = f"{driver.current_url}#product-{hash(name)}"

                # Skip if we've already seen this product (duplicate page)
                if href in seen_urls:
                    continue
                seen_urls.add(href)

                price_text = find_one_text(card, PRICE_SELECTORS, cache_key="price")
                regular_text = find_one_text(card, REGULAR_PRICE_SELECTORS, cache_key="regular_price")

                price = parse_price(price_text)
                regular_price = parse_price(regular_text)

                sale_price = None
                if regular_price and price and price < regular_price:
                    sale_price = price
                    price = regular_price
                elif regular_price and price and price >= regular_price:
                    regular_price = price
                else:
                    regular_price = price

                image_url = find_one_attr(card, IMAGE_SELECTORS, "src", cache_key="product_image")

                package_size = find_one_text(card, SIZE_SELECTORS, cache_key="product_size")
                full_text = card.text or ""
                size = package_size or extract_size(name) or extract_size(full_text)

                product_id = upsert_product(
                    brand=brand_name,
                    product_name=name,
                    url=href,
                    size=size,
                    image_url=image_url,
                )
                insert_price(
                    product_id=product_id,
                    price=price,
                    regular_price=regular_price,
                    sale_price=sale_price,
                )
                total_saved += 1
                page_saved += 1

            except Exception as exc:
                log.debug("Error extracting product from card: %s", exc)
                continue

        # If no new products were saved on this page, stop paginating
        if page_saved == 0:
            log.info("  No new products on page %d — stopping pagination", current_page)
            break

        log.info("  Page %d: saved %d new products", current_page, page_saved)

        # Pagination — go to next page via URL
        current_page += 1
        next_url = build_brand_url(brand_code, page=current_page)
        driver.get(next_url)
        time.sleep(6)

        if "Access Denied" in driver.page_source[:1000]:
            break

        human_delay()

    log.info("  %s: saved %d products across %d page(s)", brand_name, total_saved, current_page - 1)
    return total_saved


def run():
    """Main scraper entry point."""
    init_db()
    log.info("Starting scraper for %d brands", len(BRANDS))

    options = uc.ChromeOptions()
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    if HEADLESS:
        options.add_argument("--headless=new")

    driver = uc.Chrome(options=options, use_subprocess=True, version_main=145)
    driver.set_page_load_timeout(TIMEOUT_MS // 1000)

    try:
        grand_total = 0
        for brand_name, brand_code in BRANDS.items():
            try:
                count = scrape_brand(driver, brand_name, brand_code)
                grand_total += count
            except Exception as exc:
                log.error("Unhandled error for %s: %s", brand_name, exc)

            human_delay(BRAND_DELAY_MIN, BRAND_DELAY_MAX)

        log.info("Scraping complete. Total products saved: %d", grand_total)
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    run()
