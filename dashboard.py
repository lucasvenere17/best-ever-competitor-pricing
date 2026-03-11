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

# Load retailer logo SVGs as base64 data URIs
_RETAILER_LOGOS = {}
for _logo_file, _retailer_key in [("sephora_logo.svg", "Sephora"), ("sdm_logo.svg", "Shoppers Drug Mart"), ("walmart_logo.svg", "Walmart")]:
    _logo_path = os.path.join(ASSETS_DIR, _logo_file)
    if os.path.exists(_logo_path):
        with open(_logo_path, "rb") as _f:
            _RETAILER_LOGOS[_retailer_key] = "data:image/svg+xml;base64," + base64.b64encode(_f.read()).decode()

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

# Retailer filter
all_retailers = sorted(df_all["retailer"].dropna().unique()) if "retailer" in df_all.columns else ["Shoppers Drug Mart"]
if len(all_retailers) > 1:
    selected_retailers = st.sidebar.multiselect("Retailers", all_retailers, default=all_retailers)
else:
    selected_retailers = all_retailers

# Brand filter — only show brands available at selected retailers
brands_at_selected = sorted(
    df_all[df_all["retailer"].isin(selected_retailers)]["brand"].unique()
) if selected_retailers else []
all_brands = brands_at_selected
selected_brands = st.sidebar.multiselect("Brands", all_brands, default=all_brands)

all_categories = sorted(df_all["category"].dropna().unique())
selected_categories = st.sidebar.multiselect("Categories", all_categories, default=all_categories)

