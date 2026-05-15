import streamlit as st
import pandas as pd
import plotly.express as px
import gspread
import yfinance as yf
import numpy as np
from pyxirr import xirr
from datetime import datetime, time
from oauth2client.service_account import ServiceAccountCredentials


# ================================================================
# SECTION 1: HELPERS
# ================================================================

def sanitize_numeric(df, cols):
    """Robustly convert columns to float."""
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


def get_nav_sheet():
    client = get_client()
    sheet_name = st.secrets["sheets"]["sheet_name"]
    return client.open(sheet_name).worksheet("nav_history")


def get_score_sheet():
    client = get_client()
    sheet_name = st.secrets["sheets"]["sheet_name"]
    return client.open(sheet_name).worksheet("score_history")


# ================================================================
# SECTION 3: TRANSACTION CRUD
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
# SECTION 4: CASHFLOW CRUD
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
# SECTION 5: PRICE FETCH (SESSION-CACHED)
# ================================================================

def get_price(stock):
    """Fetch single stock price — plain Python float."""
    try:
        val = yf.Ticker(str(stock).strip() + ".NS").history(period="1d")["Close"].iloc[-1]
        return float(pd.to_numeric(val, errors="coerce") or 0.0)
    except Exception:
        return 0.0


def fetch_all_prices(stocks):
    """Batch fetch prices for all stocks using yfinance.download."""
    if not stocks:
        return {}
    try:
        symbols = [s.strip() + ".NS" for s in stocks]
        raw = yf.download(symbols, period="1d", auto_adjust=True, progress=False)["Close"]
        prices = {}
        if len(symbols) == 1:
            prices[stocks[0]] = float(pd.to_numeric(raw.iloc[-1], errors="coerce") or 0.0)
        else:
            for sym, stock in zip(symbols, stocks):
                try:
                    prices[stock] = float(pd.to_numeric(raw[sym].iloc[-1], errors="coerce") or 0.0)
                except Exception:
                    prices[stock] = 0.0
        return prices
    except Exception:
        # Fallback: fetch one by one
        return {s: get_price(s) for s in stocks}


@st.cache_data(ttl=300, show_spinner=False)
def get_cached_prices(stocks_tuple):
    """
    Cache prices for 5 min (ttl=300s).
    Accepts a tuple (hashable) of stock symbols.
    """
    return fetch_all_prices(list(stocks_tuple))


# ================================================================
# SECTION 6: PORTFOLIO CALCULATIONS
# ================================================================

def compute_portfolio(df, prices=None):
    if df.empty:
        return 0.0, 0.0, 0.0, pd.DataFrame()

    df = df.copy()
    df = sanitize_numeric(df, ["qty", "price", "charges"])
    df["type"] = df["type"].astype(str).str.strip().str.upper()
    df["amount"] = df["qty"] * df["price"]

    multiplier = df["type"].map(lambda t: 1.0 if t == "BUY" else -1.0)
    df["signed_qty"] = df["qty"] * multiplier

    holdings = df.groupby("stock").agg({"signed_qty": "sum"}).reset_index()
    holdings.columns = ["stock", "qty"]
    holdings["qty"] = pd.to_numeric(holdings["qty"], errors="coerce").fillna(0.0).astype("float64")
    holdings = holdings[holdings["qty"] > 0].copy()

    if holdings.empty:
        buy_cost      = float(df.loc[df["type"] == "BUY",  "amount"].sum())
        sell_proceeds = float(df.loc[df["type"] == "SELL", "amount"].sum())
        total_charges = float(df["charges"].sum())
        realised_pnl  = sell_proceeds - buy_cost - total_charges
        return 0.0, 0.0, realised_pnl, pd.DataFrame()

    buys = df[df["type"] == "BUY"].copy()
    avg_cost = (
        buys.groupby("stock")
        .apply(lambda x: (x["qty"] * x["price"]).sum() / x["qty"].sum())
        .reset_index()
    )
    avg_cost.columns = ["stock", "avg_price"]

    holdings = holdings.merge(avg_cost, on="stock", how="left")
    holdings["avg_price"] = pd.to_numeric(holdings["avg_price"], errors="coerce").fillna(0.0)
    holdings["invested"]  = holdings["qty"] * holdings["avg_price"]

    # Use pre-fetched prices if available, else fetch individually
    if prices:
        holdings["cmp"] = holdings["stock"].map(lambda s: float(prices.get(s, 0.0)))
    else:
        holdings["cmp"] = holdings["stock"].apply(get_price)
    holdings["cmp"]   = pd.to_numeric(holdings["cmp"], errors="coerce").fillna(0.0).astype("float64")
    holdings["value"] = holdings["qty"] * holdings["cmp"]

    invested       = float(holdings["invested"].sum())
    total_value    = float(holdings["value"].sum())
    unrealised_pnl = total_value - invested

    sell_df       = df[df["type"] == "SELL"].copy()
    sell_proceeds = float(sell_df["amount"].sum())

    sold_cost = 0.0
    for stock, grp in sell_df.groupby("stock"):
        avg_row = avg_cost[avg_cost["stock"] == stock]
        if not avg_row.empty:
            avg_p = float(avg_row["avg_price"].iloc[0])
            sold_cost += float(grp["qty"].sum()) * avg_p

    realised_pnl  = sell_proceeds - sold_cost
    total_charges = float(df["charges"].sum())
    pnl = unrealised_pnl + realised_pnl - total_charges

    holdings["pnl"] = (holdings["value"] - holdings["invested"]).round(2)

    return invested, total_value, pnl, holdings


