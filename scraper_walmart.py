"""Web scraper for Walmart Canada hair care product pricing.

Uses undetected-chromedriver. Extracts product metadata from Walmart's
__NEXT_DATA__ JSON (Next.js) and prices from the rendered DOM, since
Walmart loads prices dynamically after the initial page render.
"""

import json
import logging
import os
import re
import time
from urllib.parse import unquote

from selenium.webdriver.common.by import By

from config import WALMART_CONFIG
from database import init_db, insert_price, upsert_product
from scraper_utils import (
    create_chrome_driver,
    dump_page,
    extract_size,
    human_delay,
    parse_price,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(os.path.dirname(__file__), "scraper_walmart.log"), encoding="utf-8"
        ),
    ],
)
log = logging.getLogger(__name__)

# Unpack config
RETAILER = WALMART_CONFIG["retailer"]
BASE_URL = WALMART_CONFIG["base_url"]
BRANDS = WALMART_CONFIG["brands"]
MIN_DELAY = WALMART_CONFIG["min_delay"]
MAX_DELAY = WALMART_CONFIG["max_delay"]
BRAND_DELAY_MIN = WALMART_CONFIG["brand_delay_min"]
BRAND_DELAY_MAX = WALMART_CONFIG["brand_delay_max"]

# Skip non-hair products
SKIP_KEYWORDS = [
    "curling iron", "flat iron", "blow dryer", "hair clipper", "hair trimmer",
    "body wash", "deodorant", "body lotion", "face wash", "toothpaste",
    "razor", "shaving", "soap", "body spray", "antiperspirant",
]

MAX_PAGES = 5


def _extract_next_data(driver) -> dict | None:
    """Extract the __NEXT_DATA__ JSON from the page."""
    try:
        data = driver.execute_script(
            "var el = document.getElementById('__NEXT_DATA__');"
            "return el ? JSON.parse(el.textContent) : null;"
        )
        return data
    except Exception as exc:
        log.debug("Failed to extract __NEXT_DATA__: %s", exc)
        return None


def _extract_dom_prices(driver) -> dict:
    """Extract prices from the rendered DOM, keyed by product URL path.

    Returns {url_path: {"price": float, "was_price": float|None}}.
    Uses the product link href within each card for matching instead of
    data-item-id (which doesn't correspond to __NEXT_DATA__ IDs).
    """
    prices = {}
    cards = driver.find_elements(By.CSS_SELECTOR, "[data-item-id]")

    for card in cards:
        # Extract the product link URL from this card
        try:
            link_el = card.find_element(By.CSS_SELECTOR, 'a[href*="/ip/"]')
            href = link_el.get_attribute("href") or ""
        except Exception:
            continue

        # Normalize: URL-decode, extract path, strip all query params
        url_path = unquote(href.split("walmart.ca")[-1] if "walmart.ca" in href else href)
        url_path = url_path.split("?")[0]  # Strip query params for clean matching
        if not url_path:
            continue

        # Find price element within this card
        try:
            price_el = card.find_element(By.CSS_SELECTOR, '[data-automation-id="product-price"]')
            price_text = driver.execute_script("return arguments[0].textContent || '';", price_el)
        except Exception:
            continue

        if not price_text:
            continue

        # Parse "Now $15.97current price Now $15.97, Was $17.57$17.57"
        # or "$9.67current price $9.67"
        current = None
        was = None

        # Extract "Was" price first
        was_match = re.search(r'Was\s*\$(\d+\.?\d*)', price_text)
        if was_match:
            was = float(was_match.group(1))

        # Extract current price (first dollar amount)
        current_match = re.search(r'\$(\d+\.?\d*)', price_text)
        if current_match:
            current = float(current_match.group(1))

        if current is not None:
            prices[url_path] = {"price": current, "was_price": was}

    return prices


