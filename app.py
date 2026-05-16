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
# SECTION 2: GOOGLE SHEETS - CLIENT & ACCESSORS
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
    return get_client().open(st.secrets["sheets"]["sheet_name"]).worksheet("transactions")

def get_cashflow_sheet():
    return get_client().open(st.secrets["sheets"]["sheet_name"]).worksheet("load_cashflows")

def get_nav_sheet():
    return get_client().open(st.secrets["sheets"]["sheet_name"]).worksheet("nav_history")

def get_score_sheet():
    return get_client().open(st.secrets["sheets"]["sheet_name"]).worksheet("load_score_history")


# ================================================================
# SECTION 3: TRANSACTION CRUD
# ================================================================

def load_transactions():
    data = get_sheet().get_all_records()
    df   = pd.DataFrame(data)
    if df.empty:
        return pd.DataFrame(columns=["date", "stock", "qty", "price", "type", "charges"])
    df.columns     = [str(c).strip().lower() for c in df.columns]
    df             = sanitize_numeric(df, ["qty", "price", "charges"])
    df["type"]     = df["type"].astype(str).str.strip().str.upper()
    df["row_index"] = range(2, len(df) + 2)
    return df

def add_transaction(row):
    get_sheet().append_row([
        row["date"], row["stock"],
        float(row["qty"]), float(row["price"]),
        row["type"], float(row["charges"])
    ])

def delete_transaction(row_index):
    get_sheet().delete_rows(row_index)

def update_transaction(row_index, row):
    get_sheet().update(f"A{row_index}:F{row_index}", [[
        row["date"], row["stock"],
        float(row["qty"]), float(row["price"]),
        row["type"], float(row["charges"])
    ]])


# ================================================================
# SECTION 4: CASHFLOW CRUD
# ================================================================

def load_cashflows():
    data = get_cashflow_sheet().get_all_records()
    df   = pd.DataFrame(data)
    if df.empty:
        return pd.DataFrame(columns=["date", "type", "amount", "note"])
    df.columns = [str(c).strip().lower() for c in df.columns]
    df         = sanitize_numeric(df, ["amount"])
    df["type"] = df["type"].astype(str).str.strip().str.upper()
    return df

def add_cashflow_entry(row):
    get_cashflow_sheet().append_row([
        row["date"], row["type"], float(row["amount"]), row["note"]
    ])


# ================================================================
# SECTION 5: PRICE FETCH (SESSION-CACHED)
# ================================================================

def get_price(stock):
    try:
        val = yf.Ticker(str(stock).strip() + ".NS").history(period="1d")["Close"].iloc[-1]
        return float(pd.to_numeric(val, errors="coerce") or 0.0)
    except Exception:
        return 0.0

def fetch_all_prices(stocks):
    if not stocks:
        return {}
    try:
        symbols = [s.strip() + ".NS" for s in stocks]
        raw     = yf.download(symbols, period="1d", auto_adjust=True, progress=False)["Close"]
        prices  = {}
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
        return {s: get_price(s) for s in stocks}

@st.cache_data(ttl=300, show_spinner=False)
def get_cached_prices(stocks_tuple):
    """Cache ONLY market prices for 5 min. Sheet data is never cached."""
    return fetch_all_prices(list(stocks_tuple))


# ================================================================
# SECTION 6: PORTFOLIO CALCULATIONS
# ================================================================

