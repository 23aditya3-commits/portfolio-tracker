import yfinance as yf
import pandas as pd
from pyxirr import xirr
from datetime import datetime

from sheets import load_cashflows   # ✅ IMPORTANT FIX


# ---------------- PRICE FETCH ----------------

def get_price(stock):

    try:
        return yf.Ticker(stock + ".NS") \
            .history(period="1d")["Close"].iloc[-1]
    except:
        return 0


# ---------------- PORTFOLIO CALC ----------------

def compute_portfolio(df):

    if df.empty:
        return 0, 0, 0, pd.DataFrame()

    df = df.copy()

    df["qty"] = df["qty"].astype(float)
    df["price"] = df["price"].astype(float)

    df["signed_qty"] = df.apply(
        lambda x: x["qty"] if x["type"] == "BUY" else -x["qty"],
        axis=1
    )

    invested = (
        df[df["type"] == "BUY"]["qty"] *
        df[df["type"] == "BUY"]["price"]
    ).sum()

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
    df["qty"] = df["qty"].astype(float)
    df["price"] = df["price"].astype(float)
    df["charges"] = df.get("charges", 0).astype(float)

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


# ---------------- REAL CASH SYSTEM (FIXED CORE) ----------------

def calculate_free_cash(df):

    cash_df = load_cashflows()

    if cash_df.empty:
        return 0

    cash_df = cash_df.copy()
    cash_df["amount"] = cash_df["amount"].astype(float)

    total_cash = cash_df["amount"].sum()

    if df.empty:
        return round(total_cash, 2)

    df = df.copy()
    df["qty"] = df["qty"].astype(float)
    df["price"] = df["price"].astype(float)
    df["charges"] = df.get("charges", 0).astype(float)

    buy_spent = (
        df[df["type"] == "BUY"]["qty"] *
        df[df["type"] == "BUY"]["price"]
    ).sum()

    sell_received = (
        df[df["type"] == "SELL"]["qty"] *
        df[df["type"] == "SELL"]["price"]
    ).sum()

    available = total_cash - buy_spent - df["charges"].sum() + sell_received

    return round(max(available, 0), 2)


# ---------------- CASH VALIDATION (FINAL FIX) ----------------

def check_free_cash_before_buy(df, new_date, qty, price):

    cash_df = load_cashflows()

    if cash_df.empty:
        return False   # no money in system → block BUY

    cash_df = cash_df.copy()
    cash_df["amount"] = cash_df["amount"].astype(float)

    total_cash = cash_df["amount"].sum()

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])

    past = df[df["date"] <= pd.to_datetime(new_date)]

    buy_spent = (
        past[past["type"] == "BUY"]["qty"] *
        past[past["type"] == "BUY"]["price"]
    ).sum()

    sell_received = (
        past[past["type"] == "SELL"]["qty"] *
        past[past["type"] == "SELL"]["price"]
    ).sum()

    charges = past["charges"].sum()

    available = total_cash - buy_spent - charges + sell_received

    return available >= (qty * price)
