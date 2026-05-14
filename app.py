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
# SECTION 2: GOOGLE SHEETS — CLIENT & ACCESSORS
# ================================================================

def get_client():

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]

    creds_dict = dict(st.secrets["gcp_service_account"])

    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        creds_dict,
        scope
    )

    return gspread.authorize(creds)


def get_sheet():

    client = get_client()

    sheet_name = st.secrets["sheets"]["sheet_name"]

    return client.open(sheet_name).worksheet("transactions")


def get_cashflow_sheet():

    client = get_client()

    sheet_name = st.secrets["sheets"]["sheet_name"]

    return client.open(sheet_name).worksheet("load_cashflows")


def get_nav_sheet():

    client = get_client()

    sheet_name = st.secrets["sheets"]["sheet_name"]

    return client.open(sheet_name).worksheet("nav_history")


# ================================================================
# SECTION 3: TRANSACTION CRUD
# ================================================================

def load_transactions():

    sheet = get_sheet()

    data = sheet.get_all_records()

    df = pd.DataFrame(data)

    if df.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "stock",
                "qty",
                "price",
                "type",
                "charges"
            ]
        )

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


# ================================================================
# SECTION 4: CASHFLOW CRUD
# ================================================================

def load_cashflows():

    sheet = get_cashflow_sheet()

    data = sheet.get_all_records()

    df = pd.DataFrame(data)

    if df.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "type",
                "amount",
                "note"
            ]
        )

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


# ================================================================
# SECTION 5: PRICE FETCH
# ================================================================

def get_price(stock):

    try:
        val = yf.Ticker(
            str(stock).strip() + ".NS"
        ).history(period="1d")["Close"].iloc[-1]

        return float(
            pd.to_numeric(val, errors="coerce") or 0.0
        )

    except Exception:
        return 0.0


# ================================================================
# SECTION 6: PORTFOLIO CALCULATIONS
# ================================================================

def compute_portfolio(df):

    if df.empty:
        return 0.0, 0.0, 0.0, pd.DataFrame()

    df = df.copy()

    df = sanitize_numeric(
        df,
        ["qty", "price", "charges"]
    )

    df["type"] = df["type"].astype(str).str.strip().str.upper()

    df["amount"] = df["qty"] * df["price"]

    multiplier = df["type"].map(
        lambda t: 1.0 if t == "BUY" else -1.0
    )

    df["signed_qty"] = df["qty"] * multiplier

    holdings = (
        df.groupby("stock")
        .agg({"signed_qty": "sum"})
        .reset_index()
    )

    holdings.columns = ["stock", "qty"]

    holdings["qty"] = pd.to_numeric(
        holdings["qty"],
        errors="coerce"
    ).fillna(0.0)

    holdings = holdings[holdings["qty"] > 0].copy()

    if holdings.empty:

        buy_cost = float(
            df.loc[df["type"] == "BUY", "amount"].sum()
        )

        sell_proceeds = float(
            df.loc[df["type"] == "SELL", "amount"].sum()
        )

        total_charges = float(df["charges"].sum())

        realised_pnl = (
            sell_proceeds
            - buy_cost
            - total_charges
        )

        return 0.0, 0.0, realised_pnl, pd.DataFrame()

    buys = df[df["type"] == "BUY"].copy()

    avg_cost = (
        buys.groupby("stock")
        .apply(
            lambda x:
            (x["qty"] * x["price"]).sum()
            / x["qty"].sum()
        )
        .reset_index()
    )

    avg_cost.columns = ["stock", "avg_price"]

    holdings = holdings.merge(
        avg_cost,
        on="stock",
        how="left"
    )

    holdings["avg_price"] = pd.to_numeric(
        holdings["avg_price"],
        errors="coerce"
    ).fillna(0.0)

    holdings["invested"] = (
        holdings["qty"] * holdings["avg_price"]
    )

    holdings["cmp"] = holdings["stock"].apply(get_price)

    holdings["value"] = (
        holdings["qty"] * holdings["cmp"]
    )

    invested = float(holdings["invested"].sum())

    total_value = float(holdings["value"].sum())

    unrealised_pnl = total_value - invested

    sell_df = df[df["type"] == "SELL"].copy()

    sell_proceeds = float(sell_df["amount"].sum())

    sold_cost = 0.0

    for stock, grp in sell_df.groupby("stock"):

        avg_row = avg_cost[avg_cost["stock"] == stock]

        if not avg_row.empty:

            avg_p = float(avg_row["avg_price"].iloc[0])

            sold_cost += (
                float(grp["qty"].sum()) * avg_p
            )

    realised_pnl = sell_proceeds - sold_cost

    total_charges = float(df["charges"].sum())

    pnl = (
        unrealised_pnl
        + realised_pnl
        - total_charges
    )

    holdings["pnl"] = (
        holdings["value"] - holdings["invested"]
    ).round(2)

    return invested, total_value, pnl, holdings


