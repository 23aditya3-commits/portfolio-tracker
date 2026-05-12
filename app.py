import streamlit as st
import pandas as pd
import plotly.express as px

from sheets import load_transactions, add_transaction
from portfolio import compute_portfolio, compute_xirr

st.set_page_config(page_title="Portfolio Tracker", layout="wide")

st.title("📊 My Mutual Fund Tracker (MVP)")

# Load data
df = load_transactions()

# Compute portfolio
invested, value, pnl, holdings = compute_portfolio(df)
xirr_val = compute_xirr(df)

# ---------------- DASHBOARD ----------------
st.subheader("📊 Dashboard")

col1, col2, col3, col4 = st.columns(4)

col1.metric("Invested", f"₹{invested:,.0f}")
col2.metric("Current Value", f"₹{value:,.0f}")
col3.metric("P&L", f"₹{pnl:,.0f}")
col4.metric("XIRR", f"{xirr_val*100:.2f}%")

# ---------------- HOLDINGS ----------------
st.subheader("📌 Holdings")

st.dataframe(holdings)

# Pie chart
fig = px.pie(holdings, values="value", names="stock")
st.plotly_chart(fig)

# ---------------- ADD TRANSACTION ----------------
st.subheader("➕ Add Transaction")

with st.form("tx_form"):
    date = st.date_input("Date")
    stock = st.text_input("Stock (e.g. HDFCBANK)")
    qty = st.number_input("Qty", min_value=0)
    price = st.number_input("Price", min_value=0)
    type_ = st.selectbox("Type", ["BUY", "SELL"])
    charges = st.number_input("Charges", min_value=0)

    submit = st.form_submit_button("Add")

    if submit:
        add_transaction({
            "date": str(date),
            "stock": stock,
            "qty": qty,
            "price": price,
            "type": type_,
            "charges": charges
        })
        st.success("Transaction added!")
        st.rerun()