def _extract_products(driver, brand_name: str, brand_keywords: list[str]) -> list[dict]:
    """Extract products by combining __NEXT_DATA__ metadata with DOM prices."""
    data = _extract_next_data(driver)
    if not data:
        return []

    # Get DOM prices keyed by item_id
    dom_prices = _extract_dom_prices(driver)
    log.info("  DOM prices extracted for %d items", len(dom_prices))

    products = []
    try:
        page_props = data.get("props", {}).get("pageProps", {})
        initial_data = page_props.get("initialData", {})
        search_result = initial_data.get("searchResult", {})
        item_stacks = search_result.get("itemStacks", [])

        for stack in item_stacks:
            items = stack.get("items", [])
            for item in items:
                if item.get("__typename") != "Product":
                    continue

                name = item.get("name", "")
                if not name:
                    continue

                # Skip non-hair products
                name_lower = name.lower()
                if any(kw in name_lower for kw in SKIP_KEYWORDS):
                    continue

                # Verify brand match
                item_brand = (item.get("brand") or "").lower()
                combined = f"{name_lower} {item_brand}"
                if not any(kw in combined for kw in brand_keywords):
                    continue

                # URL
                canonical = item.get("canonicalUrl", "")
                url = BASE_URL + canonical if canonical else ""
                if not url:
                    continue

                # Image
                image_url = item.get("imageInfo", {}).get("thumbnailUrl", "")

                # Size
                size = extract_size(name)

                # Price — from DOM prices, matched by canonical URL path (no query params)
                canonical_path = canonical.split("?")[0]
                dom_price = dom_prices.get(canonical_path, {})

                price = dom_price.get("price")
                was_price = dom_price.get("was_price")

                regular_price = price
                sale_price = None
                if was_price and price and price < was_price:
                    regular_price = was_price
                    sale_price = price
                    price = was_price

                products.append({
                    "brand": brand_name,
                    "name": name,
                    "url": url,
                    "price": price,
                    "regular_price": regular_price,
                    "sale_price": sale_price,
                    "image_url": image_url,
                    "size": size,
                })

    except Exception as exc:
        log.error("Error parsing product data: %s", exc)

    return products


def _add_page_param(url: str, page: int) -> str:
    """Append or replace &page= parameter in a URL."""
    if "?" in url:
        url = re.sub(r'[&?]page=\d+', '', url)
        return f"{url}&page={page}"
    else:
        return f"{url}?page={page}"


def scrape_brand(driver, brand_name: str, brand_config: dict) -> int:
    """Scrape all products for a single brand from Walmart.ca."""
    base_url = brand_config["url"]
    keywords = brand_config["keywords"]
    log.info("Scraping %s  →  %s", brand_name, base_url)

    total_saved = 0
    seen_urls = set()

    for page_num in range(1, MAX_PAGES + 1):
        url = _add_page_param(base_url, page_num) if page_num > 1 else base_url
        log.info("  Page %d: %s", page_num, url)

        driver.get(url)
        time.sleep(10)

        # Scroll to trigger lazy loading of prices
        for _ in range(5):
            driver.execute_script("window.scrollBy(0, window.innerHeight)")
            time.sleep(1.5)

        products = _extract_products(driver, brand_name, keywords)

        if not products:
            if page_num == 1:
                log.warning("No products found for %s on page 1", brand_name)
                dump_page(driver, f"walmart_{brand_name}_empty")
            else:
                log.info("  No more products on page %d — stopping", page_num)
            break

        page_saved = 0
        for p in products:
            if p["url"] in seen_urls:
                continue
            seen_urls.add(p["url"])

            try:
                product_id = upsert_product(
                    brand=p["brand"],
                    product_name=p["name"],
                    url=p["url"],
                    size=p["size"],
                    image_url=p["image_url"],
                    retailer=RETAILER,
                )
                if product_id is None:
                    continue
                insert_price(
                    product_id=product_id,
                    price=p["price"],
                    regular_price=p["regular_price"],
                    sale_price=p["sale_price"],
                )
                page_saved += 1
            except Exception as exc:
                log.debug("Error saving product %s: %s", p.get("name"), exc)

        total_saved += page_saved
        log.info("  Page %d: saved %d new products", page_num, page_saved)

        if len(products) < 30:
            break

        human_delay(MIN_DELAY, MAX_DELAY)

    log.info("  %s: saved %d products total", brand_name, total_saved)
    return total_saved


def run():
    """Main scraper entry point for Walmart Canada."""
    init_db()
    log.info("Starting Walmart scraper for %d brands", len(BRANDS))

    driver = create_chrome_driver()
    time.sleep(3)

    # Warm up browser
    try:
        driver.get("https://www.google.com")
        time.sleep(3)
    except Exception:
        pass

    try:
        grand_total = 0
        for brand_name, brand_config in BRANDS.items():
            try:
                count = scrape_brand(driver, brand_name, brand_config)
                grand_total += count
            except Exception as exc:
                log.error("Unhandled error for %s: %s", brand_name, exc)

            human_delay(BRAND_DELAY_MIN, BRAND_DELAY_MAX)

        log.info("Walmart scraping complete. Total products saved: %d", grand_total)
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    run()