def compute_xirr(df):

    if df.empty:
        return 0.0

    df = df.copy()

    df = sanitize_numeric(
        df,
        ["qty", "price", "charges"]
    )

    df["type"] = df["type"].astype(str).str.strip().str.upper()

    df["date"] = pd.to_datetime(
        df["date"],
        errors="coerce"
    )

    df = df.dropna(subset=["date"])

    if df.empty:
        return 0.0

    cashflows = []

    for _, row in df.iterrows():

        amount = (
            float(row["qty"])
            * float(row["price"])
        )

        if row["type"] == "BUY":

            cf = -(
                amount
                + float(row["charges"])
            )

        else:

            cf = (
                amount
                - float(row["charges"])
            )

        cashflows.append(
            (
                row["date"].to_pydatetime(),
                cf
            )
        )

    multiplier = df["type"].map(
        lambda t: 1.0 if t == "BUY" else -1.0
    )

    df["signed_qty"] = df["qty"] * multiplier

    open_holdings = (
        df.groupby("stock")["signed_qty"]
        .sum()
    )

    open_holdings = open_holdings[
        open_holdings > 0
    ]

    terminal_value = sum(
        float(qty) * get_price(str(stock))
        for stock, qty in open_holdings.items()
    )

    if terminal_value > 0:

        cashflows.append(
            (
                datetime.today(),
                float(terminal_value)
            )
        )

    if len(cashflows) < 2:
        return 0.0

    try:

        result = xirr(cashflows)

        return (
            float(result)
            if result is not None
            else 0.0
        )

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
                "symbol": item.get(
                    "symbol",
                    ""
                ).replace(".NS", "")
            }

            for item in results

            if item.get(
                "symbol",
                ""
            ).endswith(".NS")
        ]

    except Exception:
        return []


# ================================================================
# SECTION 7: FREE CASH
# ================================================================

def calculate_free_cash(df):

    cash_df = load_cashflows()

    if cash_df.empty:
        return 0.0

    total_cash = float(cash_df["amount"].sum())

    if df.empty:
        return round(total_cash, 2)

    df = df.copy()

    df = sanitize_numeric(
        df,
        ["qty", "price", "charges"]
    )

    df["type"] = df["type"].astype(str).str.strip().str.upper()

    df["amount"] = df["qty"] * df["price"]

    buy_spent = float(
        df.loc[df["type"] == "BUY", "amount"].sum()
    )

    sell_received = float(
        df.loc[df["type"] == "SELL", "amount"].sum()
    )

    charges_total = float(df["charges"].sum())

    available = (
        total_cash
        - buy_spent
        - charges_total
        + sell_received
    )

    return round(max(available, 0.0), 2)


# ================================================================
# SECTION 7B: NAV SYSTEM
# ================================================================

def calculate_total_units(cash_df):

    if cash_df.empty:
        return 0.0

    credit = float(
        cash_df.loc[
            cash_df["type"] == "CREDIT",
            "amount"
        ].sum()
    )

    debit = float(
        cash_df.loc[
            cash_df["type"] == "DEBIT",
            "amount"
        ].sum()
    )

    net_cash = credit - debit

    if net_cash <= 0:
        return 0.0

    units = net_cash / 10

    return float(units)


def calculate_nav(
    total_value,
    free_cash,
    units
):

    total_assets = (
        float(total_value)
        + float(free_cash)
    )

    if units <= 0:
        return 10.0

    return round(total_assets / units, 2)


def save_nav_history(
    nav,
    total_assets,
    units
):

    sheet = get_nav_sheet()

    today = str(datetime.today().date())

    data = sheet.get_all_records()

    existing_dates = [
        str(x.get("date"))
        for x in data
    ]

    row_data = [
        today,
        float(nav),
        float(total_assets),
        float(units)
    ]

    if today in existing_dates:

        row_num = existing_dates.index(today) + 2

        sheet.update(
            f"A{row_num}:D{row_num}",
            [row_data]
        )

    else:

        sheet.append_row(row_data)


def load_nav_history():

    sheet = get_nav_sheet()

    data = sheet.get_all_records()

    df = pd.DataFrame(data)

    if df.empty:

        return pd.DataFrame(
            columns=[
                "date",
                "nav",
                "portfolio_value",
                "units"
            ]
        )

    df.columns = [
        str(c).strip().lower()
        for c in df.columns
    ]

    df = sanitize_numeric(
        df,
        ["nav", "portfolio_value", "units"]
    )

    df["date"] = pd.to_datetime(df["date"])

    return df


# ================================================================
# SECTION 8: STREAMLIT APP
# ================================================================

st.set_page_config(
    page_title="Portfolio Tracker",
    layout="wide"
)

st.title("📊 My Mutual Fund Tracker")

# ---- LOAD DATA ----

df = load_transactions()

if df is None:
    df = pd.DataFrame()

if df.empty:

    st.warning(
        "No transactions found. Showing empty dashboard."
    )

    df = pd.DataFrame(
        columns=[
            "date",
            "stock",
            "qty",
            "price",
            "type",
            "charges",
            "row_index"
        ]
    )

df = sanitize_numeric(
    df,
    ["qty", "price", "charges"]
)