def compute_xirr(df, prices=None):
    if df.empty:
        return 0.0

    df = df.copy()
    df = sanitize_numeric(df, ["qty", "price", "charges"])
    df["type"] = df["type"].astype(str).str.strip().str.upper()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])

    if df.empty:
        return 0.0

    cashflows = []
    for _, row in df.iterrows():
        amount = float(row["qty"]) * float(row["price"])
        if row["type"] == "BUY":
            cf = -(amount + float(row["charges"]))
        else:
            cf = amount - float(row["charges"])
        cashflows.append((row["date"].to_pydatetime(), cf))

    multiplier = df["type"].map(lambda t: 1.0 if t == "BUY" else -1.0)
    df["signed_qty"] = df["qty"] * multiplier
    open_holdings = df.groupby("stock")["signed_qty"].sum()
    open_holdings = open_holdings[open_holdings > 0]

    terminal_value = sum(
        float(qty) * (float(prices.get(str(stock), 0.0)) if prices else get_price(str(stock)))
        for stock, qty in open_holdings.items()
    )

    if terminal_value > 0:
        cashflows.append((datetime.today(), float(terminal_value)))

    if len(cashflows) < 2:
        return 0.0

    try:
        result = xirr(cashflows)
        return float(result) if result is not None else 0.0
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
    df = sanitize_numeric(df, ["qty", "price", "charges"])
    df["type"] = df["type"].astype(str).str.strip().str.upper()
    df["amount"] = df["qty"] * df["price"]

    buy_spent     = float(df.loc[df["type"] == "BUY",  "amount"].sum())
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
# SECTION 8: NAV SYSTEM
# ================================================================

def calculate_total_units(cash_df):
    if cash_df.empty:
        return 0.0
    credit = float(cash_df.loc[cash_df["type"] == "CREDIT", "amount"].sum())
    debit  = float(cash_df.loc[cash_df["type"] == "DEBIT",  "amount"].sum())
    net_cash = credit - debit
    if net_cash <= 0:
        return 0.0
    return round(net_cash / 10, 4)


def calculate_nav(total_value, free_cash, units):
    total_assets = float(total_value) + float(free_cash)
    if units <= 0:
        return 10.0
    return round(total_assets / units, 2)


def save_nav_history(nav, total_assets, units):
    try:
        sheet = get_nav_sheet()
        today = str(datetime.today().date())
        data  = sheet.get_all_records()
        existing_dates = [str(x.get("date")) for x in data]
        row_data = [today, float(nav), float(total_assets), float(units)]
        if today in existing_dates:
            row_num = existing_dates.index(today) + 2
            sheet.update(f"A{row_num}:D{row_num}", [row_data])
        else:
            sheet.append_row(row_data)
    except Exception:
        pass


def load_nav_history():
    try:
        sheet = get_nav_sheet()
        data  = sheet.get_all_records()
        df    = pd.DataFrame(data)
        if df.empty:
            return pd.DataFrame(columns=["date", "nav", "portfolio_value", "units"])
        df.columns = [str(c).strip().lower() for c in df.columns]
        df = sanitize_numeric(df, ["nav", "portfolio_value", "units"])
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        return df
    except Exception:
        return pd.DataFrame(columns=["date", "nav", "portfolio_value", "units"])


# ================================================================
# SECTION 9: SCORING ENGINE (100 POINT SYSTEM)
# ================================================================