# Apply filters — include products from selected brands at ANY retailer
# (so we can show cross-retailer comparison even when filtering)
brand_cat_mask = (
    df_all["brand"].isin(selected_brands)
    & df_all["category"].isin(selected_categories)
)
df = df_all[brand_cat_mask].copy()

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
                <div style="color:rgba(255,255,255,0.8);font-size:0.85em;">Hair care prices across retailers</div>
            </div>
        </div>
    </div>
    """)
else:
    st.title("Best Ever Competitor Pricing Tracker")
    st.caption("Hair care competitor prices across retailers")

# ---------------------------------------------------------------------------
# Helper: build price row HTML for a product card
# ---------------------------------------------------------------------------
PLACEHOLDER_IMG = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120'%3E%3Crect width='120' height='120' fill='%23e0e0e0'/%3E%3Ctext x='50%25' y='50%25' dominant-baseline='middle' text-anchor='middle' fill='%23999' font-size='14'%3ENo Image%3C/text%3E%3C/svg%3E"

def _price_html(price_val, sale_val, is_lowest=False):
    """Build price HTML for a single retailer row."""
    color = "#2E7D32" if is_lowest else "#1A1A1A"
    weight = "bold" if is_lowest else "600"
    if pd.notna(sale_val) and sale_val:
        reg_display = f"${price_val:.2f}" if pd.notna(price_val) else ""
        return (
            f'<span style="text-decoration:line-through;color:#888;font-size:0.85em;">{reg_display}</span> '
            f'<span style="color:#C6858F;font-weight:bold;font-size:0.95em;">${sale_val:.2f}</span>'
        )
    elif pd.notna(price_val):
        return f'<span style="color:{color};font-weight:{weight};font-size:0.95em;">${price_val:.2f}</span>'
    else:
        return '<span style="color:#CCC;font-size:0.85em;">N/A</span>'


def _build_card_html(group_rows, new_cutoff, selected_retailers):
    """Build a product card with price rows per retailer.

    group_rows: DataFrame of rows for one product_group (or a single product).
    """
    # Use the first row for shared product info; prefer selected retailers for image
    primary = group_rows.iloc[0]
    for _, r in group_rows.iterrows():
        if r["retailer"] in selected_retailers and pd.notna(r.get("image_url")):
            primary = r
            break

    img_src = primary.get("image_url") or PLACEHOLDER_IMG
    brand_name = primary.get("brand", "")
    prod_name = primary.get("product_name", "")
    size = _size_to_ml(primary.get("size") or "")
    category = primary.get("category") or ""
    primary_url = primary.get("url") or ""

    # New badge
    is_new = False
    first_seen = primary.get("first_seen")
    if first_seen and str(first_seen) > new_cutoff:
        is_new = True
    new_badge = '<span style="background:#E53935;color:white;font-size:0.7em;font-weight:bold;padding:2px 8px;border-radius:4px;position:absolute;top:8px;right:8px;">NEW</span>' if is_new else ""

    # Build price rows per retailer
    # Find lowest price to highlight
    prices = {}
    for _, r in group_rows.iterrows():
        ret = r["retailer"]
        effective_price = r.get("sale_price") if pd.notna(r.get("sale_price")) else r.get("price")
        if pd.notna(effective_price):
            prices[ret] = effective_price

    lowest_price = min(prices.values()) if prices else None
    lowest_retailers = {ret for ret, p in prices.items() if p == lowest_price} if lowest_price else set()

    price_rows_html = ""
    for _, r in group_rows.iterrows():
        ret = r["retailer"]
        row_url = r.get("url") or ""
        logo_src = _RETAILER_LOGOS.get(ret, "")
        is_lowest = ret in lowest_retailers and len(prices) > 1
        p_html = _price_html(r.get("price"), r.get("sale_price"), is_lowest=is_lowest)

        # Dim retailers not in the selected filter
        opacity = "1" if ret in selected_retailers else "0.4"

        logo_img = f'<img src="{logo_src}" alt="{ret}" style="height:16px;border-radius:2px;" />' if logo_src else f'<span style="font-size:0.7em;color:#888;">{ret}</span>'

        # Wrap each retailer row in a link to that retailer's product page
        if row_url:
            price_rows_html += f'''
            <a href="{row_url}" target="_blank" rel="noopener" style="text-decoration:none;display:flex;align-items:center;justify-content:space-between;padding:3px 0;opacity:{opacity};border-radius:4px;" onmouseover="this.style.background='#f5f5f5'" onmouseout="this.style.background='transparent'">
                {logo_img}
                {p_html}
            </a>
            '''
        else:
            price_rows_html += f'''
            <div style="display:flex;align-items:center;justify-content:space-between;padding:3px 0;opacity:{opacity};">
                {logo_img}
                {p_html}
            </div>
            '''

    # Wrap image and product name in a link to the primary product page
    if primary_url:
        img_html = f'<a href="{primary_url}" target="_blank" rel="noopener"><img src="{img_src}" alt="{prod_name}" style="width:120px;height:120px;object-fit:contain;margin-bottom:8px;border-radius:6px;" /></a>'
        name_html = f'<a href="{primary_url}" target="_blank" rel="noopener" style="color:#1A1A1A;text-decoration:none;" onmouseover="this.style.textDecoration=\'underline\'" onmouseout="this.style.textDecoration=\'none\'"><div style="font-weight:600;font-size:0.95em;margin:4px 0;min-height:40px;line-height:1.3;">{prod_name}</div></a>'
    else:
        img_html = f'<img src="{img_src}" alt="{prod_name}" style="width:120px;height:120px;object-fit:contain;margin-bottom:8px;border-radius:6px;" />'
        name_html = f'<div style="color:#1A1A1A;font-weight:600;font-size:0.95em;margin:4px 0;min-height:40px;line-height:1.3;">{prod_name}</div>'

    return f"""
    <div style="border:1px solid #DDD6CD;border-radius:10px;padding:12px;text-align:center;margin-bottom:12px;background:white;min-height:340px;position:relative;">
        {new_badge}
        {img_html}
        <div style="color:#ACA399;font-size:0.8em;text-transform:uppercase;letter-spacing:0.5px;">{brand_name}</div>
        {name_html}
        <div style="color:#ACA399;font-size:0.8em;">{size}</div>
        <div style="margin:8px 4px 6px 4px;text-align:left;">{price_rows_html}</div>
        <span style="background:#809C85;color:white;font-size:0.75em;padding:2px 8px;border-radius:12px;">{category}</span>
    </div>
    """


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_overview, tab_compare, tab_history, tab_sales = st.tabs(
    ["Overview", "Brand Comparison", "Price History", "Sale Tracker"]
)

# ===== TAB 1 — Overview =====
with tab_overview:
    # Metrics — based on selected retailers
    df_selected = df[df["retailer"].isin(selected_retailers)]
    total_products = df_selected["product_name"].nunique() if not df_selected.empty else 0
    num_brands = df_selected["brand"].nunique() if not df_selected.empty else 0
    num_retailers = df_selected["retailer"].nunique() if not df_selected.empty else 0
    avg_price = df_selected["price"].mean() if not df_selected.empty and df_selected["price"].notna().any() else 0
    last_scraped = df_selected["date_scraped"].max() if not df_selected.empty and "date_scraped" in df_selected.columns and df_selected["date_scraped"].notna().any() else "N/A"

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Products", total_products)
    c2.metric("Brands Tracked", num_brands)
    c3.metric("Retailers", num_retailers)
    c4.metric("Last Scraped", str(last_scraped)[:16] if last_scraped != "N/A" else last_scraped)

    # --- New products notification ---
    new_cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    if "first_seen" in df_selected.columns:
        new_products = df_selected[df_selected["first_seen"] > new_cutoff]
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

    # Work with the full df (all retailers for these brands) for cross-retailer cards
    card_df = df.copy()
    if search_query:
        q = search_query.lower()
        mask = False
        for col in ["product_name", "brand", "category", "size"]:
            if col in card_df.columns:
                mask = mask | card_df[col].fillna("").str.lower().str.contains(q, na=False)
        card_df = card_df[mask]

    if card_df.empty:
        st.info("No products match your search.")
    else:
        # Group by product_group where available, otherwise by id
        has_groups = "product_group" in card_df.columns and card_df["product_group"].notna().any()

        if has_groups:
            # For sorting, compute a representative price per group (from selected retailers)
            group_key = "product_group"
            selected_mask = card_df["retailer"].isin(selected_retailers)
            group_price = card_df[selected_mask].groupby(group_key)["price"].min().rename("sort_price")
            group_brand = card_df.groupby(group_key).first()[["brand", "product_name"]]
            group_info = group_brand.join(group_price)

            if sort_option == "Price Low-High":
                group_order = group_info.sort_values("sort_price", ascending=True, na_position="last").index
            elif sort_option == "Price High-Low":
                group_order = group_info.sort_values("sort_price", ascending=False, na_position="last").index
            elif sort_option == "Brand":
                group_order = group_info.sort_values(["brand", "product_name"]).index
            else:
                group_order = group_info.sort_values("product_name").index

            # Only show groups that have at least one product at a selected retailer
            groups_with_selected = card_df[card_df["retailer"].isin(selected_retailers)][group_key].unique()
            group_order = [g for g in group_order if g in groups_with_selected]
        else:
            group_key = "id"
            if sort_option == "Price Low-High":
                card_df = card_df.sort_values("price", ascending=True, na_position="last")
            elif sort_option == "Price High-Low":
                card_df = card_df.sort_values("price", ascending=False, na_position="last")
            elif sort_option == "Brand":
                card_df = card_df.sort_values(["brand", "product_name"])
            else:
                card_df = card_df.sort_values("product_name")
            group_order = card_df[group_key].unique()

        # --- Pagination ---
        per_page = 24
        total_groups = len(group_order)
        total_pages = max(1, math.ceil(total_groups / per_page))

        if "overview_page" not in st.session_state:
            st.session_state.overview_page = 1
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
        page_groups = group_order[start_idx : start_idx + per_page]

        st.caption(f"Showing {start_idx + 1}--{min(start_idx + per_page, total_groups)} of {total_groups} products")

        # --- Render product cards in a 4-column grid ---
        rows_on_page = [page_groups[i : i + 4] for i in range(0, len(page_groups), 4)]

        for row_chunk in rows_on_page:
            cols = st.columns(4)
            for idx, grp in enumerate(row_chunk):
                with cols[idx]:
                    group_rows = card_df[card_df[group_key] == grp]
                    html = _build_card_html(group_rows, new_cutoff, selected_retailers)
                    st.html(html)

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

    # Filter data for this tab (only from selected retailers)
    cdf = df_all[
        df_all["brand"].isin(compare_brands)
        & df_all["category"].isin(compare_categories)
        & df_all["retailer"].isin(selected_retailers)
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

        # --- Chart 2: Retailer Price Gap (only if multiple retailers selected) ---
        if len(selected_retailers) > 1 and "retailer" in cdf.columns:
            st.subheader("Average Price by Brand & Retailer")
            brand_retailer = (
                cdf.groupby(["brand", "retailer"])["price"]
                .mean()
                .round(2)
                .reset_index()
            )
            RETAILER_COLORS = {
                "Shoppers Drug Mart": "#D71920",
                "Sephora": "#000000",
                "Walmart": "#0071DC",
            }
            fig_gap = px.bar(
                brand_retailer,
                x="brand",
                y="price",
                color="retailer",
                barmode="group",
                labels={"price": "Avg Price ($)", "brand": "", "retailer": "Retailer"},
                color_discrete_map=RETAILER_COLORS,
            )
            fig_gap.update_layout(
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            st.plotly_chart(fig_gap, use_container_width=True)

        # --- Chart 3: Average price by category per brand ---
        st.subheader("Average Price by Category")
        brand_cat = (
            cdf.groupby(["brand", "category"])["price"]
            .mean()
            .round(2)
            .reset_index()
        )
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

        # --- Chart 4: Product count by brand & category (stacked) ---
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

        # --- Chart 5: Price distribution box plot ---
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

    products_list = df[df["retailer"].isin(selected_retailers)][["id", "brand", "product_name", "retailer"]].drop_duplicates()
    products_list["label"] = products_list["brand"] + " — " + products_list["product_name"] + " (" + products_list["retailer"] + ")"

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

    sales = df[df["retailer"].isin(selected_retailers) & df["sale_price"].notna()].copy()

    if sales.empty:
        st.info("No products currently on sale.")
    else:
        sales["discount_pct"] = (
            (sales["regular_price"] - sales["sale_price"]) / sales["regular_price"] * 100
        ).round(1)

        sales_sorted = sales.sort_values("discount_pct", ascending=False)

        sale_cols = ["brand", "product_name", "retailer", "category", "regular_price", "sale_price", "discount_pct"]
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