def compute_portfolio(df, prices=None):
    if df.empty:
        return 0.0, 0.0, 0.0, pd.DataFrame()
    df           = df.copy()
    df           = sanitize_numeric(df, ["qty", "price", "charges"])
    df["type"]   = df["type"].astype(str).str.strip().str.upper()
    df["amount"] = df["qty"] * df["price"]

    multiplier       = df["type"].map(lambda t: 1.0 if t == "BUY" else -1.0)
    df["signed_qty"] = df["qty"] * multiplier

    holdings = df.groupby("stock").agg({"signed_qty": "sum"}).reset_index()
    holdings.columns = ["stock", "qty"]
    holdings["qty"]  = pd.to_numeric(holdings["qty"], errors="coerce").fillna(0.0).astype("float64")
    holdings         = holdings[holdings["qty"] > 0].copy()

    if holdings.empty:
        buy_cost      = float(df.loc[df["type"] == "BUY",  "amount"].sum())
        sell_proceeds = float(df.loc[df["type"] == "SELL", "amount"].sum())
        total_charges = float(df["charges"].sum())
        return 0.0, 0.0, sell_proceeds - buy_cost - total_charges, pd.DataFrame()

    buys     = df[df["type"] == "BUY"].copy()
    avg_cost = (
        buys.groupby("stock")
        .apply(lambda x: (x["qty"] * x["price"]).sum() / x["qty"].sum())
        .reset_index()
    )
    avg_cost.columns      = ["stock", "avg_price"]
    holdings              = holdings.merge(avg_cost, on="stock", how="left")
    holdings["avg_price"] = pd.to_numeric(holdings["avg_price"], errors="coerce").fillna(0.0)
    holdings["invested"]  = holdings["qty"] * holdings["avg_price"]

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
    sold_cost     = 0.0
    for stock, grp in sell_df.groupby("stock"):
        avg_row = avg_cost[avg_cost["stock"] == stock]
        if not avg_row.empty:
            sold_cost += float(grp["qty"].sum()) * float(avg_row["avg_price"].iloc[0])

    realised_pnl  = sell_proceeds - sold_cost
    total_charges = float(df["charges"].sum())
    pnl           = unrealised_pnl + realised_pnl - total_charges
    holdings["pnl"] = (holdings["value"] - holdings["invested"]).round(2)
    return invested, total_value, pnl, holdings


def compute_xirr(df, prices=None):
    if df.empty:
        return 0.0
    df         = df.copy()
    df         = sanitize_numeric(df, ["qty", "price", "charges"])
    df["type"] = df["type"].astype(str).str.strip().str.upper()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df         = df.dropna(subset=["date"])
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

    multiplier       = df["type"].map(lambda t: 1.0 if t == "BUY" else -1.0)
    df["signed_qty"] = df["qty"] * multiplier
    open_h           = df.groupby("stock")["signed_qty"].sum()
    open_h           = open_h[open_h > 0]

    terminal = sum(
        float(qty) * (float(prices.get(str(stock), 0.0)) if prices else get_price(str(stock)))
        for stock, qty in open_h.items()
    )
    if terminal > 0:
        cashflows.append((datetime.today(), float(terminal)))
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
            {"label": item.get("symbol", ""), "symbol": item.get("symbol", "").replace(".NS", "")}
            for item in results if item.get("symbol", "").endswith(".NS")
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
    df           = df.copy()
    df           = sanitize_numeric(df, ["qty", "price", "charges"])
    df["type"]   = df["type"].astype(str).str.strip().str.upper()
    df["amount"] = df["qty"] * df["price"]
    buy_spent     = float(df.loc[df["type"] == "BUY",  "amount"].sum())
    sell_received = float(df.loc[df["type"] == "SELL", "amount"].sum())
    charges_total = float(df["charges"].sum())
    return round(max(total_cash - buy_spent - charges_total + sell_received, 0.0), 2)

def check_free_cash_before_buy(df, new_date, qty, price):
    cash_df = load_cashflows()
    if cash_df.empty:
        return False
    total_cash   = float(cash_df["amount"].sum())
    df           = df.copy()
    df           = sanitize_numeric(df, ["qty", "price", "charges"])
    df["type"]   = df["type"].astype(str).str.strip().str.upper()
    df["date"]   = pd.to_datetime(df["date"], errors="coerce")
    past         = df[df["date"] <= pd.to_datetime(new_date)].copy()
    past["amount"] = past["qty"] * past["price"]
    buy_spent     = float(past.loc[past["type"] == "BUY",  "amount"].sum())
    sell_received = float(past.loc[past["type"] == "SELL", "amount"].sum())
    charges_total = float(past["charges"].sum())
    available     = total_cash - buy_spent - charges_total + sell_received
    return available >= float(qty) * float(price)


