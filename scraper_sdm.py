"""Web scraper for Shoppers Drug Mart hair care product pricing.

Uses undetected-chromedriver to bypass Akamai bot detection and scrape
product names, prices, and sizes for configured competitor brands.
"""

import logging
import os
import time
from urllib.parse import urlencode

from config import SDM_CONFIG
from database import init_db, insert_price, upsert_product
from scraper_utils import (
    create_chrome_driver,
    dump_page,
    extract_size,
    find_elements,
    find_one_attr,
    find_one_text,
    human_delay,
    parse_price,
)

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

# Unpack SDM config
RETAILER = SDM_CONFIG["retailer"]
BASE_URL = SDM_CONFIG["base_url"]
HAIR_CARE_URL = SDM_CONFIG["hair_care_url"]
BRAND_NAV_PARAM = SDM_CONFIG["brand_nav_param"]
BRANDS = SDM_CONFIG["brands"]
BRAND_URL_KEYWORDS = SDM_CONFIG["brand_url_keywords"]
MIN_DELAY = SDM_CONFIG["min_delay"]
MAX_DELAY = SDM_CONFIG["max_delay"]
BRAND_DELAY_MIN = SDM_CONFIG["brand_delay_min"]
BRAND_DELAY_MAX = SDM_CONFIG["brand_delay_max"]

# Keywords that indicate a product is a tool/appliance (not a hair care product)
SKIP_KEYWORDS = ["curling iron", "flat iron", "blow dryer", "curling wand", "hair clipper", "hair trimmer"]

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_brand_url(brand_code: str, page: int = 1) -> str:
    params = urlencode({
        "nav": BRAND_NAV_PARAM,
        "brandName": brand_code,
        "page": page,
    })
    return f"{HAIR_CARE_URL}?{params}"


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
    seen_urls = set()
    current_page = 1
    max_pages = 10

    while current_page <= max_pages:
        # Scroll down to trigger lazy loading
        for _ in range(4):
            driver.execute_script("window.scrollBy(0, window.innerHeight)")
            time.sleep(0.8)

        cards = find_elements(driver, PRODUCT_CARD_SELECTORS, cache_key="sdm_product_card")

        if not cards:
            if current_page == 1:
                log.warning("No product cards found for %s — dumping page", brand_name)
                dump_page(driver, f"sdm_{brand_name}")
            break

        log.info("  Page %d: found %d product cards", current_page, len(cards))

        page_saved = 0
        for card in cards:
            try:
                name = find_one_text(card, PRODUCT_NAME_SELECTORS, cache_key="sdm_product_name")
                if not name:
                    continue

                name_lower = name.lower()
                if any(kw in name_lower for kw in SKIP_KEYWORDS):
                    continue

                href = find_one_attr(card, LINK_SELECTORS, "href", cache_key="sdm_product_link")
                if href and not href.startswith("http"):
                    href = BASE_URL + href
                if not href:
                    href = f"{driver.current_url}#product-{hash(name)}"

                url_keywords = BRAND_URL_KEYWORDS.get(brand_name, [])
                if url_keywords and not any(kw in href.lower() for kw in url_keywords):
                    continue

                if href in seen_urls:
                    continue
                seen_urls.add(href)

                price_text = find_one_text(card, PRICE_SELECTORS, cache_key="sdm_price")
                regular_text = find_one_text(card, REGULAR_PRICE_SELECTORS, cache_key="sdm_regular_price")

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

                image_url = find_one_attr(card, IMAGE_SELECTORS, "src", cache_key="sdm_product_image")

                package_size = find_one_text(card, SIZE_SELECTORS, cache_key="sdm_product_size")
                full_text = card.text or ""
                size = package_size or extract_size(name) or extract_size(full_text)

                product_id = upsert_product(
                    brand=brand_name,
                    product_name=name,
                    url=href,
                    size=size,
                    image_url=image_url,
                    retailer=RETAILER,
                )
                if product_id is None:
                    continue
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

        if page_saved == 0:
            log.info("  No new products on page %d — stopping pagination", current_page)
            break

        log.info("  Page %d: saved %d new products", current_page, page_saved)

        current_page += 1
        next_url = build_brand_url(brand_code, page=current_page)
        driver.get(next_url)
        time.sleep(6)

        if "Access Denied" in driver.page_source[:1000]:
            break

        human_delay(MIN_DELAY, MAX_DELAY)

    log.info("  %s: saved %d products across %d page(s)", brand_name, total_saved, current_page - 1)
    return total_saved


def run():
    """Main scraper entry point for Shoppers Drug Mart."""
    init_db()
    log.info("Starting Shoppers Drug Mart scraper for %d brands", len(BRANDS))

    driver = create_chrome_driver()

    try:
        grand_total = 0
        for brand_name, brand_code in BRANDS.items():
            try:
                count = scrape_brand(driver, brand_name, brand_code)
                grand_total += count
            except Exception as exc:
                log.error("Unhandled error for %s: %s", brand_name, exc)

            human_delay(BRAND_DELAY_MIN, BRAND_DELAY_MAX)

        log.info("Shoppers Drug Mart scraping complete. Total products saved: %d", grand_total)
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    run()
