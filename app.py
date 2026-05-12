import streamlit as st
import plotly.express as px

from sheets import load_transactions, add_transaction
from portfolio import compute_portfolio, compute_xirr
from portfolio import search_stocks

st.set_page_config(page_title="Portfolio Tracker", layout="wide")

st.title("📊 My Mutual Fund Tracker")

# Load data
df = load_transactions()

# Compute portfolio
invested, value, pnl, holdings = compute_portfolio(df)
xirr_val = compute_xirr(df)

# ================= TABS =================
tab1, tab2, tab3, tab4 = st.tabs([
    "📈 Dashboard",
    "➕ Add Transaction",
    "📌 Holdings",
    "🧠 Scoring (WIP)"
])

# ================= TAB 1: DASHBOARD =================
with tab1:
    st.subheader("📈 Portfolio Overview")

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Invested", f"₹{invested:,.0f}")
    col2.metric("Current Value", f"₹{value:,.0f}")
    col3.metric("P&L", f"₹{pnl:,.0f}")
    col4.metric("XIRR", f"{xirr_val*100:.2f}%")

    st.divider()

    st.subheader("📊 Allocation")

    fig = px.pie(holdings, values="value", names="stock")
    st.plotly_chart(fig, use_container_width=True)

# ================= TAB 2: ADD TRANSACTION =================
with tab2:
    st.subheader("➕ Add New Transaction")

    with st.form("txn_form"):
        date = st.date_input("Date")
        search_query = st.text_input("Search Stock (type hdfc, reliance etc)")
        stock_options = search_stocks(search_query) if search_query else []
        selected_stock = st.selectbox(
            "Select Stock",
            stock_options,
            format_func=lambda x: x["label"] if x else ""
        )
        stock = selected_stock["symbol"] if selected_stock else ""stock = st.text_input("Stock")
        qty = st.number_input("Quantity", min_value=0.0)
        price = st.number_input("Price", min_value=0.0)
        type_ = st.selectbox("Type", ["BUY", "SELL"])
        charges = st.number_input("Charges", min_value=0.0)

        submit = st.form_submit_button("Add Transaction")

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

# ================= TAB 3: HOLDINGS =================
with tab3:
    st.subheader("📌 Holdings Breakdown")

    st.dataframe(holdings, use_container_width=True)

# ================= TAB 4: SCORING PLACEHOLDER =================
with tab4:
    st.subheader("🧠 Stock Scoring Engine (Coming Next)")

    st.info("This will include:")
    st.write("""
    - Fundamentals (40)
    - Valuation (25)
    - Technical (20)
    - Macro (15)
    
    Final Rule:
    - > 80 → Strong Buy
    - 70–80 → Hold
    - < 70 → Exit
    """)

    st.warning("We will integrate scoring engine in next step.")
