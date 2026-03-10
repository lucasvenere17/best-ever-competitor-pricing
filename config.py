"""Configuration for the Best Ever Competitor Pricing Tracker."""

import os

# Database
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "pricing.db")

# ---------------------------------------------------------------------------
# Shared scraper settings
# ---------------------------------------------------------------------------
HEADLESS = False
TIMEOUT_MS = 60000  # 60 seconds

# ---------------------------------------------------------------------------
# Shoppers Drug Mart
# ---------------------------------------------------------------------------
SDM_CONFIG = {
    "retailer": "Shoppers Drug Mart",
    "base_url": "https://www.shoppersdrugmart.ca",
    "hair_care_url": "https://www.shoppersdrugmart.ca/shop/categories/HairCare/c/57131",
    "brand_nav_param": "/shop/categories/HairCare",
    "brands": {
        "Monday":       "SDM_MONDAY",
        "Maui":         "SDM_MAUI",
        "Native":       "SDM_NATIVE",
        "Kristin Ess":  "SDM_KRISTIN,ESS",
        "OGX":          "SDM_ORGANIX",
        "Marc Anthony":  "SDM_MARC,ANTHONY",
        "John Frieda":  "SDM_JOHN,FRIEDA",
        "Nexxus":       "SDM_NEXXUS",
        "L'Oreal Ever": "SDM_L%27OREAL",
    },
    "brand_url_keywords": {
        "Monday": ["monday"],
        "Maui": ["maui"],
        "Native": ["native"],
        "Kristin Ess": ["kristin-ess"],
        "OGX": ["ogx", "organix"],
        "Marc Anthony": ["marc-anthony"],
        "John Frieda": ["john-frieda"],
        "Nexxus": ["nexxus"],
        "L'Oreal Ever": ["l-oreal", "loreal"],
    },
    "min_delay": 2,
    "max_delay": 5,
    "brand_delay_min": 5,
    "brand_delay_max": 12,
}

# ---------------------------------------------------------------------------
# Sephora (placeholder — will be filled after site research)
# ---------------------------------------------------------------------------
SEPHORA_CONFIG = {
    "retailer": "Sephora",
    "base_url": "https://www.sephora.com",
    "brand_url_template": "https://www.sephora.com/ca/en/brand/{slug}",
    "brands": {
        "Moroccan Oil": "moroccanoil",
        "K18": "k18-hair",
        "Dae": "dae",
        "Vegamour": "vegamour",
        "Necessaire": "necessaire",
        "Olaplex": "olaplex",
        "Amika": "amika",
        "Living Proof": "living-proof",
        "Ouai": "ouai-haircare",
        "Crown Affair": "crown-affair",
        "Gisou": "gisou",
        "Colour Wow": "color-wow",
        "Pattern": "pattern-beauty-by-tracee-ellis-ross",
    },
    "min_delay": 3,
    "max_delay": 7,
    "brand_delay_min": 8,
    "brand_delay_max": 15,
}

# ---------------------------------------------------------------------------
# Backward compatibility — flat exports used by scraper_sdm.py
# ---------------------------------------------------------------------------
BASE_URL = SDM_CONFIG["base_url"]
BRANDS = SDM_CONFIG["brands"]
HAIR_CARE_URL = SDM_CONFIG["hair_care_url"]
BRAND_NAV_PARAM = SDM_CONFIG["brand_nav_param"]
MIN_DELAY = SDM_CONFIG["min_delay"]
MAX_DELAY = SDM_CONFIG["max_delay"]
BRAND_DELAY_MIN = SDM_CONFIG["brand_delay_min"]
BRAND_DELAY_MAX = SDM_CONFIG["brand_delay_max"]
