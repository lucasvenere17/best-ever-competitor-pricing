"""Run scrapers if they haven't been run this week yet.

Designed to be triggered at login/startup via Task Scheduler.
Checks a marker file per retailer to avoid duplicate runs within the same week.
"""

import os
import sys
from datetime import datetime

MARKER_DIR = os.path.join(os.path.dirname(__file__), "data")

SCRAPERS = [
    {"name": "Shoppers Drug Mart", "marker": ".last_scrape_week_sdm", "module": "scraper_sdm"},
    {"name": "Sephora", "marker": ".last_scrape_week_sephora", "module": "scraper_sephora"},
    {"name": "Walmart", "marker": ".last_scrape_week_walmart", "module": "scraper_walmart"},
]


def marker_path(marker_file: str) -> str:
    return os.path.join(MARKER_DIR, marker_file)


def already_ran_this_week(marker_file: str) -> bool:
    """Check if the scraper has already run this calendar week."""
    path = marker_path(marker_file)
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r") as f:
            last_week = f.read().strip()
        current_week = datetime.now().strftime("%Y-W%W")
        return last_week == current_week
    except Exception:
        return False


def mark_completed(marker_file: str):
    """Write the current week to the marker file."""
    os.makedirs(MARKER_DIR, exist_ok=True)
    path = marker_path(marker_file)
    with open(path, "w") as f:
        f.write(datetime.now().strftime("%Y-W%W"))


def main():
    for scraper in SCRAPERS:
        name = scraper["name"]
        marker = scraper["marker"]
        module_name = scraper["module"]

        if already_ran_this_week(marker):
            print(f"{name}: already ran this week. Skipping.")
            continue

        print(f"Starting {name} scrape at {datetime.now().strftime('%Y-%m-%d %H:%M')}")

        try:
            mod = __import__(module_name)
            mod.run()
            mark_completed(marker)
            print(f"{name}: weekly scrape complete. Marker updated.")
        except ImportError:
            print(f"{name}: scraper module '{module_name}' not found yet — skipping.")
        except Exception as exc:
            print(f"{name}: error during scrape — {exc}")

    print("All scrapers done.")


if __name__ == "__main__":
    main()
