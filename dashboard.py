"""Streamlit dashboard for the Best Ever Competitor Pricing Tracker."""

import math
import re

import streamlit as st
import pandas as pd
import plotly.express as px

from database import get_brands_summary, get_latest_prices, get_price_history, init_db

_SIZE_RE = re.compile(r"([\d.]+)\s*(ml|mL|ML|l|L|fl\s*oz|oz|g|kg)\b", re.IGNORECASE)

def _size_to_ml(size_str: str) -> str:
    """Convert a size string to milliliters."""
    if not size_str:
        return ""
    m = _SIZE_RE.search(size_str)
    if not m:
        return size_str
    value = float(m.group(1))
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
    page_icon="\U0001F4B2",
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
st.sidebar.title("Filters")

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
# Header
# ---------------------------------------------------------------------------
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
        page = st.number_input("Page", min_value=1, max_value=max(total_pages, 1), value=1, step=1)

        start_idx = (page - 1) * per_page
        page_df = card_df.iloc[start_idx : start_idx + per_page]

        st.caption(f"Showing {start_idx + 1}–{min(start_idx + per_page, len(card_df))} of {len(card_df)} products")

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
                            f'<span style="color:#d32f2f;font-weight:bold;font-size:1.1em;">${sale_val:.2f}</span>'
                        )
                    elif pd.notna(price_val):
                        price_html = f'<span style="font-weight:bold;font-size:1.1em;">${price_val:.2f}</span>'
                    else:
                        price_html = '<span style="color:#888;">Price N/A</span>'

                    brand_name = product.get("brand", "")
                    prod_name = product.get("product_name", "")
                    size = _size_to_ml(product.get("size") or "")
                    category = product.get("category") or ""

                    card_html = f"""
                    <div style="border:1px solid #e0e0e0;border-radius:10px;padding:12px;text-align:center;margin-bottom:12px;background:#fafafa;min-height:320px;">
                        <img src="{img_src}" alt="{prod_name}" style="width:120px;height:120px;object-fit:contain;margin-bottom:8px;border-radius:6px;" />
                        <div style="color:#666;font-size:0.8em;text-transform:uppercase;letter-spacing:0.5px;">{brand_name}</div>
                        <div style="font-weight:600;font-size:0.95em;margin:4px 0;min-height:40px;line-height:1.3;">{prod_name}</div>
                        <div style="color:#888;font-size:0.8em;">{size}</div>
                        <div style="margin:8px 0;">{price_html}</div>
                        <span style="background:#e3f2fd;color:#1565c0;font-size:0.75em;padding:2px 8px;border-radius:12px;">{category}</span>
                    </div>
                    """
                    st.html(card_html)

        # Page indicator at bottom
        st.caption(f"Page {page} of {total_pages}")

# ===== TAB 2 — Brand Comparison =====
with tab_compare:
    summary = load_brands_summary()
    if summary.empty:
        st.info("No brand data available.")
    else:
        summary_filtered = summary[summary["brand"].isin(selected_brands)]

        # Average price bar chart
        st.subheader("Average Price by Brand")
        fig_bar = px.bar(
            summary_filtered.sort_values("avg_price"),
            x="avg_price",
            y="brand",
            orientation="h",
            labels={"avg_price": "Average Price ($)", "brand": "Brand"},
            color="avg_price",
            color_continuous_scale="RdYlGn_r",
        )
        fig_bar.update_layout(showlegend=False)
        st.plotly_chart(fig_bar, use_container_width=True)

        # Price by brand + category grouped bars
        if "category" in df.columns:
            st.subheader("Average Price by Brand & Category")
            brand_cat = (
                df.groupby(["brand", "category"])["price"]
                .mean()
                .reset_index()
            )
            fig_group = px.bar(
                brand_cat,
                x="brand",
                y="price",
                color="category",
                barmode="group",
                labels={"price": "Avg Price ($)", "brand": "Brand"},
            )
            st.plotly_chart(fig_group, use_container_width=True)

        # Box plot
        st.subheader("Price Distribution by Brand")
        fig_box = px.box(
            df,
            x="brand",
            y="price",
            color="brand",
            labels={"brand": "Brand", "price": "Price ($)"},
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
