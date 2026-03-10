"""Web scraper for Sephora hair care product pricing.

Uses undetected-chromedriver to bypass Akamai bot detection and scrape
product names, prices, and sizes for configured competitor brands.
Targets Sephora Canada (sephora.com/ca/en/).
"""

import logging
import os
import time

from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException

from config import SEPHORA_CONFIG
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
            os.path.join(os.path.dirname(__file__), "scraper_sephora.log"), encoding="utf-8"
        ),
    ],
)
log = logging.getLogger(__name__)

# Unpack Sephora config
RETAILER = SEPHORA_CONFIG["retailer"]
BASE_URL = SEPHORA_CONFIG["base_url"]
BRAND_URL_TEMPLATE = SEPHORA_CONFIG["brand_url_template"]
BRANDS = SEPHORA_CONFIG["brands"]
MIN_DELAY = SEPHORA_CONFIG["min_delay"]
MAX_DELAY = SEPHORA_CONFIG["max_delay"]
BRAND_DELAY_MIN = SEPHORA_CONFIG["brand_delay_min"]
BRAND_DELAY_MAX = SEPHORA_CONFIG["brand_delay_max"]

# Max "Show More" clicks before giving up (safety limit)
MAX_SHOW_MORE_CLICKS = 20


def _click_show_more(driver) -> bool:
    """Click the 'Show More Products' button if present. Returns True if clicked."""
    try:
        # Try multiple selectors for the show-more button
        for selector in [
            "button[data-at*='show_more']",
            "button[data-comp*='ShowMore']",
        ]:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, selector)
                if btn.is_displayed():
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                    time.sleep(0.5)
                    btn.click()
                    return True
            except NoSuchElementException:
                continue

        # Fallback: find button by text content
        buttons = driver.find_elements(By.TAG_NAME, "button")
        for btn in buttons:
            try:
                txt = btn.text.strip().lower()
                if "show more" in txt and btn.is_displayed():
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                    time.sleep(0.5)
                    btn.click()
                    return True
            except Exception:
                continue

    except Exception as exc:
        log.debug("Error looking for Show More button: %s", exc)

    return False


def _extract_products_from_page(driver, brand_name: str) -> list[dict]:
    """Parse all product data from the currently loaded page.

    Sephora card text structure (one line per element):
        [0] Brand name (e.g. "Olaplex")
        [1] Product name
        [2] Review count (e.g. "282", "2.2K") — skip this
        [3] Price (e.g. "$49.00", "$49.00 - $99.00" for size variants)
        [4+] Optional badges: "NEW", "LIMITED EDITION", etc.
    """
    products = []

    # Find all product links — most stable selector on Sephora's React SPA
    product_links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/product/"]')

    # Deduplicate by href
    seen_hrefs = set()
    unique_cards = []
    for link in product_links:
        href = link.get_attribute("href") or ""
        # Skip non-product links (e.g. subscription promo)
        if "/product/subscription" in href.lower():
            continue
        if href and href not in seen_hrefs:
            seen_hrefs.add(href)
            unique_cards.append(link)

    for link in unique_cards:
        try:
            href = link.get_attribute("href") or ""
            if not href.startswith("http"):
                href = BASE_URL + href

            # Walk up to the product card container
            card = link
            for _ in range(6):
                parent = card.find_element(By.XPATH, "..")
                children = parent.find_elements(By.CSS_SELECTOR, 'a[href*="/product/"]')
                if len(children) > 1:
                    break
                card = parent

            card_text = card.text or ""
            lines = [l.strip() for l in card_text.split("\n") if l.strip()]

            # Need at least brand + name + price
            if len(lines) < 3:
                continue

            # Line 0 = brand, Line 1 = product name
            name = lines[1]
            if not name:
                continue

            # Find the price line (first line containing '$')
            price = None
            regular_price = None
            sale_price = None

            for line in lines:
                if "$" not in line:
                    continue
                # Handle range format "$49.00 - $99.00" (size variants) — take the lower price
                if " - " in line:
                    parts = line.split(" - ")
                    low = parse_price(parts[0])
                    high = parse_price(parts[1]) if len(parts) > 1 else None
                    price = low
                    regular_price = low
                else:
                    price = parse_price(line)
                    regular_price = price
                break  # only use the first price line

            # Image
            image_url = None
            try:
                img = card.find_element(By.TAG_NAME, "img")
                image_url = img.get_attribute("src")
            except Exception:
                pass

            # Size — extract from product name or card text
            size = extract_size(name) or extract_size(card_text)

            products.append({
                "brand": brand_name,
                "name": name,
                "url": href,
                "price": price,
                "regular_price": regular_price,
                "sale_price": sale_price,
                "image_url": image_url,
                "size": size,
            })

        except Exception as exc:
            log.debug("Error extracting product from card: %s", exc)
            continue

    return products


