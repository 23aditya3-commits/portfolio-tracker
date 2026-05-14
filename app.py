import streamlit as st
import pandas as pd
import plotly.express as px
import gspread
import yfinance as yf
from pyxirr import xirr
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials


# ================================================================
# SECTION 1: HELPERS
# ================================================================

def sanitize_numeric(df, cols):
    """
    Robustly convert columns to float.
    Uses per-element map() — works across all pandas versions
    and handles empty strings, None, mixed int/str from Google Sheets.
    """
    def _clean(v):
        s = str(v).strip()
        if s in ("", "None", "nan", "NaN", "NaT"):
            return 0.0
        try:
            return float(s)
        except (ValueError, TypeError):
            return 0.0

    for col in cols:
        if col in df.columns:
            df[col] = df[col].map(_clean)
    return df


# ================================================================
# SECTION 2: GOOGLE SHEETS — CLIENT & SHEET ACCESSORS
# ================================================================

def get_client():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)


def get_sheet():
    client = get_client()
    sheet_name = st.secrets["sheets"]["sheet_name"]
    return client.open(sheet_name).worksheet("transactions")


def get_cashflow_sheet():
    client = get_client()
    sheet_name = st.secrets["sheets"]["sheet_name"]
    return client.open(sheet_name).worksheet("load_cashflows")


# ================================================================
# SECTION 3: GOOGLE SHEETS — TRANSACTION CRUD
# ================================================================

def load_transactions():
    sheet = get_sheet()
    data = sheet.get_all_records()
    df = pd.DataFrame(data)

    if df.empty:
        return pd.DataFrame(columns=["date", "stock", "qty", "price", "type", "charges"])

    df.columns = [str(c).strip().lower() for c in df.columns]
    df = sanitize_numeric(df, ["qty", "price", "charges"])
    df["type"] = df["type"].astype(str).str.strip().str.upper()
    df["row_index"] = range(2, len(df) + 2)
    return df


def add_transaction(row):
    sheet = get_sheet()
    sheet.append_row([
        row["date"],
        row["stock"],
        float(row["qty"]),
        float(row["price"]),
        row["type"],
        float(row["charges"])
    ])


def delete_transaction(row_index):
    sheet = get_sheet()
    sheet.delete_rows(row_index)


def update_transaction(row_index, row):
    sheet = get_sheet()
    sheet.update(
        f"A{row_index}:F{row_index}",
        [[
            row["date"],
            row["stock"],
            float(row["qty"]),
            float(row["price"]),
            row["type"],
            float(row["charges"])
        ]]
    )


def clear_transactions():
    sheet = get_sheet()
    sheet.clear()
    sheet.append_row(["date", "stock", "qty", "price", "type", "charges"])


# ================================================================
# SECTION 4: GOOGLE SHEETS — CASHFLOW CRUD
# ================================================================

def load_cashflows():
    sheet = get_cashflow_sheet()
    data = sheet.get_all_records()
    df = pd.DataFrame(data)

    if df.empty:
        return pd.DataFrame(columns=["date", "type", "amount", "note"])

    df.columns = [str(c).strip().lower() for c in df.columns]
    df = sanitize_numeric(df, ["amount"])
    df["type"] = df["type"].astype(str).str.strip().str.upper()
    return df


def add_cashflow_entry(row):
    sheet = get_cashflow_sheet()
    sheet.append_row([
        row["date"],
        row["type"],
        float(row["amount"]),
        row["note"]
    ])


def clear_cashflow():
    sheet = get_cashflow_sheet()
    sheet.clear()
    sheet.append_row(["date", "type", "amount", "note"])


# ================================================================
# SECTION 5: PORTFOLIO — PRICE FETCH
# ================================================================

def get_price(stock):
    """Always returns a plain Python float."""
    try:
        val = yf.Ticker(stock + ".NS").history(period="1d")["Close"].iloc[-1]
        return float(val)
    except Exception:
        return 0.0


# ================================================================
# SECTION 6: PORTFOLIO — CALCULATIONS
# ================================================================

