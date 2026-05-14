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
    calculate_free_cash,
    check_free_cash_before_buy
)

st.set_page_config(page_title="Portfolio Tracker", layout="wide")

st.title("📊 My Mutual Fund Tracker")

# ================= LOAD DATA =================
df = load_transactions()

# SAFE EMPTY HANDLING
if df is None:
    df = pd.DataFrame()

if df.empty:
    st.warning("No transactions found. Showing empty dashboard.")

    df = pd.DataFrame(columns=[
        "date", "stock", "qty", "price", "type", "charges", "row_index"
    ])

# SAFE TYPE CONVERSION
for col in ["qty", "price", "charges"]:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

if "date" in df.columns:
    df["date"] = pd.to_datetime(df["date"], errors="coerce")

# ================= CALCULATIONS =================
invested, value, pnl, holdings = compute_portfolio(df)
xirr_val = compute_xirr(df)
free_cash = calculate_free_cash(df)

# ================= TABS (UPDATED) =================
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📈 Dashboard",
    "➕ Add Transaction",
    "📌 Holdings",
    "🧠 Scoring (WIP)",
    "💰 Funds"
])

# ================= TAB 1 =================
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

    if holdings is not None and not holdings.empty:
        fig = px.pie(holdings, values="value", names="stock")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No holdings yet")

# ================= TAB 2 =================
with tab2:

    st.subheader("➕ Add Transaction")

    search_query = st.text_input("Search Stock (e.g. hdfc, reliance)")
    stock_options = search_stocks(search_query) if search_query else []

    if not stock_options:
        stock_options = [{"label": "No results", "symbol": ""}]

    selected_stock = st.selectbox(
        "Select Stock",
        stock_options,
        format_func=lambda x: x["label"]
    )

    stock = selected_stock["symbol"]

    with st.form("add_form"):

        date = st.date_input("Date")
        qty = st.number_input("Qty", min_value=0.0)
        price = st.number_input("Price", min_value=0.0)
        type_ = st.selectbox("Type", ["BUY", "SELL"])
        charges = st.number_input("Charges", min_value=0.0)

        submit = st.form_submit_button("Add")

        if submit:

            qty = float(qty)
            price = float(price)

            if type_ == "BUY":

                can_buy = check_free_cash_before_buy(
                    df,
                    date,
                    qty,
                    price
                )

                if not can_buy:
                    st.error("❌ Insufficient Free Cash for this transaction!")
                    st.stop()

            add_transaction({
                "date": str(date),
                "stock": stock,
                "qty": qty,
                "price": price,
                "type": type_,
                "charges": charges
            })

            st.success("Transaction Added!")
            st.rerun()

    st.divider()

    cutoff = pd.Timestamp.today() - pd.DateOffset(months=3)

    if "date" in df.columns:
        df_filtered = df[df["date"] >= cutoff]
    else:
        df_filtered = df

    with st.expander("📊 Existing Transactions (Last 3 Months)", expanded=False):
        st.dataframe(df_filtered, use_container_width=True)

    st.divider()

    with st.expander("🛠️ Edit / Delete Transactions", expanded=False):

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

        st.subheader("✏️ Edit Transaction")

        edit_row = st.selectbox(
            "Select row to edit",
            df["row_index"],
            key="edit_row"
        )

        filtered = df[df["row_index"] == edit_row]

        if not filtered.empty:

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

# ================= TAB 3 =================
with tab3:
    st.subheader("📌 Holdings Breakdown")

    if holdings is not None and not holdings.empty:
        st.dataframe(holdings, use_container_width=True)
    else:
        st.info("No holdings yet")

# ================= TAB 4 =================
with tab4:
    st.subheader("🧠 Stock Scoring Engine (Coming Next)")

    st.info("""
    Scoring system:
    - Fundamentals (40)
    - Valuation (25)
    - Technical (20)
    - Macro (15)
    """)

    st.warning("Next step: build scoring + auto rebalance engine")

# ================= TAB 5 - FUNDS =================
with tab5:

    st.subheader("💰 Funds Management")

    cf = load_cashflow()

    st.write("### Cashflow History")
    st.dataframe(cf, use_container_width=True)

    st.divider()

    st.subheader("➕ Add Funds")

    with st.form("fund_form"):

        date = st.date_input("Date")
        amount = st.number_input("Amount", min_value=0.0)
        type_ = st.selectbox("Type", ["CREDIT", "DEBIT"])
        note = st.text_input("Note")

        submit = st.form_submit_button("Add Fund Entry")

        if submit:

            add_cashflow_entry({
                "date": str(date),
                "type": type_,
                "amount": amount,
                "note": note
            })

            st.success("Fund Entry Added!")
            st.rerun()
