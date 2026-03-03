"""Streamlit dashboard for the Best Ever Competitor Pricing Tracker."""

import math
import re
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd
import plotly.express as px

import base64
import os

from database import get_brands_summary, get_latest_prices, get_price_history, init_db

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")

_SIZE_RE = re.compile(r"(\d+\.?\d*)\s*(ml|mL|ML|l|L|fl\s*oz|oz|g|kg)\b", re.IGNORECASE)

def _size_to_ml(size_str: str) -> str:
    """Convert a size string to milliliters."""
    if not size_str:
        return ""
    m = _SIZE_RE.search(size_str)
    if not m:
        return size_str
    try:
        value = float(m.group(1))
    except ValueError:
        return size_str
    unit = m.group(2).lower().replace(" ", "")
    if unit in ("ml",):
        return f"{int(value)} ml"
    if unit in ("l",):
        return f"{int(value * 1000)} ml"
    if unit in ("floz", "oz"):
        return f"{int(round(value * 29.5735))} ml"
    return size_str

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Best Ever Competitor Pricing",
    page_icon="\U0001F331",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_db()

# ---------------------------------------------------------------------------
# Data loading (cached)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def load_latest_prices():
    rows = get_latest_prices()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


@st.cache_data(ttl=300)
def load_brands_summary():
    rows = get_brands_summary()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


@st.cache_data(ttl=300)
def load_price_history(product_id=None, days=90):
    rows = get_price_history(product_id=product_id, days=days)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.markdown("### Filters")

df_all = load_latest_prices()

if df_all.empty:
    st.title("Best Ever Competitor Pricing Tracker")
    st.warning("No data yet. Run `python scraper.py` first to populate the database.")
    st.stop()

all_brands = sorted(df_all["brand"].unique())
all_categories = sorted(df_all["category"].dropna().unique())

selected_brands = st.sidebar.multiselect("Brands", all_brands, default=all_brands)
selected_categories = st.sidebar.multiselect("Categories", all_categories, default=all_categories)

# Apply filters
df = df_all[
    df_all["brand"].isin(selected_brands)
    & df_all["category"].isin(selected_categories)
].copy()

if df.empty:
    st.warning("No products match the selected filters.")
    st.stop()

# ---------------------------------------------------------------------------
# Branded header
# ---------------------------------------------------------------------------
logo_path = os.path.join(ASSETS_DIR, "logo_white.png")
if os.path.exists(logo_path):
    with open(logo_path, "rb") as f:
        logo_b64 = base64.b64encode(f.read()).decode()
    st.html(f"""
    <div style="background:#809C85;padding:20px 32px;border-radius:10px;margin-bottom:16px;display:flex;align-items:center;justify-content:space-between;">
        <div style="display:flex;align-items:center;gap:20px;">
            <img src="data:image/png;base64,{logo_b64}" alt="Best Ever" style="height:48px;" />
            <div>
                <div style="color:white;font-size:1.4em;font-weight:700;letter-spacing:0.5px;">Competitor Pricing Tracker</div>
                <div style="color:rgba(255,255,255,0.8);font-size:0.85em;">Hair care prices on Shoppers Drug Mart</div>
            </div>
        </div>
    </div>
    """)
else:
    st.title("Best Ever Competitor Pricing Tracker")
    st.caption("Hair care competitor prices on Shoppers Drug Mart")

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_overview, tab_compare, tab_history, tab_sales = st.tabs(
    ["Overview", "Brand Comparison", "Price History", "Sale Tracker"]
)