def compute_portfolio(df):
    if df.empty:
        return 0.0, 0.0, 0.0, pd.DataFrame()

    df = df.copy()
    df = sanitize_numeric(df, ["qty", "price", "charges"])
    df["type"] = df["type"].astype(str).str.strip().str.upper()

    df["amount"] = df["qty"] * df["price"]
    invested = float(df.loc[df["type"] == "BUY", "amount"].sum())

    multiplier = df["type"].map(lambda t: 1.0 if t == "BUY" else -1.0)
    df["signed_qty"] = df["qty"] * multiplier

    holdings = df.groupby("stock").agg({"signed_qty": "sum"}).reset_index()
    holdings.columns = ["stock", "qty"]
    holdings = holdings[holdings["qty"] > 0].copy()

    holdings["qty"] = holdings["qty"].map(lambda v: float(v) if v else 0.0)
    holdings["cmp"] = holdings["stock"].apply(get_price).map(float)

    # DEBUG — remove once fixed
    st.write("### DEBUG holdings dtypes", holdings.dtypes.to_dict())
    st.write("### DEBUG holdings sample", holdings.head())
    for col in ["qty", "cmp"]:
        st.write(f"DEBUG {col} values + types:", [(v, type(v).__name__) for v in holdings[col].tolist()])

    holdings["value"] = holdings["qty"] * holdings["cmp"]

    total_value = float(holdings["value"].sum())
    pnl = total_value - invested

    return invested, total_value, pnl, holdings


def compute_xirr(df):
    if df.empty:
        return 0.0

    df = df.copy()
    df = sanitize_numeric(df, ["qty", "price", "charges"])
    df["type"] = df["type"].astype(str).str.strip().str.upper()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    cashflows = []
    for _, row in df.iterrows():
        if pd.isnull(row["date"]):
            continue
        amount = float(row["qty"]) * float(row["price"])
        if row["type"] == "BUY":
            amount = -(amount + float(row["charges"]))
        else:
            amount = amount - float(row["charges"])
        # pyxirr needs plain Python datetime, not pandas Timestamp
        cashflows.append((row["date"].to_pydatetime(), amount))

    if not cashflows:
        return 0.0

    holdings = df.groupby("stock")["qty"].sum().reset_index()
    total_value = sum(
        float(row["qty"]) * get_price(str(row["stock"]))
        for _, row in holdings.iterrows()
        if float(row["qty"]) > 0
    )
    cashflows.append((datetime.today(), float(total_value)))

    try:
        return xirr(cashflows)
    except Exception:
        return 0.0


def search_stocks(query):
    if not query:
        return []
    try:
        results = yf.Search(query).quotes
        return [
            {
                "label": item.get("symbol", ""),
                "symbol": item.get("symbol", "").replace(".NS", "")
            }
            for item in results
            if item.get("symbol", "").endswith(".NS")
        ]
    except Exception:
        return []


# ================================================================
# SECTION 7: PORTFOLIO — FREE CASH SYSTEM
# ================================================================

def calculate_free_cash(df):
    cash_df = load_cashflows()
    if cash_df.empty:
        return 0.0

    total_cash = float(cash_df["amount"].sum())

    if df.empty:
        return round(total_cash, 2)

    df = df.copy()
    df = sanitize_numeric(df, ["qty", "price", "charges"])
    df["type"] = df["type"].astype(str).str.strip().str.upper()
    df["amount"] = df["qty"] * df["price"]

    buy_spent    = float(df.loc[df["type"] == "BUY",  "amount"].sum())
    sell_received = float(df.loc[df["type"] == "SELL", "amount"].sum())
    charges_total = float(df["charges"].sum())

    available = total_cash - buy_spent - charges_total + sell_received
    return round(max(available, 0.0), 2)


def check_free_cash_before_buy(df, new_date, qty, price):
    cash_df = load_cashflows()
    if cash_df.empty:
        return False

    total_cash = float(cash_df["amount"].sum())

    df = df.copy()
    df = sanitize_numeric(df, ["qty", "price", "charges"])
    df["type"] = df["type"].astype(str).str.strip().str.upper()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    past = df[df["date"] <= pd.to_datetime(new_date)].copy()
    past["amount"] = past["qty"] * past["price"]

    buy_spent     = float(past.loc[past["type"] == "BUY",  "amount"].sum())
    sell_received = float(past.loc[past["type"] == "SELL", "amount"].sum())
    charges_total = float(past["charges"].sum())

    available = total_cash - buy_spent - charges_total + sell_received
    return available >= float(qty) * float(price)


# ================================================================
# SECTION 8: STREAMLIT APP
# ================================================================

st.set_page_config(page_title="Portfolio Tracker", layout="wide")
st.title("📊 My Mutual Fund Tracker")

# ---- LOAD DATA ----
df = load_transactions()

if df is None:
    df = pd.DataFrame()

if df.empty:
    st.warning("No transactions found. Showing empty dashboard.")
    df = pd.DataFrame(columns=["date", "stock", "qty", "price", "type", "charges", "row_index"])

# Final safety pass at app level
df = sanitize_numeric(df, ["qty", "price", "charges"])
if "type" in df.columns:
    df["type"] = df["type"].astype(str).str.strip().str.upper()
if "date" in df.columns:
    df["date"] = pd.to_datetime(df["date"], errors="coerce")

