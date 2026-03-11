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
                    try:
                        btn.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", btn)
                    return True
            except NoSuchElementException:
                continue

        # Fallback: find button by text content ("Show More Products")
        buttons = driver.find_elements(By.TAG_NAME, "button")
        for btn in buttons:
            try:
                txt = btn.text.strip().lower()
                if "show more products" in txt and btn.is_displayed():
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                    time.sleep(0.5)
                    try:
                        btn.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", btn)
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

            # On Sephora, the <a> tag IS the card — its text contains everything.
            # .text returns newline-separated lines; textContent returns one blob.
            link_text = link.text or ""
            used_inner_text = bool(link_text.strip())
            if not used_inner_text:
                link_text = driver.execute_script("return arguments[0].innerText || arguments[0].textContent || '';", link)
            lines = [l.strip() for l in link_text.split("\n") if l.strip()]

            # Extract product name using data attribute (most reliable)
            name = link.get_attribute("data-cnstrc-item-name") or ""

            # Fallback: parse from lines (line 0 = brand, line 1 = name)
            if not name and len(lines) >= 2:
                name = lines[1]

            if not name:
                continue

            # Extract price — from data attribute or text
            price = None
            regular_price = None
            sale_price = None

            data_price = link.get_attribute("data-cnstrc-item-price") or ""
            if data_price:
                # Format: "27.00 - $67.50" or "35.00"
                if " - " in data_price:
                    low = parse_price(data_price.split(" - ")[0])
                    price = low
                    regular_price = low
                else:
                    price = parse_price(data_price)
                    regular_price = price
            else:
                # Fallback: scan lines for $ sign
                for line in lines:
                    if "$" not in line:
                        continue
                    if "value" in line.lower():
                        continue
                    if " - " in line:
                        low = parse_price(line.split(" - ")[0])
                        price = low
                        regular_price = low
                    else:
                        price = parse_price(line)
                        regular_price = price
                    break

            # Image — look inside the link element
            image_url = None
            try:
                img = link.find_element(By.TAG_NAME, "img")
                image_url = img.get_attribute("src")
            except Exception:
                pass

            # Size — extract from product name or full text
            size = extract_size(name) or extract_size(link_text)

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

    # Dismiss cookie consent banner if present (blocks clicks otherwise)
    try:
        driver.execute_script("""
            // Remove Clarip/CookieConsent banner
            var banner = document.querySelector('[aria-label*="Cookie Consent"]') ||
                         document.querySelector('.cc-window') ||
                         document.querySelector('.cc-banner');
            if (banner) banner.remove();
            // Click any dismiss/accept buttons that might exist
            var btns = document.querySelectorAll('.cc-btn, .cc-dismiss, .cc-allow');
            btns.forEach(function(b) { try { b.click(); } catch(e) {} });
        """)
        time.sleep(0.5)
    except Exception:
        pass

    # Scroll down gradually to trigger lazy loading of all products
    # Using scrollBy instead of scrollTo(bottom) — Sephora's lazy load
    # needs the viewport to pass through each section
    for _ in range(30):
        driver.execute_script("window.scrollBy(0, window.innerHeight)")
        time.sleep(1)
        at_bottom = driver.execute_script(
            "return (window.scrollY + window.innerHeight) >= document.body.scrollHeight - 50"
        )
        if at_bottom:
            break

    # Click "Show More" to load additional product batches
    for click_num in range(MAX_SHOW_MORE_CLICKS):
        if not _click_show_more(driver):
            log.info("  No more 'Show More' button after %d click(s)", click_num)
            break

        log.info("  Clicked 'Show More' (#%d)", click_num + 1)
        time.sleep(3)
        human_delay(MIN_DELAY, MAX_DELAY)

    # Scroll back to top, then slowly down — re-triggers lazy load for all products
    driver.execute_script("window.scrollTo(0, 0)")
    time.sleep(1)
    for _ in range(20):
        driver.execute_script("window.scrollBy(0, window.innerHeight)")
        time.sleep(0.8)
        new_h = driver.execute_script("return window.scrollY + window.innerHeight")
        total_h = driver.execute_script("return document.body.scrollHeight")
        if new_h >= total_h:
            break

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
            if product_id is None:
                continue
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