# ================================================================
# SECTION 8: NAV SYSTEM
# ================================================================

def calculate_total_units(cash_df):
    if cash_df.empty:
        return 0.0
    credit   = float(cash_df.loc[cash_df["type"] == "CREDIT", "amount"].sum())
    debit    = float(cash_df.loc[cash_df["type"] == "DEBIT",  "amount"].sum())
    net_cash = credit - debit
    return round(net_cash / 10, 4) if net_cash > 0 else 0.0

def calculate_nav(total_value, free_cash, units):
    total_assets = float(total_value) + float(free_cash)
    return round(total_assets / units, 2) if units > 0 else 10.0

def save_nav_history(nav, total_assets, units):
    try:
        sheet = get_nav_sheet()
        today = str(datetime.today().date())
        data  = sheet.get_all_records()
        dates = [str(x.get("date")) for x in data]
        row   = [today, float(nav), float(total_assets), float(units)]
        if today in dates:
            idx = dates.index(today)
            sheet.update(f"A{idx+2}:D{idx+2}", [row])
        else:
            sheet.append_row(row)
    except Exception:
        pass

def load_nav_history():
    try:
        data = get_nav_sheet().get_all_records()
        df   = pd.DataFrame(data)
        if df.empty:
            return pd.DataFrame(columns=["date", "nav", "portfolio_value", "units"])
        df.columns = [str(c).strip().lower() for c in df.columns]
        df         = sanitize_numeric(df, ["nav", "portfolio_value", "units"])
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        return df
    except Exception:
        return pd.DataFrame(columns=["date", "nav", "portfolio_value", "units"])


# ================================================================
# SECTION 9: SCORING ENGINE - 100 POINT SYSTEM
#
#  Fundamentals  40 pts   ROE, Revenue Growth, Profit Growth, D/E, Op Margin
#  Valuation     25 pts   PE, PB, PEG, EV/EBITDA
#  Technical     20 pts   SMA50, SMA200, RSI, 1M Momentum
#  Macro         15 pts   Sector quality + Beta
# ================================================================

def _score_fundamentals(info):
    roe    = float(info.get("returnOnEquity")   or 0) * 100
    rev_g  = float(info.get("revenueGrowth")    or 0) * 100
    prof_g = float(info.get("earningsGrowth")   or 0) * 100
    de     = float(info.get("debtToEquity")     or 0)
    margin = float(info.get("operatingMargins") or 0) * 100

    score = 0
    if   roe > 25:    score += 10
    elif roe > 15:    score += 7
    elif roe > 8:     score += 4

    if   rev_g > 20:  score += 8
    elif rev_g > 10:  score += 5
    elif rev_g > 5:   score += 2

    if   prof_g > 20: score += 8
    elif prof_g > 10: score += 5
    elif prof_g > 5:  score += 2

    if   de < 0.3:    score += 8
    elif de < 1.0:    score += 5
    elif de < 2.0:    score += 2

    if   margin > 25: score += 6
    elif margin > 15: score += 4
    elif margin > 8:  score += 2

    return min(score, 40), {
        "roe_pct":        round(roe,    2),
        "rev_growth_pct": round(rev_g,  2),
        "prof_growth_pct":round(prof_g, 2),
        "debt_equity":    round(de,     2),
        "op_margin_pct":  round(margin, 2),
    }