# ---- CALCULATIONS ----
invested, value, pnl, holdings = compute_portfolio(df)
xirr_val  = compute_xirr(df)
free_cash = calculate_free_cash(df)

# ---- TABS ----
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📈 Dashboard",
    "➕ Add Transaction",
    "📌 Holdings",
    "🧠 Scoring (WIP)",
    "💰 Funds"
])

# ================= TAB 1: DASHBOARD =================
with tab1:
    st.subheader("📈 Portfolio Overview")

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Invested",       f"₹{invested:,.0f}")
    col2.metric("Current Value",  f"₹{value:,.0f}")
    col3.metric("P&L",            f"₹{pnl:,.0f}")
    col4.metric("XIRR",           f"{xirr_val * 100:.2f}%")
    col5.metric("Free Cash",      f"₹{free_cash:,.0f}")

    st.divider()
    st.subheader("📊 Allocation")

    if holdings is not None and not holdings.empty:
        fig = px.pie(holdings, values="value", names="stock")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No holdings yet")

# ================= TAB 2: ADD TRANSACTION =================
with tab2:
    st.subheader("➕ Add Transaction")

    search_query  = st.text_input("Search Stock (e.g. hdfc, reliance)")
    stock_options = search_stocks(search_query) if search_query else []

    if not stock_options:
        stock_options = [{"label": "No results", "symbol": ""}]

    selected_stock = st.selectbox("Select Stock", stock_options, format_func=lambda x: x["label"])
    stock = selected_stock["symbol"]

    with st.form("add_form"):
        date    = st.date_input("Date")
        qty     = st.number_input("Qty",     min_value=0.0)
        price   = st.number_input("Price",   min_value=0.0)
        type_   = st.selectbox("Type", ["BUY", "SELL"])
        charges = st.number_input("Charges", min_value=0.0)
        submit  = st.form_submit_button("Add")

        if submit:
            qty   = float(qty)
            price = float(price)

            if type_ == "BUY":
                can_buy = check_free_cash_before_buy(df, date, qty, price)
                if not can_buy:
                    st.error("❌ Insufficient Free Cash for this transaction!")
                    st.stop()

            add_transaction({
                "date":    str(date),
                "stock":   stock,
                "qty":     qty,
                "price":   price,
                "type":    type_,
                "charges": float(charges)
            })
            st.success("Transaction Added!")
            st.rerun()

    st.divider()

    cutoff      = pd.Timestamp.today() - pd.DateOffset(months=3)
    df_filtered = df[df["date"] >= cutoff] if "date" in df.columns else df

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

        edit_row = st.selectbox("Select row to edit", df["row_index"], key="edit_row")
        filtered = df[df["row_index"] == edit_row]

        if not filtered.empty:
            edit_data = filtered.iloc[0]

            with st.form("edit_form"):
                date       = st.date_input("Date",    value=pd.to_datetime(edit_data["date"]))
                stock_edit = st.text_input("Stock",   value=edit_data["stock"])
                qty        = st.number_input("Qty",   value=float(edit_data["qty"]))
                price      = st.number_input("Price", value=float(edit_data["price"]))
                type_      = st.selectbox("Type", ["BUY", "SELL"])
                charges    = st.number_input("Charges", value=float(edit_data["charges"]))
                update_btn = st.form_submit_button("Update")

                if update_btn:
                    update_transaction(edit_row, {
                        "date":    str(date),
                        "stock":   stock_edit,
                        "qty":     float(qty),
                        "price":   float(price),
                        "type":    type_,
                        "charges": float(charges)
                    })
                    st.success("Updated!")
                    st.rerun()

# ================= TAB 3: HOLDINGS =================
with tab3:
    st.subheader("📌 Holdings Breakdown")

    if holdings is not None and not holdings.empty:
        st.dataframe(holdings, use_container_width=True)
    else:
        st.info("No holdings yet")

# ================= TAB 4: SCORING =================
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

# ================= TAB 5: FUNDS =================
with tab5:
    st.subheader("💰 Funds Management")

    cf = load_cashflows()
    st.write("### Cashflow History")
    st.dataframe(cf, use_container_width=True)

    st.divider()
    st.subheader("➕ Add Funds")

    with st.form("fund_form"):
        date   = st.date_input("Date")
        amount = st.number_input("Amount", min_value=0.0)
        type_  = st.selectbox("Type", ["CREDIT", "DEBIT"])
        note   = st.text_input("Note")
        submit = st.form_submit_button("Add Fund Entry")

        if submit:
            add_cashflow_entry({
                "date":   str(date),
                "type":   type_,
                "amount": float(amount),
                "note":   note
            })
            st.success("Fund Entry Added!")
            st.rerun()