# ===== TAB 1 — Overview =====
with tab_overview:
    total_products = len(df)
    num_brands = df["brand"].nunique()
    avg_price = df["price"].mean() if "price" in df.columns and df["price"].notna().any() else 0
    last_scraped = df["date_scraped"].max() if "date_scraped" in df.columns and df["date_scraped"].notna().any() else "N/A"

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Products", total_products)
    c2.metric("Brands Tracked", num_brands)
    c3.metric("Avg Price", f"${avg_price:.2f}")
    c4.metric("Last Scraped", str(last_scraped)[:16] if last_scraped != "N/A" else last_scraped)

    # --- New products notification ---
    new_cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    if "first_seen" in df.columns:
        new_products = df[df["first_seen"] > new_cutoff]
        new_count = len(new_products)
        if new_count > 0:
            new_brands = new_products["brand"].value_counts()
            brand_summary = ", ".join(f"{count} from {brand}" for brand, count in new_brands.items())
            st.success(f"**{new_count} new product(s)** added in the last 7 days: {brand_summary}")

    # --- Search & Sort controls ---
    ctrl1, ctrl2 = st.columns([3, 1])
    with ctrl1:
        search_query = st.text_input("Search products", placeholder="Search by name, brand, category, size...")
    with ctrl2:
        sort_option = st.selectbox("Sort by", ["Brand", "Product Name", "Price Low-High", "Price High-Low"])

    # Apply search filter
    card_df = df.copy()
    if search_query:
        q = search_query.lower()
        mask = False
        for col in ["product_name", "brand", "category", "size"]:
            if col in card_df.columns:
                mask = mask | card_df[col].fillna("").str.lower().str.contains(q, na=False)
        card_df = card_df[mask]

    # Apply sort
    if sort_option == "Price Low-High":
        card_df = card_df.sort_values("price", ascending=True, na_position="last")
    elif sort_option == "Price High-Low":
        card_df = card_df.sort_values("price", ascending=False, na_position="last")
    elif sort_option == "Brand":
        card_df = card_df.sort_values(["brand", "product_name"])
    else:
        card_df = card_df.sort_values("product_name")

    if card_df.empty:
        st.info("No products match your search.")
    else:
        # --- Pagination ---
        per_page = 24
        total_pages = math.ceil(len(card_df) / per_page)

        if "overview_page" not in st.session_state:
            st.session_state.overview_page = 1
        # Clamp to valid range
        if st.session_state.overview_page > total_pages:
            st.session_state.overview_page = total_pages
        if st.session_state.overview_page < 1:
            st.session_state.overview_page = 1
        page = st.session_state.overview_page

        # Top pagination controls
        pg_left, pg_info, pg_right = st.columns([1, 2, 1])
        with pg_left:
            if st.button("< Prev", key="top_prev", disabled=(page <= 1), use_container_width=True):
                st.session_state.overview_page -= 1
                st.rerun()
        with pg_info:
            st.markdown(
                f"<div style='text-align:center;padding:6px 0;color:#1A1A1A;'>Page {page} of {total_pages}</div>",
                unsafe_allow_html=True,
            )
        with pg_right:
            if st.button("Next >", key="top_next", disabled=(page >= total_pages), use_container_width=True):
                st.session_state.overview_page += 1
                st.rerun()

        start_idx = (page - 1) * per_page
        page_df = card_df.iloc[start_idx : start_idx + per_page]

        st.caption(f"Showing {start_idx + 1}--{min(start_idx + per_page, len(card_df))} of {len(card_df)} products")

        # --- Placeholder SVG for missing images ---
        PLACEHOLDER_IMG = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120'%3E%3Crect width='120' height='120' fill='%23e0e0e0'/%3E%3Ctext x='50%25' y='50%25' dominant-baseline='middle' text-anchor='middle' fill='%23999' font-size='14'%3ENo Image%3C/text%3E%3C/svg%3E"

        # --- Render product cards in a 4-column grid ---
        rows_on_page = [page_df.iloc[i : i + 4] for i in range(0, len(page_df), 4)]

        for row_chunk in rows_on_page:
            cols = st.columns(4)
            for idx, (_, product) in enumerate(row_chunk.iterrows()):
                with cols[idx]:
                    img_src = product.get("image_url") or PLACEHOLDER_IMG

                    # Build price HTML
                    price_val = product.get("price")
                    sale_val = product.get("sale_price")
                    if pd.notna(sale_val) and sale_val:
                        reg_display = f"${price_val:.2f}" if pd.notna(price_val) else ""
                        price_html = (
                            f'<span style="text-decoration:line-through;color:#888;font-size:0.9em;">{reg_display}</span> '
                            f'<span style="color:#C6858F;font-weight:bold;font-size:1.1em;">${sale_val:.2f}</span>'
                        )
                    elif pd.notna(price_val):
                        price_html = f'<span style="font-weight:bold;font-size:1.1em;">${price_val:.2f}</span>'
                    else:
                        price_html = '<span style="color:#888;">Price N/A</span>'

                    brand_name = product.get("brand", "")
                    prod_name = product.get("product_name", "")
                    size = _size_to_ml(product.get("size") or "")
                    category = product.get("category") or ""

                    # Check if product is new (first seen within last 7 days)
                    is_new = False
                    first_seen = product.get("first_seen")
                    if first_seen and str(first_seen) > new_cutoff:
                        is_new = True
                    new_badge = '<span style="background:#C6858F;color:white;font-size:0.7em;font-weight:bold;padding:2px 8px;border-radius:4px;position:absolute;top:8px;right:8px;">NEW</span>' if is_new else ""

                    card_html = f"""
                    <div style="border:1px solid #DDD6CD;border-radius:10px;padding:12px;text-align:center;margin-bottom:12px;background:white;min-height:320px;position:relative;">
                        {new_badge}
                        <img src="{img_src}" alt="{prod_name}" style="width:120px;height:120px;object-fit:contain;margin-bottom:8px;border-radius:6px;" />
                        <div style="color:#ACA399;font-size:0.8em;text-transform:uppercase;letter-spacing:0.5px;">{brand_name}</div>
                        <div style="color:#1A1A1A;font-weight:600;font-size:0.95em;margin:4px 0;min-height:40px;line-height:1.3;">{prod_name}</div>
                        <div style="color:#ACA399;font-size:0.8em;">{size}</div>
                        <div style="margin:8px 0;">{price_html}</div>
                        <span style="background:#809C85;color:white;font-size:0.75em;padding:2px 8px;border-radius:12px;">{category}</span>
                    </div>
                    """
                    st.html(card_html)

        # Bottom pagination controls
        bt_left, bt_info, bt_right = st.columns([1, 2, 1])
        with bt_left:
            if st.button("< Prev", key="bot_prev", disabled=(page <= 1), use_container_width=True):
                st.session_state.overview_page -= 1
                st.rerun()
        with bt_info:
            st.markdown(
                f"<div style='text-align:center;padding:6px 0;color:#1A1A1A;'>Page {page} of {total_pages}</div>",
                unsafe_allow_html=True,
            )
        with bt_right:
            if st.button("Next >", key="bot_next", disabled=(page >= total_pages), use_container_width=True):
                st.session_state.overview_page += 1
                st.rerun()

