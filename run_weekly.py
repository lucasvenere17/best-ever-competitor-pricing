"""Run the scraper if it hasn't been run this week yet.

Designed to be triggered at login/startup via Task Scheduler.
Checks a marker file to avoid duplicate runs within the same week.
"""

import os
import sys
from datetime import datetime

MARKER_FILE = os.path.join(os.path.dirname(__file__), "data", ".last_scrape_week")


def already_ran_this_week() -> bool:
    """Check if the scraper has already run this calendar week."""
    if not os.path.exists(MARKER_FILE):
        return False
    try:
        with open(MARKER_FILE, "r") as f:
            last_week = f.read().strip()
        current_week = datetime.now().strftime("%Y-W%W")
        return last_week == current_week
    except Exception:
        return False


def mark_completed():
    """Write the current week to the marker file."""
    os.makedirs(os.path.dirname(MARKER_FILE), exist_ok=True)
    with open(MARKER_FILE, "w") as f:
        f.write(datetime.now().strftime("%Y-W%W"))


def main():
    if already_ran_this_week():
        print("Scraper already ran this week. Skipping.")
        return

    print(f"Starting weekly scrape at {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # Import and run the scraper
    from scraper import run
    run()

    mark_completed()
    print("Weekly scrape complete. Marker updated.")


if __name__ == "__main__":
    main()
