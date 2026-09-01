import streamlit as st
import pandas as pd
import plotly.express as px
import gspread
import yfinance as yf
from pyxirr import xirr
from datetime import datetime, time
from oauth2client.service_account import ServiceAccountCredentials
import numpy as np
import re
import requests
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import warnings
warnings.filterwarnings('ignore')

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


def get_recommendation_sheet():
    """Get or create recommendation history sheet"""
    client = get_client()
    sheet_name = st.secrets["sheets"]["sheet_name"]
    try:
        return client.open(sheet_name).worksheet("recommendations")
    except:
        # Create the sheet if it doesn't exist
        sheet = client.open(sheet_name).add_worksheet(
            title="recommendations",
            rows=1000,
            cols=20
        )
        sheet.append_row([
            "date", "stock", "current_price", "avg_price", "qty",
            "returns_pct", "health_score", "sentiment_score",
            "recommendation", "reason", "sector", "pe_ratio", "roe"
        ])
        return sheet

# ================================================================
# SECTION 3: SECTOR BENCHMARKS & MAPPING
# ================================================================

SECTOR_BENCHMARKS = {
    'BANKING': {
        'pe_ratio': {'min': 5, 'max': 15, 'ideal': 10},
        'pb_ratio': {'min': 0.8, 'max': 2.5, 'ideal': 1.5},
        'roe': {'min': 12, 'max': 25, 'ideal': 18},
        'debt_to_equity': {'min': 0, 'max': 10, 'ideal': 5},
        'profit_margin': {'min': 20, 'max': 40, 'ideal': 30},
        'revenue_growth': {'min': 8, 'max': 20, 'ideal': 14},
        'earnings_growth': {'min': 10, 'max': 25, 'ideal': 18},
        'current_ratio': {'min': 1, 'max': 2, 'ideal': 1.5}
    },
    'FMCG': {
        'pe_ratio': {'min': 20, 'max': 50, 'ideal': 35},
        'pb_ratio': {'min': 5, 'max': 15, 'ideal': 10},
        'roe': {'min': 15, 'max': 35, 'ideal': 25},
        'debt_to_equity': {'min': 0, 'max': 1, 'ideal': 0.3},
        'profit_margin': {'min': 10, 'max': 25, 'ideal': 18},
        'revenue_growth': {'min': 8, 'max': 20, 'ideal': 14},
        'earnings_growth': {'min': 10, 'max': 22, 'ideal': 16},
        'current_ratio': {'min': 1.5, 'max': 3, 'ideal': 2}
    },
    'TECHNOLOGY': {
        'pe_ratio': {'min': 15, 'max': 40, 'ideal': 25},
        'pb_ratio': {'min': 3, 'max': 10, 'ideal': 6},
        'roe': {'min': 15, 'max': 30, 'ideal': 22},
        'debt_to_equity': {'min': 0, 'max': 1, 'ideal': 0.2},
        'profit_margin': {'min': 15, 'max': 35, 'ideal': 25},
        'revenue_growth': {'min': 10, 'max': 30, 'ideal': 20},
        'earnings_growth': {'min': 12, 'max': 32, 'ideal': 22},
        'current_ratio': {'min': 1.5, 'max': 3, 'ideal': 2.2}
    },
    'AUTO': {
        'pe_ratio': {'min': 10, 'max': 25, 'ideal': 17},
        'pb_ratio': {'min': 1.5, 'max': 4, 'ideal': 2.5},
        'roe': {'min': 10, 'max': 25, 'ideal': 18},
        'debt_to_equity': {'min': 0, 'max': 1.5, 'ideal': 0.5},
        'profit_margin': {'min': 5, 'max': 15, 'ideal': 10},
        'revenue_growth': {'min': 5, 'max': 20, 'ideal': 12},
        'earnings_growth': {'min': 6, 'max': 22, 'ideal': 14},
        'current_ratio': {'min': 1, 'max': 2, 'ideal': 1.5}
    },
    'PHARMA': {
        'pe_ratio': {'min': 15, 'max': 35, 'ideal': 25},
        'pb_ratio': {'min': 2, 'max': 6, 'ideal': 4},
        'roe': {'min': 12, 'max': 28, 'ideal': 20},
        'debt_to_equity': {'min': 0, 'max': 1, 'ideal': 0.3},
        'profit_margin': {'min': 10, 'max': 25, 'ideal': 18},
        'revenue_growth': {'min': 8, 'max': 25, 'ideal': 16},
        'earnings_growth': {'min': 10, 'max': 28, 'ideal': 18},
        'current_ratio': {'min': 1.5, 'max': 3, 'ideal': 2}
    },
    'OIL_GAS': {
        'pe_ratio': {'min': 5, 'max': 20, 'ideal': 12},
        'pb_ratio': {'min': 0.5, 'max': 2, 'ideal': 1.2},
        'roe': {'min': 8, 'max': 20, 'ideal': 14},
        'debt_to_equity': {'min': 0, 'max': 2, 'ideal': 0.8},
        'profit_margin': {'min': 5, 'max': 15, 'ideal': 10},
        'revenue_growth': {'min': 3, 'max': 15, 'ideal': 8},
        'earnings_growth': {'min': 4, 'max': 16, 'ideal': 10},
        'current_ratio': {'min': 1, 'max': 2, 'ideal': 1.4}
    },
    'METALS': {
        'pe_ratio': {'min': 5, 'max': 15, 'ideal': 10},
        'pb_ratio': {'min': 0.5, 'max': 2, 'ideal': 1.2},
        'roe': {'min': 8, 'max': 20, 'ideal': 14},
        'debt_to_equity': {'min': 0, 'max': 1.5, 'ideal': 0.6},
        'profit_margin': {'min': 5, 'max': 15, 'ideal': 10},
        'revenue_growth': {'min': 3, 'max': 15, 'ideal': 8},
        'earnings_growth': {'min': 4, 'max': 16, 'ideal': 10},
        'current_ratio': {'min': 1, 'max': 2, 'ideal': 1.4}
    },
    'DEFAULT': {
        'pe_ratio': {'min': 10, 'max': 30, 'ideal': 20},
        'pb_ratio': {'min': 1, 'max': 5, 'ideal': 3},
        'roe': {'min': 10, 'max': 25, 'ideal': 18},
        'debt_to_equity': {'min': 0, 'max': 2, 'ideal': 0.8},
        'profit_margin': {'min': 5, 'max': 20, 'ideal': 12},
        'revenue_growth': {'min': 5, 'max': 20, 'ideal': 12},
        'earnings_growth': {'min': 6, 'max': 22, 'ideal': 14},
        'current_ratio': {'min': 1, 'max': 2.5, 'ideal': 1.8}
    }
}