if "type" in df.columns:
    df["type"] = (
        df["type"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

if "date" in df.columns:
    df["date"] = pd.to_datetime(
        df["date"],
        errors="coerce"
    )

# ---- CALCULATIONS ----

invested, value, pnl, holdings = compute_portfolio(df)

xirr_val = compute_xirr(df)

free_cash = calculate_free_cash(df)

cash_df = load_cashflows()

units = calculate_total_units(cash_df)

total_assets = value + free_cash

nav = calculate_nav(
    value,
    free_cash,
    units
)

save_nav_history(
    nav,
    total_assets,
    units
)

nav_df = load_nav_history()

# ---- TABS ----

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📈 Dashboard",
    "➕ Add Transaction",
    "📌 Holdings",
    "🧠 Scoring (WIP)",
    "💰 Funds"
])

# ================================================================
# TAB 1: DASHBOARD
# ================================================================

with tab1:

    st.subheader("📈 Portfolio Overview")

    col1, col2, col3, col4, col5, col6 = st.columns(6)

    col1.metric(
        "Invested",
        f"₹{invested:,.2f}"
    )

    col2.metric(
        "Current Value",
        f"₹{value:,.2f}"
    )

    col3.metric(
        "P&L",
        f"₹{pnl:,.2f}"
    )

    col4.metric(
        "XIRR",
        f"{(xirr_val or 0.0) * 100:.2f}%"
    )

    col5.metric(
        "Free Cash",
        f"₹{free_cash:,.2f}"
    )

    col6.metric(
        "NAV",
        f"₹{nav:.2f}"
    )

    total_charges_display = (
        float(df["charges"].sum())
        if not df.empty
        else 0.0
    )

    gross_pnl = pnl + total_charges_display

    st.caption(
        f"📊 Gross P&L: ₹{gross_pnl:,.2f} | "
        f"Charges: ₹{total_charges_display:,.2f} | "
        f"Net P&L: ₹{pnl:,.2f}"
    )

    st.divider()

    st.subheader("📈 Mutual Fund NAV")

    if nav_df is not None and not nav_df.empty:

        nav_chart = px.line(
            nav_df,
            x="date",
            y="nav",
            markers=True,
            title="NAV Growth"
        )

        st.plotly_chart(
            nav_chart,
            use_container_width=True
        )

    else:

        st.info(
            "NAV history not available yet"
        )

    st.divider()

    st.subheader("📊 Allocation")

    if holdings is not None and not holdings.empty:

        fig = px.pie(
            holdings,
            values="value",
            names="stock"
        )

        st.plotly_chart(
            fig,
            use_container_width=True
        )

    else:

        st.info("No holdings yet")

# ================================================================
# TAB 2: ADD TRANSACTION
# ================================================================

with tab2:

    st.subheader("➕ Add Transaction")

    search_query = st.text_input(
        "Search Stock"
    )

    stock_options = (
        search_stocks(search_query)
        if search_query
        else []
    )

    if not stock_options:

        stock_options = [
            {
                "label": "No results",
                "symbol": ""
            }
        ]

    selected_stock = st.selectbox(
        "Select Stock",
        stock_options,
        format_func=lambda x: x["label"]
    )

    stock = selected_stock["symbol"]

    with st.form("add_form"):

        date = st.date_input("Date")

        qty = st.number_input(
            "Qty",
            min_value=0.0
        )

        price = st.number_input(
            "Price",
            min_value=0.0
        )

        type_ = st.selectbox(
            "Type",
            ["BUY", "SELL"]
        )

        charges = st.number_input(
            "Charges",
            min_value=0.0
        )

        submit = st.form_submit_button("Add")

        if submit:

            add_transaction({
                "date": str(date),
                "stock": stock,
                "qty": float(qty),
                "price": float(price),
                "type": type_,
                "charges": float(charges)
            })

            st.success("Transaction Added!")

            st.rerun()

# ================================================================
# TAB 3: HOLDINGS
# ================================================================

with tab3:

    st.subheader("📌 Holdings Breakdown")

    if holdings is not None and not holdings.empty:

        st.dataframe(
            holdings,
            use_container_width=True
        )

    else:

        st.info("No holdings yet")

# ================================================================
# TAB 4: SCORING
# ================================================================

with tab4:

    st.subheader(
        "🧠 Stock Scoring Engine (Coming Next)"
    )

    st.info("""
    Scoring system:
    - Fundamentals
    - Valuation
    - Technical
    - Macro
    """)

# ================================================================
# TAB 5: FUNDS
# ================================================================

with tab5:

    st.subheader("💰 Funds Management")

    cf = load_cashflows()

    st.write("### Cashflow History")

    st.dataframe(
        cf,
        use_container_width=True
    )

    st.divider()

    st.subheader("➕ Add Funds")

    with st.form("fund_form"):

        date = st.date_input("Date")

        amount = st.number_input(
            "Amount",
            min_value=0.0
        )

        type_ = st.selectbox(
            "Type",
            ["CREDIT", "DEBIT"]
        )

        note = st.text_input("Note")

        submit = st.form_submit_button(
            "Add Fund Entry"
        )

        if submit:

            add_cashflow_entry({
                "date": str(date),
                "type": type_,
                "amount": float(amount),
                "note": note
            })

            st.success("Fund Entry Added!")

            st.rerun()