# ===== TAB 2 — Brand Comparison =====
with tab_compare:
    # Brand color palette (consistent across all charts)
    BRAND_COLORS = {
        "Monday": "#809C85",
        "Maui": "#C6858F",
        "Native": "#7A99AC",
        "Kristin Ess": "#9991A4",
        "OGX": "#ACA399",
        "Marc Anthony": "#5B8A72",
        "John Frieda": "#B07D87",
        "Nexxus": "#6A87A0",
        "L'Oreal Ever": "#8A8298",
    }

    # In-tab filter controls
    filt1, filt2 = st.columns(2)
    with filt1:
        compare_brands = st.multiselect(
            "Select brands to compare",
            all_brands,
            default=all_brands,
            key="compare_brands",
        )
    with filt2:
        compare_categories = st.multiselect(
            "Select categories",
            all_categories,
            default=all_categories,
            key="compare_categories",
        )

    # Filter data for this tab
    cdf = df_all[
        df_all["brand"].isin(compare_brands)
        & df_all["category"].isin(compare_categories)
    ].copy()

    if cdf.empty:
        st.info("No products match the selected filters.")
    else:
        # --- Chart 1: Overall average price by brand ---
        st.subheader("Average Price by Brand")
        avg_by_brand = (
            cdf.groupby("brand")["price"]
            .mean()
            .reset_index()
            .sort_values("price")
        )
        avg_by_brand["color"] = avg_by_brand["brand"].map(BRAND_COLORS).fillna("#ACA399")
        fig_bar = px.bar(
            avg_by_brand,
            x="price",
            y="brand",
            orientation="h",
            labels={"price": "Average Price ($)", "brand": ""},
            color="brand",
            color_discrete_map=BRAND_COLORS,
        )
        fig_bar.update_layout(showlegend=False, yaxis=dict(categoryorder="total ascending"))
        fig_bar.update_traces(texttemplate="$%{x:.2f}", textposition="outside")
        st.plotly_chart(fig_bar, use_container_width=True)

        # --- Chart 2: Average price by category per brand ---
        st.subheader("Average Price by Category")
        brand_cat = (
            cdf.groupby(["brand", "category"])["price"]
            .mean()
            .round(2)
            .reset_index()
        )
        # Order categories logically
        cat_order = ["shampoo", "conditioner", "mask", "treatment", "styling", "other"]
        cat_order = [c for c in cat_order if c in brand_cat["category"].unique()]
        brand_cat["category"] = pd.Categorical(brand_cat["category"], categories=cat_order, ordered=True)
        brand_cat = brand_cat.sort_values("category")

        fig_group = px.bar(
            brand_cat,
            x="category",
            y="price",
            color="brand",
            barmode="group",
            labels={"price": "Avg Price ($)", "category": "", "brand": "Brand"},
            color_discrete_map=BRAND_COLORS,
        )
        fig_group.update_layout(
            xaxis=dict(categoryorder="array", categoryarray=cat_order),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig_group, use_container_width=True)

        # --- Chart 3: Product count by brand & category (stacked) ---
        st.subheader("Product Count by Brand")
        count_data = (
            cdf.groupby(["brand", "category"])
            .size()
            .reset_index(name="count")
        )
        count_data["category"] = pd.Categorical(count_data["category"], categories=cat_order, ordered=True)
        fig_count = px.bar(
            count_data,
            x="brand",
            y="count",
            color="category",
            barmode="stack",
            labels={"count": "Products", "brand": "", "category": "Category"},
        )
        fig_count.update_layout(
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig_count, use_container_width=True)

        # --- Chart 4: Price distribution box plot ---
        st.subheader("Price Distribution by Brand")
        fig_box = px.box(
            cdf,
            x="brand",
            y="price",
            color="brand",
            labels={"brand": "", "price": "Price ($)"},
            color_discrete_map=BRAND_COLORS,
        )
        fig_box.update_layout(showlegend=False, yaxis=dict(range=[0, 100]))
        st.plotly_chart(fig_box, use_container_width=True)