def _score_valuation(info):
    pe  = float(info.get("trailingPE")         or 0)
    pb  = float(info.get("priceToBook")        or 0)
    peg = float(info.get("pegRatio")           or 0)
    ev  = float(info.get("enterpriseToEbitda") or 0)

    score = 0
    if   0 < pe  < 15:  score += 10
    elif 0 < pe  < 25:  score += 6
    elif 0 < pe  < 35:  score += 3

    if   0 < pb  < 1.5: score += 5
    elif 0 < pb  < 3:   score += 3
    elif 0 < pb  < 5:   score += 1

    if   0 < peg < 1:   score += 5
    elif 0 < peg < 1.5: score += 3
    elif 0 < peg < 2:   score += 1

    if   0 < ev  < 8:   score += 5
    elif 0 < ev  < 15:  score += 3
    elif 0 < ev  < 20:  score += 1

    return min(score, 25), {
        "pe":        round(pe,  2),
        "pb":        round(pb,  2),
        "peg":       round(peg, 2),
        "ev_ebitda": round(ev,  2),
    }


def _calc_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss  = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    val   = rsi.iloc[-1]
    return float(val) if not np.isnan(float(val)) else 50.0


def _score_technical(ticker):
    try:
        hist = ticker.history(period="1y")
        if hist.empty or len(hist) < 50:
            return 0, {"sma50": 0.0, "sma200": 0.0, "rsi": 50.0, "momentum_1m_pct": 0.0}

        close  = hist["Close"]
        price  = float(close.iloc[-1])
        sma50  = float(close.rolling(50).mean().iloc[-1])
        sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else sma50
        rsi    = _calc_rsi(close)
        p1m    = float(close.iloc[-21]) if len(close) >= 21 else price
        mom    = ((price - p1m) / p1m * 100) if p1m > 0 else 0.0

        score = 0
        if   price > sma50 * 1.02:  score += 5
        elif price > sma50:          score += 3

        if   price > sma200 * 1.02: score += 5
        elif price > sma200:         score += 3

        if   40 <= rsi <= 65: score += 5
        elif 30 <= rsi < 40:  score += 3
        elif 65 < rsi <= 75:  score += 2

        if   mom > 5: score += 5
        elif mom > 2: score += 3
        elif mom > 0: score += 1

        return min(score, 20), {
            "sma50":           round(sma50,  2),
            "sma200":          round(sma200, 2),
            "rsi":             round(rsi,    2),
            "momentum_1m_pct": round(mom,    2),
        }
    except Exception:
        return 0, {"sma50": 0.0, "sma200": 0.0, "rsi": 50.0, "momentum_1m_pct": 0.0}


_SECTOR_SCORES = {
    "HDFCBANK": 13, "ICICIBANK": 13, "KOTAKBANK": 12, "AXISBANK": 11,
    "BAJFINANCE": 12, "SBICARD": 10,
    "INFY": 12, "TCS": 12, "WIPRO": 10, "HCLTECH": 11, "TECHM": 10,
    "NESTLEIND": 12, "HINDUNILVR": 12, "ASIANPAINT": 11, "TITAN": 11,
    "SUNPHARMA": 11, "DRREDDY": 11, "CIPLA": 10,
    "MARUTI": 10, "BAJAJ-AUTO": 10, "HEROMOTOCO": 10, "TATAMOTORS": 9,
    "RELIANCE": 11, "ONGC": 8, "NTPC": 8, "POWERGRID": 8,
    "TATASTEEL": 8, "HINDALCO": 8, "JSWSTEEL": 8,
    "IONEXCHANG": 11, "TATAGOLD": 9, "ASHOKLEY": 9,
}

def _score_macro(stock, info):
    base  = 8
    s     = stock.upper()
    for key, val in _SECTOR_SCORES.items():
        if key in s:
            base = val
            break
    beta  = float(info.get("beta") or 1.0)
    bonus = 2 if 0.5 <= beta <= 1.2 else (1 if beta < 0.5 else 0)
    return min(base + bonus, 15)