# ---------------- FUNDAMENTALS (40 pts) ----------------
def calculate_fundamental_score(info):
    score = 0
    roe            = float(info.get("returnOnEquity")   or 0) * 100
    revenue_growth = float(info.get("revenueGrowth")    or 0) * 100
    profit_growth  = float(info.get("earningsGrowth")   or 0) * 100
    debt_equity    = float(info.get("debtToEquity")     or 0)
    margin         = float(info.get("operatingMargins") or 0) * 100

    if roe            > 15: score += 8
    if revenue_growth > 10: score += 10
    if profit_growth  > 10: score += 10
    if debt_equity    <  1: score += 6
    if margin         > 15: score += 6

    return score, roe, revenue_growth, profit_growth, debt_equity, margin


# ---------------- VALUATION (25 pts) ----------------
def calculate_valuation_score(info):
    score = 0
    pe  = float(info.get("trailingPE")          or 0)
    pb  = float(info.get("priceToBook")         or 0)
    peg = float(info.get("pegRatio")            or 0)
    ev  = float(info.get("enterpriseToEbitda")  or 0)

    if pe  > 0 and pe  < 20: score += 10
    elif pe > 0 and pe < 30: score += 5
    if pb  > 0 and pb  < 3:  score += 5
    if peg > 0 and peg < 1.5: score += 5
    if ev  > 0 and ev  < 15: score += 5

    return score, pe, pb, peg, ev


# ---------------- TECHNICAL (20 pts) ----------------
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss  = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1]) if not rsi.empty else 50.0


def calculate_technical_score(ticker):
    try:
        hist  = ticker.history(period="1y")
        if hist.empty or len(hist) < 50:
            return 0, 0.0, 0.0, 50.0

        close  = hist["Close"]
        price  = float(close.iloc[-1])
        sma50  = float(close.rolling(50).mean().iloc[-1])
        sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else sma50
        rsi    = calculate_rsi(close)

        score = 0
        if price > sma50:                     score += 5
        if price > sma200:                    score += 5
        if 50 < rsi < 70:                     score += 5
        if price > float(close.iloc[-20]):    score += 5

        return score, sma50, sma200, rsi
    except Exception:
        return 0, 0.0, 0.0, 50.0


# ---------------- MACRO (15 pts) ----------------
def calculate_macro_score(stock):
    # Sector-based heuristic — extend this map as needed
    sector_scores = {
        "HDFCBANK": 12, "ICICIBANK": 12, "KOTAKBANK": 11,
        "INFY": 10,     "TCS": 10,       "WIPRO": 9,
        "RELIANCE": 11, "ONGC": 8,
        "ASIANPAINT": 10, "NESTLEIND": 10,
    }
    for key, val in sector_scores.items():
        if key in stock.upper():
            return val
    return 8   # default


# ---------------- MAIN ENGINE ----------------
def run_full_scoring(holdings):
    if holdings is None or holdings.empty:
        return pd.DataFrame()

    results = []
    stocks  = holdings["stock"].unique().tolist()

    progress = st.progress(0, text="Scoring stocks...")
    total    = len(stocks)

    for i, stock in enumerate(stocks):
        progress.progress((i + 1) / total, text=f"Scoring {stock}...")
        try:
            ticker = yf.Ticker(stock + ".NS")
            info   = ticker.info

            f_score, roe, rev, prof, debt, margin = calculate_fundamental_score(info)
            v_score, pe, pb, peg, ev              = calculate_valuation_score(info)
            t_score, sma50, sma200, rsi           = calculate_technical_score(ticker)
            m_score                               = calculate_macro_score(stock)

            total_score = f_score + v_score + t_score + m_score

            results.append({
                "stock":          stock,
                "fundamentals":   f_score,
                "valuation":      v_score,
                "technical":      t_score,
                "macro":          m_score,
                "total":          total_score,
                "roe_%":          round(roe,  2),
                "rev_growth_%":   round(rev,  2),
                "prof_growth_%":  round(prof, 2),
                "debt/equity":    round(debt, 2),
                "margin_%":       round(margin, 2),
                "pe":             round(pe,   2),
                "pb":             round(pb,   2),
                "peg":            round(peg,  2),
                "ev/ebitda":      round(ev,   2),
                "rsi":            round(rsi,  2),
            })
        except Exception:
            continue

    progress.empty()
    return pd.DataFrame(results).sort_values("total", ascending=False).reset_index(drop=True)


def should_update_scores():
    now          = datetime.now()
    current_time = now.time()
    return (
        time(10, 0) <= current_time <= time(10, 15)
        or
        time(15, 0) <= current_time <= time(15, 15)
    )