# Map stock symbols to sectors (expand as needed)
SYMBOL_SECTOR_MAP = {
    'RELIANCE': 'OIL_GAS',
    'TCS': 'TECHNOLOGY',
    'INFY': 'TECHNOLOGY',
    'HDFCBANK': 'BANKING',
    'ICICIBANK': 'BANKING',
    'KOTAKBANK': 'BANKING',
    'AXISBANK': 'BANKING',
    'SBIN': 'BANKING',
    'YESBANK': 'BANKING',
    'ITC': 'FMCG',
    'HINDUNILVR': 'FMCG',
    'NESTLEIND': 'FMCG',
    'BRITANNIA': 'FMCG',
    'TITAN': 'FMCG',
    'MARUTI': 'AUTO',
    'TATAMOTORS': 'AUTO',
    'BAJAJ-AUTO': 'AUTO',
    'SUNPHARMA': 'PHARMA',
    'DRREDDY': 'PHARMA',
    'CIPLA': 'PHARMA',
    'ONGC': 'OIL_GAS',
    'TATASTEEL': 'METALS',
    'JSWSTEEL': 'METALS',
    'HINDALCO': 'METALS',
    'DLF': 'DEFAULT',
    'GODREJCP': 'DEFAULT',
    'WIPRO': 'TECHNOLOGY',
    'HCLTECH': 'TECHNOLOGY',
    'TECHM': 'TECHNOLOGY',
    'LT': 'DEFAULT',
    'ASIANPAINT': 'FMCG',
    'ULTRACEMCO': 'DEFAULT',
    'M&M': 'AUTO',
    'TATAELXSI': 'TECHNOLOGY',
    'COALINDIA': 'METALS',
    'POWERGRID': 'DEFAULT',
    'NTPC': 'DEFAULT',
}

# ================================================================
# SECTION 4: NEWS SENTIMENT ANALYZER
# ================================================================

class NewsSentimentAnalyzer:
    def __init__(self):
        self.analyzer = SentimentIntensityAnalyzer()
        
    def get_news_for_stock(self, symbol):
        """Fetch news for a stock and analyze sentiment"""
        try:
            # Clean symbol
            clean_symbol = str(symbol).strip().replace('.NS', '')
            
            # Try Yahoo Finance news
            ticker = yf.Ticker(clean_symbol + ".NS")
            news = ticker.news
            
            if not news:
                return None
            
            # Analyze sentiment
            sentiments = []
            for n in news[:5]:
                title = n.get('title', '')
                sentiment = self.analyzer.polarity_scores(title)
                sentiments.append(sentiment['compound'])
            
            if sentiments:
                overall_sentiment = np.mean(sentiments)
                sentiment_score = (overall_sentiment + 1) * 50
                
                positive = sum(1 for s in sentiments if s > 0.05)
                negative = sum(1 for s in sentiments if s < -0.05)
                neutral = len(sentiments) - positive - negative
                
                return {
                    'sentiment_score': sentiment_score,
                    'sentiment_label': 'Positive' if sentiment_score > 60 else 'Negative' if sentiment_score < 40 else 'Neutral',
                    'positive_count': positive,
                    'negative_count': negative,
                    'neutral_count': neutral,
                    'total_news': len(sentiments),
                    'top_news': news[:3]
                }
            
            return None
            
        except Exception:
            return None

