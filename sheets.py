import streamlit as st
import gspread
import pandas as pd
from oauth2client.service_account import ServiceAccountCredentials


# ---------------- GOOGLE CLIENT ----------------

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


# ---------------- SHEETS ----------------

def get_sheet():
    client = get_client()
    sheet_name = st.secrets["sheets"]["sheet_name"]
    return client.open(sheet_name).worksheet("transactions")


def get_cashflow_sheet():
    client = get_client()
    sheet_name = st.secrets["sheets"]["sheet_name"]

    # ✅ FIXED: correct tab name MUST exist in Google Sheet
    return client.open(sheet_name).worksheet("cashflow")


# ---------------- TRANSACTIONS ----------------

def load_transactions():

    sheet = get_sheet()
    data = sheet.get_all_records()

    df = pd.DataFrame(data)

    if df.empty:
        return pd.DataFrame(columns=[
            "date", "stock", "qty", "price", "type", "charges"
        ])

    df.columns = [str(c).strip().lower() for c in df.columns]

    df["row_index"] = range(2, len(df) + 2)

    return df


def add_transaction(row):

    sheet = get_sheet()

    sheet.append_row([
        row["date"],
        row["stock"],
        row["qty"],
        row["price"],
        row["type"],
        row["charges"]
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
            row["qty"],
            row["price"],
            row["type"],
            row["charges"]
        ]]
    )


def clear_transactions():

    sheet = get_sheet()
    sheet.clear()

    sheet.append_row([
        "date", "stock", "qty", "price", "type", "charges"
    ])


# ---------------- CASHFLOW SYSTEM (FIXED) ----------------

def load_cashflow():

    sheet = get_cashflow_sheet()
    data = sheet.get_all_records()

    df = pd.DataFrame(data)

    if df.empty:
        return pd.DataFrame(columns=["date", "type", "amount", "note"])

    df.columns = [str(c).strip().lower() for c in df.columns]

    return df


def add_cashflow_entry(row):

    sheet = get_cashflow_sheet()

    sheet.append_row([
        row["date"],
        row["type"],
        row["amount"],
        row["note"]
    ])


def clear_cashflow():

    sheet = get_cashflow_sheet()
    sheet.clear()

    sheet.append_row([
        "date", "type", "amount", "note"
    ])