def load_score_history():
    try:
        sheet = get_score_sheet()
        data  = sheet.get_all_records()
        df    = pd.DataFrame(data)
        if df.empty:
            return pd.DataFrame(columns=[
                "date", "stock", "fundamentals", "roe",
                "revenue_growth", "profit_growth", "debt_equity", "margin"
            ])
        df.columns = [str(c).strip().lower() for c in df.columns]
        return df
    except Exception:
        return pd.DataFrame(columns=[
            "date", "stock", "fundamentals", "roe",
            "revenue_growth", "profit_growth", "debt_equity", "margin"
        ])


def save_fundamental_scores(holdings):
    if holdings is None or holdings.empty:
        return
    if not should_update_scores():
        return
    try:
        sheet      = get_score_sheet()
        history_df = load_score_history()
        now        = datetime.now()
        today      = str(now.date())
        session    = "MORNING" if now.hour < 12 else "EOD"

        existing = history_df[history_df["date"] == today]
        if not existing.empty:
            existing_session = existing[existing["stock"] == "__SESSION__"]
            if not existing_session.empty:
                if session in existing_session["fundamentals"].tolist():
                    return

        for stock in holdings["stock"].unique():
            result = calculate_fundamental_score(
                yf.Ticker(stock + ".NS").info
            )
            sheet.append_row([
                today, stock,
                result[0],                    # score
                round(result[1], 2),          # roe
                round(result[2], 2),          # revenue_growth
                round(result[3], 2),          # profit_growth
                round(result[4], 2),          # debt_equity
                round(result[5], 2),          # margin
            ])

        sheet.append_row([today, "__SESSION__", session, 0, 0, 0, 0, 0])
    except Exception:
        pass


# ================================================================
# SECTION 10: STREAMLIT APP
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

df = sanitize_numeric(df, ["qty", "price", "charges"])
if "type" in df.columns:
    df["type"] = df["type"].astype(str).str.strip().str.upper()
if "date" in df.columns:
    df["date"] = pd.to_datetime(df["date"], errors="coerce")

# ---- PRICE FETCH (cached, one-time per session) ----
open_stocks = tuple(sorted(
    df.groupby("stock").apply(
        lambda x: (
            x["qty"] * x["type"].map(lambda t: 1.0 if t == "BUY" else -1.0)
        ).sum()
    ).pipe(lambda s: s[s > 0].index.tolist())
)) if not df.empty else ()

col_r1, col_r2 = st.columns([6, 1])
with col_r2:
    if st.button("🔄 Refresh Prices"):
        st.cache_data.clear()
        st.rerun()
with col_r1:
    if open_stocks:
        st.caption(f"📡 Prices cached 5 min · {', '.join(open_stocks)}")

with st.spinner("Fetching market prices..."):
    prices = get_cached_prices(open_stocks) if open_stocks else {}

# ---- CALCULATIONS ----
invested, value, pnl, holdings = compute_portfolio(df, prices=prices)
xirr_val     = compute_xirr(df, prices=prices)
free_cash    = calculate_free_cash(df)
cash_df      = load_cashflows()
units        = calculate_total_units(cash_df)
total_assets = value + free_cash
nav          = calculate_nav(value, free_cash, units)
save_nav_history(nav, total_assets, units)
nav_df       = load_nav_history()

# Auto-save fundamental scores during market windows
save_fundamental_scores(holdings)

# ---- TABS ----
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📈 Dashboard",
    "➕ Add Transaction",
    "📌 Holdings",
    "🧠 Scoring",
    "💰 Funds"
])