# ================================================================
# SECTION 5: AI RECOMMENDATION ENGINE
# ================================================================

class AIRecommendationEngine:
    def __init__(self):
        self.sentiment_analyzer = NewsSentimentAnalyzer()
        
    def get_sector_for_symbol(self, symbol):
        """Get sector for a symbol"""
        clean_symbol = str(symbol).strip().replace('.NS', '')
        
        if clean_symbol in SYMBOL_SECTOR_MAP:
            return SYMBOL_SECTOR_MAP[clean_symbol]
        
        # Try to infer from symbol
        if 'BANK' in clean_symbol or clean_symbol.endswith('BANK'):
            return 'BANKING'
        elif 'TECH' in clean_symbol or 'INF' in clean_symbol:
            return 'TECHNOLOGY'
        elif 'PHARMA' in clean_symbol or 'DR' in clean_symbol:
            return 'PHARMA'
        elif 'AUTO' in clean_symbol:
            return 'AUTO'
        elif 'OIL' in clean_symbol or 'GAS' in clean_symbol:
            return 'OIL_GAS'
        
        return 'DEFAULT'
    
    def get_sector_benchmarks(self, sector):
        """Get benchmarking values for a sector"""
        return SECTOR_BENCHMARKS.get(sector, SECTOR_BENCHMARKS['DEFAULT'])
    
    def score_metric(self, value, benchmarks, metric_name):
        """Score a metric based on sector benchmarks"""
        if value is None or value == 0:
            return 50
        
        bench = benchmarks.get(metric_name)
        if not bench:
            return 50
        
        min_val = bench['min']
        max_val = bench['max']
        ideal_val = bench['ideal']
        
        lower_is_better = metric_name in ['pe_ratio', 'pb_ratio', 'debt_to_equity']
        
        if lower_is_better:
            if value <= ideal_val:
                return 90
            elif value <= max_val:
                return 90 - (40 * (value - ideal_val) / (max_val - ideal_val))
            else:
                return max(30, 50 - (20 * (value - max_val) / max_val))
        else:
            if value >= ideal_val:
                return 90
            elif value >= min_val:
                return 50 + (40 * (value - min_val) / (ideal_val - min_val))
            else:
                return max(30, 50 - (20 * (min_val - value) / min_val))
    
    def analyze_stock(self, symbol, current_price, avg_price, qty, invested_amount):
        """Comprehensive analysis of a single stock"""
        try:
            # Get fundamental data
            ticker = yf.Ticker(str(symbol).strip() + ".NS")
            info = ticker.info
            
            # Extract metrics
            pe = float(info.get('trailingPE', 0) or 0)
            roe = float(info.get('returnOnEquity', 0) or 0) * 100
            debt_equity = float(info.get('debtToEquity', 0) or 0)
            profit_margin = float(info.get('profitMargins', 0) or 0) * 100
            revenue_growth = float(info.get('revenueGrowth', 0) or 0) * 100
            earnings_growth = float(info.get('earningsGrowth', 0) or 0) * 100
            
            # Determine sector
            sector = self.get_sector_for_symbol(symbol)
            benchmarks = self.get_sector_benchmarks(sector)
            
            # Calculate fundamental scores
            fund_scores = {
                'pe_ratio': self.score_metric(pe, benchmarks, 'pe_ratio'),
                'roe': self.score_metric(roe, benchmarks, 'roe'),
                'debt_to_equity': self.score_metric(debt_equity, benchmarks, 'debt_to_equity'),
                'profit_margin': self.score_metric(profit_margin, benchmarks, 'profit_margin'),
                'revenue_growth': self.score_metric(revenue_growth, benchmarks, 'revenue_growth'),
                'earnings_growth': self.score_metric(earnings_growth, benchmarks, 'earnings_growth'),
            }
            
            # Weighted fundamental score
            weights = {
                'pe_ratio': 0.15,
                'roe': 0.20,
                'debt_to_equity': 0.15,
                'profit_margin': 0.15,
                'revenue_growth': 0.20,
                'earnings_growth': 0.15,
            }
            
            fund_score = sum(fund_scores.get(m, 50) * weights.get(m, 0.15) 
                            for m in weights.keys())
            fund_score = min(100, max(0, fund_score))
            
            # Get technical score (simplified)
            tech_score = self._calculate_technical_score(symbol, current_price)
            
            # Get news sentiment
            news = self.sentiment_analyzer.get_news_for_stock(symbol)
            sentiment_score = news['sentiment_score'] if news else 50
            
            # Overall health score
            health_score = (fund_score * 0.4 + tech_score * 0.3 + sentiment_score * 0.3)
            
            # Returns
            returns_pct = ((current_price - avg_price) / avg_price) * 100 if avg_price > 0 else 0
            current_value = qty * current_price
            unrealized_pnl = current_value - invested_amount
            
            # Generate recommendation
            recommendation = self._generate_recommendation(
                health_score, 
                returns_pct,
                news
            )
            
            return {
                'symbol': symbol,
                'current_price': current_price,
                'avg_price': avg_price,
                'qty': qty,
                'current_value': current_value,
                'invested_amount': invested_amount,
                'unrealized_pnl': unrealized_pnl,
                'returns_pct': returns_pct,
                'health_score': health_score,
                'fundamental_score': fund_score,
                'technical_score': tech_score,
                'sentiment_score': sentiment_score,
                'sector': sector,
                'pe_ratio': pe,
                'roe': roe,
                'debt_to_equity': debt_equity,
                'profit_margin': profit_margin,
                'news': news,
                'recommendation': recommendation,
                'fundamentals': fund_scores
            }
            
        except Exception as e:
            return None
    
    def _calculate_technical_score(self, symbol, current_price):
        """Calculate technical health score"""
        try:
            ticker = yf.Ticker(str(symbol).strip() + ".NS")
            hist = ticker.history(period="3mo")
            
            if len(hist) < 20:
                return 50
            
            close = hist['Close']
            sma_50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else close.mean()
            
            # Price vs 50-day SMA
            price_vs_sma = ((current_price - sma_50) / sma_50) * 100
            
            score = 50
            if -5 < price_vs_sma < 5:
                score += 10
            elif price_vs_sma < -10:
                score -= 10
            elif price_vs_sma > 20:
                score += 5
            
            # Volume analysis
            avg_volume = hist['Volume'].mean()
            current_volume = hist['Volume'].iloc[-1]
            volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1
            
            if volume_ratio > 1.5:
                score += 5
            
            return min(100, max(0, score))
            
        except:
            return 50
    
    def _generate_recommendation(self, health_score, returns_pct, news):
        """Generate action recommendation"""
        # Check news sentiment for negative impact
        news_negative = news and news['sentiment_label'] == 'Negative'
        news_positive = news and news['sentiment_label'] == 'Positive'
        
        if health_score >= 70:
            if returns_pct > 50:
                return {
                    'action': '🟢 STRONG HOLD',
                    'priority': 'Low',
                    'reason': 'Excellent fundamentals and strong performance. Continue holding for long-term growth.'
                }
            elif returns_pct > 20:
                return {
                    'action': '🟢 HOLD',
                    'priority': 'Low',
                    'reason': 'Healthy stock with good returns. Good for long-term portfolio.'
                }
            elif news_positive:
                return {
                    'action': '🟢 ACCUMULATE',
                    'priority': 'Low',
                    'reason': 'Strong fundamentals with positive news. Consider adding more.'
                }
            else:
                return {
                    'action': '🟢 HOLD',
                    'priority': 'Low',
                    'reason': 'Good fundamentals. Hold for now.'
                }
        
        elif health_score >= 50:
            if news_negative:
                return {
                    'action': '🔴 SELL ON STRENGTH',
                    'priority': 'High',
                    'reason': 'Average fundamentals with negative news. Consider reducing position.'
                }
            elif returns_pct < -15:
                return {
                    'action': '🟡 REVIEW',
                    'priority': 'Medium',
                    'reason': 'Weakening fundamentals. Monitor quarterly results.'
                }
            else:
                return {
                    'action': '🟡 HOLD & MONITOR',
                    'priority': 'Medium',
                    'reason': 'Mixed signals. Hold but watch closely.'
                }
        
        else:  # health_score < 50
            if news_negative:
                return {
                    'action': '🔴 IMMEDIATE SELL',
                    'priority': 'High',
                    'reason': 'Poor fundamentals and negative news. Exit immediately.'
                }
            elif returns_pct < -20:
                return {
                    'action': '🔴 SELL',
                    'priority': 'High',
                    'reason': 'Poor fundamentals and significant losses. Consider selling.'
                }
            elif returns_pct < -10:
                return {
                    'action': '🔴 PARTIAL SELL',
                    'priority': 'High',
                    'reason': 'Weak fundamentals. Reduce position to minimize downside.'
                }
            else:
                return {
                    'action': '🔴 SELL',
                    'priority': 'Medium',
                    'reason': 'Poor fundamentals. Better opportunities elsewhere.'
                }