# ===== TAB 3 — Price History =====
with tab_history:
    st.subheader("Price Over Time")

    products_list = df[["id", "brand", "product_name"]].drop_duplicates()
    products_list["label"] = products_list["brand"] + " — " + products_list["product_name"]

    selected_label = st.selectbox("Select product", products_list["label"].tolist())
    selected_row = products_list[products_list["label"] == selected_label].iloc[0]

    days = st.slider("Days of history", 7, 365, 90)

    hist = load_price_history(product_id=int(selected_row["id"]), days=days)

    if hist.empty:
        st.info("No price history for this product yet.")
    else:
        fig_line = px.line(
            hist,
            x="date_scraped",
            y="price",
            labels={"date_scraped": "Date", "price": "Price ($)"},
            markers=True,
        )
        if "sale_price" in hist.columns and hist["sale_price"].notna().any():
            fig_line.add_scatter(
                x=hist["date_scraped"],
                y=hist["sale_price"],
                mode="markers",
                name="Sale Price",
                marker=dict(color="red", size=8),
            )
        st.plotly_chart(fig_line, use_container_width=True)

# ===== TAB 4 — Sale Tracker =====
with tab_sales:
    st.subheader("Products Currently on Sale")

    sales = df[df["sale_price"].notna()].copy()

    if sales.empty:
        st.info("No products currently on sale.")
    else:
        sales["discount_pct"] = (
            (sales["regular_price"] - sales["sale_price"]) / sales["regular_price"] * 100
        ).round(1)

        sales_sorted = sales.sort_values("discount_pct", ascending=False)

        sale_cols = ["brand", "product_name", "category", "regular_price", "sale_price", "discount_pct"]
        sale_cols = [c for c in sale_cols if c in sales_sorted.columns]

        st.dataframe(
            sales_sorted[sale_cols],
            use_container_width=True,
            column_config={
                "regular_price": st.column_config.NumberColumn("Regular", format="$%.2f"),
                "sale_price": st.column_config.NumberColumn("Sale", format="$%.2f"),
                "discount_pct": st.column_config.NumberColumn("Discount %", format="%.1f%%"),
            },
            hide_index=True,
        )

        # Discount by brand bar chart
        st.subheader("Average Discount by Brand")
        disc_by_brand = sales.groupby("brand")["discount_pct"].mean().sort_values(ascending=False).reset_index()
        fig_disc = px.bar(
            disc_by_brand,
            x="brand",
            y="discount_pct",
            labels={"brand": "Brand", "discount_pct": "Avg Discount %"},
            color="discount_pct",
            color_continuous_scale="RdYlGn",
        )
        fig_disc.update_layout(showlegend=False)
        st.plotly_chart(fig_disc, use_container_width=True)
