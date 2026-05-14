import streamlit as st
import pandas as pd
import plotly.express as px

from sheets import (
    load_transactions,
    add_transaction,
    delete_transaction,
    update_transaction,
    load_cashflow,
    add_cashflow_entry
)

from portfolio import (
    compute_portfolio,
    compute_xirr,
    search_stocks,
    calculate_free_cash
)

st.set_page_config(page_title="Portfolio Tracker", layout="wide")

st.title("📊 My Mutual Fund Tracker")

# ---------------- LOAD DATA ----------------
df = load_transactions()

# Guard clause (VERY IMPORTANT)
if df.empty:
    st.warning("No transactions found. Showing empty dashboard.")

    df = pd.DataFrame(columns=[
        "date", "stock", "qty", "price", "type", "charges", "row_index"
    ])

# Ensure row_index exists
if "row_index" not in df.columns:
    df["row_index"] = range(2, len(df) + 2)

# ---------------- CALCULATIONS ----------------
invested, value, pnl, holdings = compute_portfolio(df)
xirr_val = compute_xirr(df)

cash_df = load_cashflow()

free_cash = calculate_free_cash(cash_df)

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

    col1, col2, col3, col4, col5 = st.columns(5)

    col1.metric("Invested", f"₹{invested:,.0f}")
    col2.metric("Current Value", f"₹{value:,.0f}")
    col3.metric("P&L", f"₹{pnl:,.0f}")
    col4.metric("XIRR", f"{xirr_val*100:.2f}%")
    col5.metric("Free Cash", f"₹{free_cash:,.0f}")

    st.divider()

    st.subheader("📊 Allocation")

    if not holdings.empty:
        fig = px.pie(holdings, values="value", names="stock")
        st.plotly_chart(fig, use_container_width=True)

# ================= TAB 2: ADD / EDIT / DELETE =================
with tab2:

    st.subheader("➕ Add / Edit / Delete Transactions")

    st.dataframe(df, use_container_width=True)

    st.divider()

    # ---------------- ADD ----------------
    st.subheader("➕ Add Transaction")

    search_query = st.text_input("Search Stock (e.g. hdfc, reliance)")

    stock_options = search_stocks(search_query) if search_query else []

    selected_stock = st.selectbox(
        "Select Stock",
        stock_options,
        format_func=lambda x: x["label"] if x else ""
    )

    stock = selected_stock["symbol"] if selected_stock else ""

    with st.form("add_form"):

        date = st.date_input("Date")
        qty = st.number_input("Qty", min_value=0.0)
        price = st.number_input("Price", min_value=0.0)
        type_ = st.selectbox("Type", ["BUY", "SELL"])
        charges = st.number_input("Charges", min_value=0.0)

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

            # ---------------- CASHFLOW ENTRY ----------------

            total_amount = qty * price

            if type_ == "BUY":

                add_cashflow_entry({
                    "date": str(date),
                    "type": "BUY",
                    "amount": -(total_amount + charges),
                    "note": stock
                })

            elif type_ == "SELL":

                add_cashflow_entry({
                    "date": str(date),
                    "type": "SELL",
                    "amount": total_amount - charges,
                    "note": stock
                })

            st.success("Transaction Added!")
            st.rerun()

    st.divider()

    # ---------------- DELETE ----------------
    st.subheader("🗑️ Delete Transaction")

    del_row = st.selectbox(
        "Select row to delete",
        df["row_index"],
        format_func=lambda x: f"Row {x}"
    )

    if st.button("Delete Transaction"):
        delete_transaction(del_row)
        st.success("Deleted!")
        st.rerun()

    st.divider()

    # ---------------- EDIT (FIXED SAFE VERSION) ----------------
    st.subheader("✏️ Edit Transaction")

    edit_row = st.selectbox(
        "Select row to edit",
        df["row_index"],
        key="edit_row"
    )

    filtered = df[df["row_index"] == edit_row]

    if filtered.empty:
        st.warning("Selected row not found (it may have been deleted).")
    else:
        edit_data = filtered.iloc[0]

        with st.form("edit_form"):

            date = st.date_input("Date", value=pd.to_datetime(edit_data["date"]))
            stock_edit = st.text_input("Stock", value=edit_data["stock"])
            qty = st.number_input("Qty", value=float(edit_data["qty"]))
            price = st.number_input("Price", value=float(edit_data["price"]))
            type_ = st.selectbox("Type", ["BUY", "SELL"])
            charges = st.number_input("Charges", value=float(edit_data["charges"]))

            update = st.form_submit_button("Update")

            if update:
                update_transaction(edit_row, {
                    "date": str(date),
                    "stock": stock_edit,
                    "qty": qty,
                    "price": price,
                    "type": type_,
                    "charges": charges
                })
                st.success("Updated!")
                st.rerun()

# ================= TAB 3: HOLDINGS =================
with tab3:
    st.subheader("📌 Holdings Breakdown")

    st.dataframe(holdings, use_container_width=True)

# ================= TAB 4: SCORING PLACEHOLDER =================
with tab4:
    st.subheader("🧠 Stock Scoring Engine (Coming Next)")

    st.info("""
    Scoring system:
    - Fundamentals (40)
    - Valuation (25)
    - Technical (20)
    - Macro (15)

    Rules:
    - > 80 → Strong Buy
    - 70–80 → Hold
    - < 70 → Exit
    """)

    st.warning("Next step: build scoring + auto rebalance engine")
