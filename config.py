"""Configuration for the Best Ever Competitor Pricing Tracker."""

import os

# Database
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "pricing.db")

# Brands to track on Shoppers Drug Mart
# The site filters by brandName query param (e.g. SDM_MAUI)
BRANDS = {
    "Monday":       "SDM_MONDAY",
    "Maui":         "SDM_MAUI",
    "Native":       "SDM_NATIVE",
    "Kristin Ess":  "SDM_KRISTIN,ESS",
    "OGX":          "SDM_ORGANIX",
    "Marc Anthony":  "SDM_MARC,ANTHONY",
    "John Frieda":  "SDM_JOHN,FRIEDA",
    "Nexxus":       "SDM_NEXXUS",
    "L'Oreal Ever": "SDM_L%27OREAL",
}

# Base URL for Shoppers Drug Mart
BASE_URL = "https://www.shoppersdrugmart.ca"

# Hair care category page — brands are filtered via query params
HAIR_CARE_URL = BASE_URL + "/shop/categories/HairCare/c/57131"

# Build a brand URL: HAIR_CARE_URL + ?nav=...&brandName=SDM_XXX&page=1
BRAND_NAV_PARAM = "/shop/categories/HairCare"

# Scraper settings
HEADLESS = False
TIMEOUT_MS = 60000  # 60 seconds — the site can be slow

# Delay settings (seconds) - between page actions within a brand
MIN_DELAY = 2
MAX_DELAY = 5

# Delay settings (seconds) - between brands (longer to avoid detection)
BRAND_DELAY_MIN = 5
BRAND_DELAY_MAX = 12
