import yfinance as yf
import pandas as pd
from pyxirr import xirr
from datetime import datetime


# ---------------- PRICE FETCH ----------------
def get_price(stock):
    try:
        return yf.Ticker(stock + ".NS").history(period="1d")["Close"].iloc[-1]
    except:
        return 0


# ---------------- PORTFOLIO ----------------
def compute_portfolio(df):

    if df.empty:
        return 0, 0, 0, pd.DataFrame()

    df = df.copy()

    # ✅ HARD FIX TYPE ISSUES
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
    df["price"] = pd.to_numeric(df["price"], errors="coerce").fillna(0)

    df["signed_qty"] = df.apply(
        lambda x: x["qty"] if x["type"] == "BUY" else -x["qty"],
        axis=1
    )

    # BUY invested only
    buy_df = df[df["type"] == "BUY"]
    invested = (buy_df["qty"] * buy_df["price"]).sum()

    holdings = df.groupby("stock").agg({
        "signed_qty": "sum"
    }).reset_index()

    holdings.columns = ["stock", "qty"]
    holdings = holdings[holdings["qty"] > 0]

    holdings["cmp"] = holdings["stock"].apply(get_price)
    holdings["value"] = holdings["qty"] * holdings["cmp"]

    total_value = holdings["value"].sum()
    pnl = total_value - invested

    return invested, total_value, pnl, holdings


# ---------------- XIRR ----------------
def compute_xirr(df):

    if df.empty:
        return 0

    df = df.copy()

    df["date"] = pd.to_datetime(df["date"])
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
    df["price"] = pd.to_numeric(df["price"], errors="coerce").fillna(0)
    df["charges"] = pd.to_numeric(df.get("charges", 0), errors="coerce").fillna(0)

    cashflows = []

    for _, row in df.iterrows():

        amount = row["qty"] * row["price"]

        if row["type"] == "BUY":
            amount = -(amount + row["charges"])
        else:
            amount = amount - row["charges"]

        cashflows.append((row["date"], amount))

    total_value = 0

    holdings = df.groupby("stock")["qty"].sum().reset_index()

    for _, row in holdings.iterrows():
        if row["qty"] > 0:
            total_value += row["qty"] * get_price(row["stock"])

    cashflows.append((datetime.today(), total_value))

    try:
        return xirr(cashflows)
    except:
        return 0


# ---------------- STOCK SEARCH ----------------
def search_stocks(query):

    if not query:
        return []

    try:
        results = yf.Search(query).quotes

        stocks = []

        for item in results:
            symbol = item.get("symbol", "")

            if symbol.endswith(".NS"):
                stocks.append({
                    "label": symbol,
                    "symbol": symbol.replace(".NS", "")
                })

        return stocks

    except:
        return []


# ---------------- FREE CASH ----------------
def calculate_free_cash(df, monthly_addition=3000):

    if df.empty:
        return 0

    df = df.copy()

    df["date"] = pd.to_datetime(df["date"])
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
    df["price"] = pd.to_numeric(df["price"], errors="coerce").fillna(0)
    df["charges"] = pd.to_numeric(df.get("charges", 0), errors="coerce").fillna(0)

    start_date = df["date"].min()
    today = datetime.today()

    months = (
        (today.year - start_date.year) * 12 +
        (today.month - start_date.month) + 1
    )

    total_added = months * monthly_addition

    buy_spent = (df[df["type"] == "BUY"]["qty"] * df[df["type"] == "BUY"]["price"]).sum()
    sell_received = (df[df["type"] == "SELL"]["qty"] * df[df["type"] == "SELL"]["price"]).sum()
    total_charges = df["charges"].sum()

    free_cash = total_added - buy_spent - total_charges + sell_received

    return round(max(free_cash, 0), 2)


# ---------------- VALIDATION ----------------
def check_free_cash_before_buy(df, new_date, qty, price, monthly_addition=3000):

    df = df.copy()

    if df.empty:
        return False

    df["date"] = pd.to_datetime(df["date"])
    new_date = pd.to_datetime(new_date)

    past = df[df["date"] <= new_date]

    if past.empty:
        return False

    start_date = past["date"].min()

    months = (
        (new_date.year - start_date.year) * 12 +
        (new_date.month - start_date.month) + 1
    )

    total_cash = months * monthly_addition

    buy_spent = (past[past["type"] == "BUY"]["qty"] * past[past["type"] == "BUY"]["price"]).sum()
    sell_received = (past[past["type"] == "SELL"]["qty"] * past[past["type"] == "SELL"]["price"]).sum()
    charges = past["charges"].sum()

    available_cash = total_cash - buy_spent - charges + sell_received

    return available_cash >= (qty * price)
