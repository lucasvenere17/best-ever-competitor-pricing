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
    """Parse all product data from the currently loaded page."""
    products = []

    # Find all product links — most stable selector on Sephora's React SPA
    product_links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/product/"]')

    # Deduplicate by href (multiple <a> tags can point to the same product)
    seen_hrefs = set()
    unique_cards = []
    for link in product_links:
        href = link.get_attribute("href") or ""
        if href and href not in seen_hrefs:
            seen_hrefs.add(href)
            unique_cards.append(link)

    for link in unique_cards:
        try:
            href = link.get_attribute("href") or ""
            if not href.startswith("http"):
                href = BASE_URL + href

            # Walk up to the product card container
            # Sephora wraps each product in a parent div; we go up a few levels
            card = link
            for _ in range(5):
                parent = card.find_element(By.XPATH, "..")
                # Stop if parent is too large (likely the grid itself)
                children = parent.find_elements(By.CSS_SELECTOR, 'a[href*="/product/"]')
                if len(children) > 1:
                    break
                card = parent

            # Product name — text of the link itself, or nearby elements
            name = ""
            try:
                name = link.text.strip()
            except Exception:
                pass
            if not name:
                # Try getting text from the card container
                card_text = card.text or ""
                lines = [l.strip() for l in card_text.split("\n") if l.strip()]
                # Usually: brand, product name, price — skip brand line
                if len(lines) >= 2:
                    name = lines[1]
                elif lines:
                    name = lines[0]

            if not name:
                continue

            # Price — look for text with dollar signs in the card
            card_text = card.text or ""
            lines = [l.strip() for l in card_text.split("\n") if l.strip()]

            price = None
            regular_price = None
            sale_price = None

            # Look for price lines (contain '$')
            price_lines = [l for l in lines if "$" in l]
            if price_lines:
                # If multiple prices, first is usually regular, second is sale
                prices_found = []
                for pl in price_lines:
                    p = parse_price(pl)
                    if p:
                        prices_found.append(p)

                if len(prices_found) == 1:
                    price = prices_found[0]
                    regular_price = price
                elif len(prices_found) >= 2:
                    # Higher is regular, lower is sale
                    prices_found.sort(reverse=True)
                    regular_price = prices_found[0]
                    sale_price = prices_found[1]
                    price = regular_price

            # Image
            image_url = None
            try:
                img = card.find_element(By.TAG_NAME, "img")
                image_url = img.get_attribute("src")
            except Exception:
                pass

            # Size — try to extract from product name or card text
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