# ================================================================
# SECTION 6: TRANSACTION CRUD
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
# SECTION 7: CASHFLOW CRUD
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
# SECTION 8: PRICE FETCH
# ================================================================

def get_price(stock):
    """Always returns a plain Python float."""
    try:
        val = yf.Ticker(str(stock).strip() + ".NS").history(period="1d")["Close"].iloc[-1]
        return float(pd.to_numeric(val, errors="coerce") or 0.0)
    except Exception:
        return 0.0

# ================================================================
# SECTION 9: PORTFOLIO CALCULATIONS
# ================================================================

def compute_portfolio(df):
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
        buy_cost = float(df.loc[df["type"] == "BUY", "amount"].sum())
        sell_proceeds = float(df.loc[df["type"] == "SELL", "amount"].sum())
        realised_pnl = sell_proceeds - buy_cost
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
    holdings["invested"] = holdings["qty"] * holdings["avg_price"]

    holdings["cmp"] = holdings["stock"].apply(get_price)
    holdings["cmp"] = pd.to_numeric(holdings["cmp"], errors="coerce").fillna(0.0).astype("float64")
    holdings["value"] = holdings["qty"] * holdings["cmp"]

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
            sold_cost += float(grp["qty"].sum()) * avg_p

    realised_pnl = sell_proceeds - sold_cost
    pnl = unrealised_pnl + realised_pnl

    holdings["pnl"] = (holdings["value"] - holdings["invested"]).round(2)

    return invested, total_value, pnl, holdings


