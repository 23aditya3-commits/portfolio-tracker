import yfinance as yf
import pandas as pd
import yfinance as yf
from pyxirr import xirr
from datetime import datetime


def get_price(stock):

    try:
        return yf.Ticker(stock + ".NS") \
            .history(period="1d")["Close"].iloc[-1]

    except:
        return 0


def compute_portfolio(df):
    df["qty"] = df["qty"].astype(float)
    df["price"] = df["price"].astype(float)

    invested = (df["qty"] * df["price"]).sum()

    holdings = df.groupby("stock").agg({
        "qty": "sum",
        "price": "mean"
    }).reset_index()

    holdings["cmp"] = holdings["stock"].apply(get_price)
    holdings["value"] = holdings["qty"] * holdings["cmp"]

    total_value = holdings["value"].sum()
    pnl = total_value - invested

    return invested, total_value, pnl, holdings


def compute_xirr(df):
    cashflows = []

    for _, row in df.iterrows():
        amt = -(row["qty"] * row["price"])
        cashflows.append((pd.to_datetime(row["date"]), amt))

    # current value as inflow
    total = sum(df["qty"] * df["price"])
    cashflows.append((datetime.today(), total))

    try:
        return xirr(cashflows)
    except:
        return 0

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