def run_full_scoring(holdings):
    if holdings is None or holdings.empty:
        return pd.DataFrame()

    stocks   = holdings["stock"].unique().tolist()
    results  = []
    progress = st.progress(0, text="Starting...")

    for i, stock in enumerate(stocks):
        pct  = (i + 1) / len(stocks)
        progress.progress(pct, text=f"Scoring {stock} ({i+1}/{len(stocks)})...")
        try:
            ticker  = yf.Ticker(stock + ".NS")
            info    = ticker.info

            f_score, f_detail = _score_fundamentals(info)
            v_score, v_detail = _score_valuation(info)
            t_score, t_detail = _score_technical(ticker)
            m_score           = _score_macro(stock, info)
            total             = f_score + v_score + t_score + m_score

            row = {
                "stock":        stock,
                "total":        total,
                "fundamentals": f_score,
                "valuation":    v_score,
                "technical":    t_score,
                "macro":        m_score,
            }
            row.update(f_detail)
            row.update(v_detail)
            row.update(t_detail)
            results.append(row)

        except Exception:
            results.append({
                "stock": stock, "total": 0,
                "fundamentals": 0, "valuation": 0,
                "technical": 0, "macro": 0,
            })

    progress.empty()
    return pd.DataFrame(results).sort_values("total", ascending=False).reset_index(drop=True)


def load_score_history():
    try:
        data = get_score_sheet().get_all_records()
        df   = pd.DataFrame(data)
        if df.empty:
            return pd.DataFrame(columns=[
                "date", "stock", "fundamentals", "valuation", "technical", "macro", "total"
            ])
        df.columns = [str(c).strip().lower() for c in df.columns]
        df         = sanitize_numeric(df, ["fundamentals", "valuation", "technical", "macro", "total"])
        return df
    except Exception:
        return pd.DataFrame(columns=[
            "date", "stock", "fundamentals", "valuation", "technical", "macro", "total"
        ])


def is_eod_window():
    now = datetime.now().time()
    return time(15, 0) <= now <= time(15, 30)


def save_scores_to_sheet(score_df):
    if score_df is None or score_df.empty:
        return
    try:
        sheet = get_score_sheet()
        today = str(datetime.today().date())
        data  = sheet.get_all_records()

        today_rows = [
            i + 2 for i, row in enumerate(data)
            if str(row.get("date", "")).strip() == today
        ]
        for r in sorted(today_rows, reverse=True):
            sheet.delete_rows(r)

        for _, row in score_df.iterrows():
            sheet.append_row([
                today,
                str(row.get("stock", "")),
                int(row.get("fundamentals", 0)),
                int(row.get("valuation",    0)),
                int(row.get("technical",    0)),
                int(row.get("macro",        0)),
                int(row.get("total",        0)),
            ])
    except Exception:
        pass


# ================================================================
# SECTION 10: STREAMLIT APP
# ================================================================

st.set_page_config(page_title="Portfolio Tracker", layout="wide")
st.title("My Mutual Fund Tracker")

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

# ---- PRICE FETCH (cached) ----
open_stocks = tuple(sorted(
    df.groupby("stock").apply(
        lambda x: (x["qty"] * x["type"].map(lambda t: 1.0 if t == "BUY" else -1.0)).sum()
    ).pipe(lambda s: s[s > 0].index.tolist())
)) if not df.empty else ()

col_r1, col_r2 = st.columns([6, 1])
with col_r2:
    if st.button("Refresh Prices"):
        get_cached_prices.clear()
        st.rerun()
with col_r1:
    if open_stocks:
        st.caption(f"Prices cached 5 min · {', '.join(open_stocks)}")

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

# ---- TABS ----
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "Dashboard",
    "Add Transaction",
    "Holdings",
    "Scoring",
    "Funds"
])


