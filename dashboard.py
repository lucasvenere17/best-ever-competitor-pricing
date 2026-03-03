"""Streamlit dashboard for the Best Ever Competitor Pricing Tracker."""

import streamlit as st
import pandas as pd
import plotly.express as px

from database import get_brands_summary, get_latest_prices, get_price_history, init_db

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

    st.subheader("Price Table")

    display_cols = ["brand", "product_name", "category", "size", "price", "regular_price", "sale_price"]
    display_cols = [c for c in display_cols if c in df.columns]

    st.dataframe(
        df[display_cols].sort_values(["brand", "product_name"]),
        use_container_width=True,
        column_config={
            "price": st.column_config.NumberColumn("Price", format="$%.2f"),
            "regular_price": st.column_config.NumberColumn("Regular", format="$%.2f"),
            "sale_price": st.column_config.NumberColumn("Sale", format="$%.2f"),
        },
        hide_index=True,
    )

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
        fig_box.update_layout(showlegend=False)
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