def scrape_brand(driver, brand_name: str, brand_slug: str) -> int:
    """Scrape all products for a single Sephora brand. Returns count of products saved."""
    url = BRAND_URL_TEMPLATE.format(slug=brand_slug)
    log.info("Scraping %s  →  %s", brand_name, url)

    driver.get(url)
    time.sleep(8)  # wait for JS rendering + Akamai challenge

    # Check if blocked
    page_text = driver.page_source[:2000].lower()
    if "access denied" in page_text or "robot" in page_text:
        log.error("Access denied / bot check for %s — skipping", brand_name)
        dump_page(driver, f"sephora_{brand_name}_blocked")
        return 0

    # Scroll and click "Show More" to load all products
    for click_num in range(MAX_SHOW_MORE_CLICKS):
        # Scroll to bottom to trigger lazy loading
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1.5)

        if not _click_show_more(driver):
            log.info("  No more 'Show More' button after %d click(s)", click_num)
            break

        log.info("  Clicked 'Show More' (#%d)", click_num + 1)
        time.sleep(3)
        human_delay(MIN_DELAY, MAX_DELAY)

    # Final scroll to ensure everything is loaded
    for _ in range(3):
        driver.execute_script("window.scrollBy(0, window.innerHeight)")
        time.sleep(0.8)

    # Extract products
    products = _extract_products_from_page(driver, brand_name)

    if not products:
        log.warning("No products found for %s — dumping page", brand_name)
        dump_page(driver, f"sephora_{brand_name}")
        return 0

    # Save to database
    saved = 0
    for p in products:
        try:
            product_id = upsert_product(
                brand=p["brand"],
                product_name=p["name"],
                url=p["url"],
                size=p["size"],
                image_url=p["image_url"],
                retailer=RETAILER,
            )
            insert_price(
                product_id=product_id,
                price=p["price"],
                regular_price=p["regular_price"],
                sale_price=p["sale_price"],
            )
            saved += 1
        except Exception as exc:
            log.debug("Error saving product %s: %s", p.get("name"), exc)

    log.info("  %s: saved %d products", brand_name, saved)
    return saved


def run():
    """Main scraper entry point for Sephora."""
    init_db()
    log.info("Starting Sephora scraper for %d brands", len(BRANDS))

    driver = create_chrome_driver()
    time.sleep(3)  # let Chrome fully initialize

    # Warm up with a neutral page to establish a normal browsing session
    # before hitting Sephora (helps avoid Akamai bot detection)
    try:
        driver.get("https://www.google.com")
        time.sleep(3)
    except Exception:
        pass

    try:
        grand_total = 0
        for brand_name, brand_slug in BRANDS.items():
            try:
                count = scrape_brand(driver, brand_name, brand_slug)
                grand_total += count
            except Exception as exc:
                log.error("Unhandled error for %s: %s", brand_name, exc)

            human_delay(BRAND_DELAY_MIN, BRAND_DELAY_MAX)

        log.info("Sephora scraping complete. Total products saved: %d", grand_total)
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    run()