# ================================================================
# TAB 1: DASHBOARD
# ================================================================
with tab1:
    st.subheader("Portfolio Overview")

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Invested",      f"Rs.{invested:,.2f}")
    col2.metric("Current Value", f"Rs.{value:,.2f}")
    col3.metric("P&L",           f"Rs.{pnl:,.2f}")
    col4.metric("XIRR",          f"{(xirr_val or 0.0) * 100:.2f}%")
    col5.metric("Free Cash",     f"Rs.{free_cash:,.2f}")
    col6.metric("NAV",           f"Rs.{nav:.2f}")

    total_charges_display = float(df["charges"].sum()) if not df.empty else 0.0
    gross_pnl = pnl + total_charges_display
    st.caption(
        f"Gross P&L: Rs.{gross_pnl:,.2f}  |  "
        f"Charges: Rs.{total_charges_display:,.2f}  |  "
        f"Net P&L (after charges): Rs.{pnl:,.2f}"
    )

    st.divider()
    st.subheader("NAV History")

    if nav_df is not None and not nav_df.empty:
        range_option = st.radio(
            "Select Time Range", ["1M", "3M", "6M", "1Y", "5Y", "YTD"], horizontal=True
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

        nav_chart = px.line(nav_filtered, x="date_str", y="nav",
                            markers=True, title=f"NAV Growth ({range_option})")
        nav_chart.update_layout(
            xaxis_title="", yaxis_title="NAV (Rs.)", hovermode="x unified",
            xaxis=dict(tickangle=-45, showgrid=False),
            yaxis=dict(showgrid=True), plot_bgcolor="rgba(0,0,0,0)",
        )
        nav_chart.update_traces(line=dict(width=2), marker=dict(size=6),
                                hovertemplate="Rs.%{y:.2f}<extra></extra>")
        st.plotly_chart(nav_chart, use_container_width=True)
    else:
        st.info("NAV history will appear after the first day of data.")

    st.divider()
    st.subheader("Allocation")
    if holdings is not None and not holdings.empty:
        fig = px.pie(holdings, values="value", names="stock")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No holdings yet")


# ================================================================
# TAB 2: ADD TRANSACTION
# ================================================================
with tab2:
    st.subheader("Add Transaction")

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
            qty, price = float(qty), float(price)
            if type_ == "BUY" and not check_free_cash_before_buy(df, date, qty, price):
                st.error("Insufficient Free Cash!")
                st.stop()
            add_transaction({
                "date": str(date), "stock": stock, "qty": qty,
                "price": price, "type": type_, "charges": float(charges)
            })
            st.success("Transaction Added!")
            st.rerun()

    st.divider()
    cutoff      = pd.Timestamp.today() - pd.DateOffset(months=3)
    df_filtered = df[df["date"] >= cutoff] if "date" in df.columns else df
    with st.expander("Existing Transactions (Last 3 Months)", expanded=False):
        st.dataframe(df_filtered, use_container_width=True)

    st.divider()
    with st.expander("Edit / Delete Transactions", expanded=False):
        st.subheader("Delete Transaction")
        del_row = st.selectbox("Select row to delete", df["row_index"],
                               format_func=lambda x: f"Row {x}")
        if st.button("Delete Transaction"):
            delete_transaction(del_row)
            st.success("Deleted!")
            st.rerun()

        st.divider()
        st.subheader("Edit Transaction")
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
                        "date": str(date), "stock": stock_edit, "qty": float(qty),
                        "price": float(price), "type": type_, "charges": float(charges)
                    })
                    st.success("Updated!")
                    st.rerun()


# ================================================================
# TAB 3: HOLDINGS
# ================================================================
with tab3:
    st.subheader("Holdings Breakdown")
    if holdings is not None and not holdings.empty:
        st.dataframe(holdings, use_container_width=True)
    else:
        st.info("No holdings yet")