def compute_xirr(df):
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
        float(qty) * get_price(str(stock))
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
# SECTION 10: FREE CASH
# ================================================================

def calculate_free_cash(df):
    cash_df = load_cashflows()
    if cash_df.empty:
        return 0.0

    total_cash = float(
        cash_df[cash_df["type"].isin(["CREDIT", "DIVIDEND"])]["amount"].sum()
    )

    if df.empty:
        return round(total_cash, 2)

    df = df.copy()
    df = sanitize_numeric(df, ["qty", "price", "charges"])
    df["type"] = df["type"].astype(str).str.strip().str.upper()
    df["amount"] = df["qty"] * df["price"]

    buy_spent = float(df.loc[df["type"] == "BUY", "amount"].sum())
    sell_received = float(df.loc[df["type"] == "SELL", "amount"].sum())
    total_charges = float(df["charges"].sum())

    available = total_cash - buy_spent - total_charges + sell_received
    return round(max(available, 0.0), 2)


def check_free_cash_before_buy(df, new_date, qty, price):
    cash_df = load_cashflows()
    if cash_df.empty:
        return False

    total_cash = float(
        cash_df[cash_df["type"].isin(["CREDIT", "DIVIDEND"])]["amount"].sum()
    )

    df = df.copy()
    df = sanitize_numeric(df, ["qty", "price", "charges"])
    df["type"] = df["type"].astype(str).str.strip().str.upper()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    past = df[df["date"] <= pd.to_datetime(new_date)].copy()
    past["amount"] = past["qty"] * past["price"]

    buy_spent = float(past.loc[past["type"] == "BUY", "amount"].sum())
    sell_received = float(past.loc[past["type"] == "SELL", "amount"].sum())
    charges_total = float(past["charges"].sum())

    available = total_cash - buy_spent - charges_total + sell_received
    return available >= float(qty) * float(price)

# ================================================================
# SECTION 11: NAV SYSTEM
# ================================================================

def calculate_total_units(cash_df):
    if cash_df.empty:
        return 0.0
    credit = float(cash_df.loc[cash_df["type"] == "CREDIT", "amount"].sum())
    debit = float(cash_df.loc[cash_df["type"] == "DEBIT", "amount"].sum())
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
        data = sheet.get_all_records()
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
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        if df.empty:
            return pd.DataFrame(columns=["date", "nav", "portfolio_value", "units"])
        df.columns = [str(c).strip().lower() for c in df.columns]
        df = sanitize_numeric(df, ["nav", "portfolio_value", "units"])
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        return df
    except Exception:
        return pd.DataFrame(columns=["date", "nav", "portfolio_value", "units"])

# ================================================================
# SECTION 12: FUNDAMENTALS SCORING ENGINE
# ================================================================

def should_update_scores():
    now = datetime.now()
    current_time = now.time()
    morning_start = time(10, 0)
    morning_end = time(10, 15)
    eod_start = time(15, 0)
    eod_end = time(15, 15)
    return (
        morning_start <= current_time <= morning_end
        or
        eod_start <= current_time <= eod_end
    )


def calculate_fundamental_score(stock):
    try:
        ticker = yf.Ticker(str(stock).strip() + ".NS")
        info = ticker.info

        roe = float(info.get("returnOnEquity") or 0) * 100
        revenue_growth = float(info.get("revenueGrowth") or 0) * 100
        profit_growth = float(info.get("earningsGrowth") or 0) * 100
        debt_equity = float(info.get("debtToEquity") or 0)
        margin = float(info.get("operatingMargins") or 0) * 100

        score = 0
        if roe > 15: score += 8
        if revenue_growth > 10: score += 10
        if profit_growth > 10: score += 10
        if debt_equity < 1: score += 6
        if margin > 15: score += 6

        return {
            "stock": stock,
            "fundamentals": score,
            "roe": round(roe, 2),
            "revenue_growth": round(revenue_growth, 2),
            "profit_growth": round(profit_growth, 2),
            "debt_equity": round(debt_equity, 2),
            "margin": round(margin, 2),
        }
    except Exception:
        return {
            "stock": stock,
            "fundamentals": 0,
            "roe": 0,
            "revenue_growth": 0,
            "profit_growth": 0,
            "debt_equity": 0,
            "margin": 0,
        }