# ================================================================
# TAB 1: DASHBOARD
# ================================================================
with tab1:
    st.subheader("📈 Portfolio Overview")

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Invested",      f"₹{invested:,.2f}")
    col2.metric("Current Value", f"₹{value:,.2f}")
    col3.metric("P&L",           f"₹{pnl:,.2f}")
    col4.metric("XIRR",          f"{(xirr_val or 0.0) * 100:.2f}%")
    col5.metric("Free Cash",     f"₹{free_cash:,.2f}")
    col6.metric("NAV",           f"₹{nav:.2f}")

    total_charges_display = float(df["charges"].sum()) if not df.empty else 0.0
    gross_pnl = pnl + total_charges_display
    st.caption(
        f"📊 Gross P&L: ₹{gross_pnl:,.2f}  |  "
        f"Charges: ₹{total_charges_display:,.2f}  |  "
        f"Net P&L (after charges): ₹{pnl:,.2f}"
    )

    st.divider()

    # ---- NAV CHART ----
    st.subheader("📈 NAV History")

    if nav_df is not None and not nav_df.empty:
        range_option = st.radio(
            "Select Time Range",
            ["1M", "3M", "6M", "1Y", "5Y", "YTD"],
            horizontal=True
        )

        today_ts   = pd.Timestamp.today()
        cutoff_map = {
            "1M":  today_ts - pd.DateOffset(months=1),
            "3M":  today_ts - pd.DateOffset(months=3),
            "6M":  today_ts - pd.DateOffset(months=6),
            "1Y":  today_ts - pd.DateOffset(years=1),
            "5Y":  today_ts - pd.DateOffset(years=5),
            "YTD": pd.Timestamp(year=today_ts.year, month=1, day=1),
        }

        nav_filtered = nav_df[nav_df["date"] >= cutoff_map[range_option]].copy()
        nav_filtered = nav_filtered.sort_values("date")
        nav_filtered["date_str"] = nav_filtered["date"].dt.strftime("%d %b '%y")

        nav_chart = px.line(
            nav_filtered, x="date_str", y="nav",
            markers=True, title=f"NAV Growth ({range_option})"
        )
        nav_chart.update_layout(
            xaxis_title="", yaxis_title="NAV (₹)",
            hovermode="x unified",
            xaxis=dict(tickangle=-45, showgrid=False),
            yaxis=dict(showgrid=True),
            plot_bgcolor="rgba(0,0,0,0)",
        )
        nav_chart.update_traces(
            line=dict(width=2), marker=dict(size=6),
            hovertemplate="₹%{y:.2f}<extra></extra>"
        )
        st.plotly_chart(nav_chart, use_container_width=True)
    else:
        st.info("NAV history will appear here after the first day of data.")

    st.divider()

    # ---- ALLOCATION PIE ----
    st.subheader("📊 Allocation")
    if holdings is not None and not holdings.empty:
        fig = px.pie(holdings, values="value", names="stock")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No holdings yet")


# ================================================================
# TAB 2: ADD TRANSACTION
# ================================================================
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
                "date": str(date), "stock": stock,
                "qty": qty, "price": price,
                "type": type_, "charges": float(charges)
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
            "Select row to delete", df["row_index"],
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
                        "date": str(date), "stock": stock_edit,
                        "qty": float(qty), "price": float(price),
                        "type": type_, "charges": float(charges)
                    })
                    st.success("Updated!")
                    st.rerun()


# ================================================================
# TAB 3: HOLDINGS
# ================================================================
with tab3:
    st.subheader("📌 Holdings Breakdown")

    if holdings is not None and not holdings.empty:
        st.dataframe(holdings, use_container_width=True)
    else:
        st.info("No holdings yet")


# ================================================================
# TAB 4: SCORING DASHBOARD (100 POINT SYSTEM)
# ================================================================
with tab4:
    st.subheader("📊 Stock Scoring Dashboard (100 Point System)")

    st.markdown("""
    | Category | Points |
    |---|---|
    | Fundamentals (ROE, Growth, Debt, Margin) | 40 |
    | Valuation (PE, PB, PEG, EV/EBITDA) | 25 |
    | Technical (SMA50, SMA200, RSI, Momentum) | 20 |
    | Macro / Sector | 15 |
    | **Total** | **100** |
    """)

    if holdings is not None and not holdings.empty:
        if st.button("🚀 Run Full Scoring Now"):
            with st.spinner("Running scoring engine... this may take 30–60 seconds"):
                score_df = run_full_scoring(holdings)

            if score_df is None or score_df.empty:
                st.warning("No scoring data returned.")
            else:
                st.success(f"✅ Scored {len(score_df)} stocks")

                st.subheader("🏆 Rankings")
                st.dataframe(score_df, use_container_width=True)

                st.subheader("📈 Score Breakdown")
                breakdown = score_df[["stock", "fundamentals", "valuation", "technical", "macro", "total"]]
                fig_bar = px.bar(
                    breakdown.melt(id_vars="stock", var_name="category", value_name="score"),
                    x="stock", y="score", color="category", barmode="stack",
                    title="Score Breakdown by Category"
                )
                st.plotly_chart(fig_bar, use_container_width=True)
    else:
        st.info("Add holdings first to run the scoring engine.")


# ================================================================
# TAB 5: FUNDS
# ================================================================
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
                "date": str(date), "type": type_,
                "amount": float(amount), "note": note
            })
            st.success("Fund Entry Added!")
            st.rerun()