# ================================================================
# TAB 4: SCORING DASHBOARD - 100 POINT SYSTEM
# ================================================================
with tab4:
    st.subheader("Stock Scoring Dashboard - 100 Point System")

    st.markdown("""
| Category | Max | Metrics |
|---|---|---|
| Fundamentals | 40 | ROE, Revenue Growth, Profit Growth, D/E Ratio, Operating Margin |
| Valuation | 25 | PE, PB, PEG, EV/EBITDA |
| Technical | 20 | SMA50, SMA200, RSI, 1-Month Momentum |
| Macro | 15 | Sector Quality + Beta |
| Total | 100 | |
""")

    st.divider()

    now = datetime.now()
    if is_eod_window():
        st.success("EOD window active (3:00 - 3:30 PM) - scores will auto-save after running.")
    else:
        label = "3:00 PM today" if now.hour < 15 else "3:00 PM tomorrow"
        st.info(f"Auto-save runs at EOD (3:00-3:30 PM IST). Next window: {label}")

    st.divider()

    if holdings is not None and not holdings.empty:
        if st.button("Run Scoring Now"):
            with st.spinner("Fetching data and scoring... (~30-60 sec)"):
                score_df = run_full_scoring(holdings)

            if score_df is not None and not score_df.empty:
                st.session_state["score_df"]   = score_df
                st.session_state["score_date"] = datetime.now().strftime("%d %b %Y %I:%M %p")
                if is_eod_window():
                    save_scores_to_sheet(score_df)
                    st.success("Scores saved to Google Sheet (EOD).")
            else:
                st.warning("No data returned. Check yfinance connectivity.")

        if "score_df" in st.session_state:
            score_df   = st.session_state["score_df"]
            score_date = st.session_state.get("score_date", "")
            st.caption(f"Last scored: {score_date}")

            summary_cols = ["stock", "total", "fundamentals", "valuation", "technical", "macro"]
            st.subheader("Rankings")
            st.dataframe(
                score_df[summary_cols].style.background_gradient(subset=["total"], cmap="RdYlGn"),
                use_container_width=True
            )

            st.subheader("Score Breakdown")
            bar_df = score_df[["stock", "fundamentals", "valuation", "technical", "macro"]].melt(
                id_vars="stock", var_name="category", value_name="score"
            )
            fig_bar = px.bar(
                bar_df, x="stock", y="score", color="category", barmode="stack",
                color_discrete_map={
                    "fundamentals": "#2196F3",
                    "valuation":    "#4CAF50",
                    "technical":    "#FF9800",
                    "macro":        "#9C27B0",
                },
                title="Score Breakdown by Category"
            )
            fig_bar.update_layout(
                xaxis_title="", yaxis_title="Score",
                plot_bgcolor="rgba(0,0,0,0)"
            )
            st.plotly_chart(fig_bar, use_container_width=True)

            with st.expander("Full Detail (all metrics)", expanded=False):
                st.dataframe(score_df, use_container_width=True)
    else:
        st.info("No holdings found. Add transactions first.")

    st.divider()
    st.subheader("Score History")
    hist_df = load_score_history()

    if hist_df is not None and not hist_df.empty:
        latest = (
            hist_df.sort_values("date")
            .groupby("stock")
            .tail(1)
            .reset_index(drop=True)
        )
        st.dataframe(latest, use_container_width=True)

        if hist_df["date"].nunique() > 1:
            st.subheader("Score Trend Over Time")
            hist_df["date"] = pd.to_datetime(hist_df["date"], errors="coerce")
            trend_fig = px.line(
                hist_df.sort_values("date"),
                x="date", y="total", color="stock",
                markers=True, title="Total Score Trend"
            )
            trend_fig.update_layout(xaxis_title="", yaxis_title="Total Score")
            st.plotly_chart(trend_fig, use_container_width=True)
    else:
        st.info("No history yet. Scores save automatically at 3:00-3:30 PM EOD.")


# ================================================================
# TAB 5: FUNDS
# ================================================================
with tab5:
    st.subheader("Funds Management")

    cf = load_cashflows()
    st.write("### Cashflow History")
    st.dataframe(cf, use_container_width=True)

    st.divider()
    st.subheader("Add Funds")

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