def load_score_history():
    try:
        sheet = get_score_sheet()
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
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
        sheet = get_score_sheet()
        history_df = load_score_history()
        now = datetime.now()
        today = str(now.date())
        session = "MORNING" if now.hour < 12 else "EOD"

        existing = history_df[history_df["date"] == today]
        if not existing.empty:
            existing_session = existing[existing["stock"] == "__SESSION__"]
            if not existing_session.empty:
                sessions_done = existing_session["fundamentals"].tolist()
                if session in sessions_done:
                    return

        for stock in holdings["stock"].unique():
            result = calculate_fundamental_score(stock)
            sheet.append_row([
                today,
                result["stock"],
                result["fundamentals"],
                result["roe"],
                result["revenue_growth"],
                result["profit_growth"],
                result["debt_equity"],
                result["margin"]
            ])

        sheet.append_row([today, "__SESSION__", session, 0, 0, 0, 0, 0])

    except Exception:
        pass

# ================================================================
# SECTION 13: SAVE RECOMMENDATIONS
# ================================================================

def save_recommendations(analysis_results):
    """Save AI recommendations to Google Sheets"""
    try:
        sheet = get_recommendation_sheet()
        today = str(datetime.today().date())
        
        # Clear old recommendations for today
        data = sheet.get_all_records()
        if data:
            # Find rows with today's date
            rows_to_delete = []
            for i, row in enumerate(data, start=2):
                if str(row.get('date', '')).startswith(today):
                    rows_to_delete.append(i)
            
            # Delete from bottom to top
            for row_num in reversed(rows_to_delete):
                sheet.delete_rows(row_num)
        
        # Add new recommendations
        for stock in analysis_results:
            rec = stock['recommendation']
            sheet.append_row([
                today,
                stock['symbol'],
                round(stock['current_price'], 2),
                round(stock['avg_price'], 2),
                stock['qty'],
                round(stock['returns_pct'], 2),
                round(stock['health_score'], 2),
                round(stock['sentiment_score'], 2),
                rec['action'],
                rec['reason'],
                stock['sector'],
                round(stock['pe_ratio'], 2),
                round(stock['roe'], 2)
            ])
    except Exception as e:
        pass

# ================================================================
# SECTION 14: STREAMLIT APP
# ================================================================

st.set_page_config(page_title="Portfolio Tracker with AI", layout="wide")
st.title("📊 AI-Powered Portfolio Tracker")

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

# ---- CALCULATIONS ----
invested, value, pnl, holdings = compute_portfolio(df)
xirr_val = compute_xirr(df)
free_cash = calculate_free_cash(df)
cash_df = load_cashflows()
units = calculate_total_units(cash_df)
total_assets = value + free_cash
nav = calculate_nav(value, free_cash, units)
save_nav_history(nav, total_assets, units)
nav_df = load_nav_history()

# Auto-run scoring engine
save_fundamental_scores(holdings)

# ---- TABS ----
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📈 Dashboard",
    "➕ Add Transaction",
    "📌 Holdings",
    "🧠 Scoring",
    "💰 Funds",
    "🤖 AI Analysis"
])

# ================================================================
# TAB 1: DASHBOARD
# ================================================================
with tab1:
    st.subheader("📈 Portfolio Overview")

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Invested", f"₹{invested:,.2f}")
    col2.metric("Current Value", f"₹{value:,.2f}")
    col3.metric("P&L", f"₹{pnl:,.2f}")
    col4.metric("XIRR", f"{(xirr_val or 0.0) * 100:.2f}%")
    col5.metric("Free Cash", f"₹{free_cash:,.2f}")
    col6.metric("NAV", f"₹{nav:.2f}")

    total_charges_display = float(df["charges"].sum()) if not df.empty else 0.0
    st.caption(
        f"📊 P&L (price gain only): ₹{pnl:,.2f}  |  "
        f"Charges paid (from cash): ₹{total_charges_display:,.2f}  |  "
        f"Net after charges: ₹{pnl - total_charges_display:,.2f}"
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

        today_ts = pd.Timestamp.today()
        cutoff_map = {
            "1M": today_ts - pd.DateOffset(months=1),
            "3M": today_ts - pd.DateOffset(months=3),
            "6M": today_ts - pd.DateOffset(months=6),
            "1Y": today_ts - pd.DateOffset(years=1),
            "5Y": today_ts - pd.DateOffset(years=5),
            "YTD": pd.Timestamp(year=today_ts.year, month=1, day=1),
        }

        nav_filtered = nav_df[nav_df["date"] >= cutoff_map[range_option]].copy()
        nav_filtered = nav_filtered.sort_values("date")
        nav_filtered["date_str"] = nav_filtered["date"].dt.strftime("%d %b '%y")

        nav_chart = px.line(
            nav_filtered,
            x="date_str",
            y="nav",
            markers=True,
            title=f"NAV Growth ({range_option})"
        )
        nav_chart.update_layout(
            xaxis_title="",
            yaxis_title="NAV (₹)",
            hovermode="x unified",
            xaxis=dict(tickangle=-45, showgrid=False),
            yaxis=dict(showgrid=True),
            plot_bgcolor="rgba(0,0,0,0)",
        )
        nav_chart.update_traces(
            line=dict(width=2),
            marker=dict(size=6),
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

    search_query = st.text_input("Search Stock (e.g. hdfc, reliance)")
    stock_options = search_stocks(search_query) if search_query else []

    if not stock_options:
        stock_options = [{"label": "No results", "symbol": ""}]

    selected_stock = st.selectbox("Select Stock", stock_options, format_func=lambda x: x["label"])
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
                can_buy = check_free_cash_before_buy(df, date, qty, price)
                if not can_buy:
                    st.error("❌ Insufficient Free Cash for this transaction!")
                    st.stop()

            add_transaction({
                "date": str(date),
                "stock": stock,
                "qty": qty,
                "price": price,
                "type": type_,
                "charges": float(charges)
            })
            st.success("Transaction Added!")
            st.rerun()

    st.divider()

    cutoff = pd.Timestamp.today() - pd.DateOffset(months=3)
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
                date = st.date_input("Date", value=pd.to_datetime(edit_data["date"]))
                stock_edit = st.text_input("Stock", value=edit_data["stock"])
                qty = st.number_input("Qty", value=float(edit_data["qty"]))
                price = st.number_input("Price", value=float(edit_data["price"]))
                type_ = st.selectbox("Type", ["BUY", "SELL"])
                charges = st.number_input("Charges", value=float(edit_data["charges"]))
                update_btn = st.form_submit_button("Update")

                if update_btn:
                    update_transaction(edit_row, {
                        "date": str(date),
                        "stock": stock_edit,
                        "qty": float(qty),
                        "price": float(price),
                        "type": type_,
                        "charges": float(charges)
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
# TAB 4: SCORING
# ================================================================
with tab4:
    st.subheader("🧠 Fundamentals Scoring Engine")

    score_df = load_score_history()

    if score_df is not None and not score_df.empty:
        score_df = score_df[score_df["stock"] != "__SESSION__"]
        latest_scores = (
            score_df.sort_values("date")
            .groupby("stock")
            .tail(1)
        )
        st.dataframe(latest_scores, use_container_width=True)
    else:
        st.info("No score history available yet. Scores update at 10:00–10:15 AM and 3:00–3:15 PM.")

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
        date = st.date_input("Date")
        amount = st.number_input("Amount", min_value=0.0)
        type_ = st.selectbox("Type", ["CREDIT", "DEBIT", "DIVIDEND"])
        note = st.text_input("Note")
        submit = st.form_submit_button("Add Fund Entry")

        if submit:
            add_cashflow_entry({
                "date": str(date),
                "type": type_,
                "amount": float(amount),
                "note": note
            })
            st.success("Fund Entry Added!")
            st.rerun()

# ================================================================
# TAB 6: AI ANALYSIS
# ================================================================
with tab6:
    st.subheader("🤖 AI-Powered Stock Analysis")
    st.markdown("**Smart recommendations based on fundamentals, technicals, news sentiment, and sector benchmarks**")
    
    if holdings is None or holdings.empty:
        st.info("📊 No holdings to analyze. Add some stocks first!")
    else:
        if st.button("🚀 Run AI Analysis", use_container_width=True, type="primary"):
            with st.spinner("🔍 Analyzing your portfolio... This may take a moment."):
                engine = AIRecommendationEngine()
                analysis_results = []
                
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                for idx, (_, row) in enumerate(holdings.iterrows()):
                    status_text.text(f"Analyzing {row['stock']}... ({idx+1}/{len(holdings)})")
                    progress_bar.progress((idx + 1) / len(holdings))
                    
                    # Get current price
                    current_price = get_price(row['stock'])
                    
                    # Calculate invested amount for this stock
                    stock_txns = df[df['stock'] == row['stock']]
                    invested_amount = 0
                    avg_price = 0
                    
                    if not stock_txns.empty:
                        buys = stock_txns[stock_txns['type'] == 'BUY']
                        if not buys.empty:
                            invested_amount = (buys['qty'] * buys['price']).sum()
                            total_qty = buys['qty'].sum()
                            avg_price = invested_amount / total_qty if total_qty > 0 else 0
                    
                    analysis = engine.analyze_stock(
                        row['stock'],
                        current_price,
                        avg_price,
                        row['qty'],
                        invested_amount
                    )
                    
                    if analysis:
                        analysis_results.append(analysis)
                
                status_text.text("✅ Analysis complete!")
                
                if analysis_results:
                    # Save recommendations to Google Sheets
                    save_recommendations(analysis_results)
                    
                    # Display results
                    st.divider()
                    
                    # Portfolio Summary
                    st.subheader("📊 Portfolio Health Summary")
                    
                    total_value = sum(s['current_value'] for s in analysis_results)
                    total_invested = sum(s['invested_amount'] for s in analysis_results)
                    total_pnl = total_value - total_invested
                    avg_health = np.mean([s['health_score'] for s in analysis_results])
                    
                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("Total Value", f"₹{total_value:,.2f}")
                    col2.metric("Total Invested", f"₹{total_invested:,.2f}")
                    col3.metric("P&L", f"₹{total_pnl:,.2f}", 
                               delta=f"{((total_pnl/total_invested)*100 if total_invested>0 else 0):.2f}%")
                    col4.metric("Avg Health Score", f"{avg_health:.1f}/100")
                    
                    # Sector Allocation
                    st.subheader("🏢 Sector Allocation")
                    sector_data = {}
                    for s in analysis_results:
                        sector = s['sector']
                        sector_data[sector] = sector_data.get(sector, 0) + s['current_value']
                    
                    sector_df = pd.DataFrame({
                        'Sector': list(sector_data.keys()),
                        'Value': list(sector_data.values())
                    })
                    st.dataframe(sector_df, use_container_width=True)
                    
                    # Recommendations by Priority
                    st.subheader("🎯 AI Recommendations")
                    
                    # High Priority - Sell
                    high_priority = [s for s in analysis_results 
                                   if 'SELL' in s['recommendation']['action'] 
                                   or 'IMMEDIATE' in s['recommendation']['action']]
                    
                    if high_priority:
                        st.warning("🔴 HIGH PRIORITY - Action Recommended")
                        sell_data = [{
                            'Stock': s['symbol'],
                            'Current Value': f"₹{s['current_value']:,.2f}",
                            'Returns': f"{s['returns_pct']:.1f}%",
                            'Health Score': f"{s['health_score']:.1f}",
                            'Action': s['recommendation']['action'],
                            'Reason': s['recommendation']['reason']
                        } for s in high_priority]
                        st.dataframe(pd.DataFrame(sell_data), use_container_width=True)
                    
                    # Medium Priority - Monitor
                    medium_priority = [s for s in analysis_results 
                                     if 'MONITOR' in s['recommendation']['action'] 
                                     or 'REVIEW' in s['recommendation']['action']]
                    
                    if medium_priority:
                        st.info("🟡 MEDIUM PRIORITY - Monitor Closely")
                        monitor_data = [{
                            'Stock': s['symbol'],
                            'Current Value': f"₹{s['current_value']:,.2f}",
                            'Returns': f"{s['returns_pct']:.1f}%",
                            'Health Score': f"{s['health_score']:.1f}",
                            'Action': s['recommendation']['action'],
                            'Reason': s['recommendation']['reason']
                        } for s in medium_priority]
                        st.dataframe(pd.DataFrame(monitor_data), use_container_width=True)
                    
                    # Low Priority - Hold
                    low_priority = [s for s in analysis_results 
                                  if 'HOLD' in s['recommendation']['action'] 
                                  or 'ACCUMULATE' in s['recommendation']['action']]
                    
                    if low_priority:
                        st.success("🟢 LOW PRIORITY - Hold/Accumulate")
                        hold_data = [{
                            'Stock': s['symbol'],
                            'Current Value': f"₹{s['current_value']:,.2f}",
                            'Returns': f"{s['returns_pct']:.1f}%",
                            'Health Score': f"{s['health_score']:.1f}",
                            'Action': s['recommendation']['action']
                        } for s in low_priority]
                        st.dataframe(pd.DataFrame(hold_data), use_container_width=True)
                    
                    # Detailed Analysis
                    st.subheader("📋 Detailed Stock Analysis")
                    detail_data = []
                    for s in analysis_results:
                        news = s.get('news', {})
                        detail_data.append({
                            'Stock': s['symbol'],
                            'Price': f"₹{s['current_price']:,.2f}",
                            'Returns': f"{s['returns_pct']:.1f}%",
                            'Health': f"{s['health_score']:.0f}",
                            'Sector': s['sector'],
                            'P/E': f"{s['pe_ratio']:.1f}",
                            'ROE': f"{s['roe']:.1f}%",
                            'Sentiment': news.get('sentiment_label', 'N/A') if news else 'N/A',
                            'News Count': news.get('total_news', 0) if news else 0,
                            'Action': s['recommendation']['action']
                        })
                    
                    st.dataframe(pd.DataFrame(detail_data), use_container_width=True)
                    
                    # News Sentiment Section
                    st.subheader("📰 News Sentiment Summary")
                    for s in analysis_results:
                        news = s.get('news')
                        if news and news['total_news'] > 0:
                            with st.expander(f"📰 {s['symbol']} - {news['sentiment_label']} ({news['total_news']} articles)"):
                                st.write(f"**Sentiment Score:** {news['sentiment_score']:.1f}/100")
                                st.write(f"**Positive:** {news['positive_count']} | **Negative:** {news['negative_count']} | **Neutral:** {news['neutral_count']}")
                                if news.get('top_news'):
                                    st.write("**Top Headlines:**")
                                    for n in news['top_news']:
                                        sentiment_emoji = "🟢" if n['sentiment']['compound'] > 0.05 else "🔴" if n['sentiment']['compound'] < -0.05 else "⚪"
                                        st.write(f"{sentiment_emoji} {n['title'][:100]}...")
