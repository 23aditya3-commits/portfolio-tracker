import streamlit as st
import gspread
import pandas as pd
from oauth2client.service_account import ServiceAccountCredentials

# ---------------- GOOGLE SHEETS CONNECTION ----------------

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

    client = gspread.authorize(creds)

    return client


# ---------------- OPEN SHEET ----------------

def get_sheet():

    client = get_client()

    sheet_name = st.secrets["sheets"]["sheet_name"]

    return client.open(sheet_name).worksheet("transactions")


# ---------------- LOAD DATA (WITH INDEX) ----------------

def load_transactions():

    sheet = get_sheet()

    data = sheet.get_all_records()

    df = pd.DataFrame(data)

    # Handle empty sheet
    if df.empty:
        return pd.DataFrame(columns=["date", "stock", "qty", "price", "type", "charges"])

    # Normalize column names safely
    df.columns = [str(c).strip().lower() for c in df.columns]

    # Add sheet row index (VERY IMPORTANT for edit/delete)
    df["row_index"] = range(2, len(df) + 2)

    return df


# ---------------- ADD TRANSACTION ----------------

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


# ---------------- DELETE TRANSACTION ----------------

def delete_transaction(row_index):

    sheet = get_sheet()

    sheet.delete_rows(row_index)


# ---------------- UPDATE TRANSACTION ----------------

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


# ---------------- CLEAR ALL DATA (OPTIONAL SAFE RESET) ----------------

def clear_transactions():

    sheet = get_sheet()

    sheet.clear()

    sheet.append_row([
        "date",
        "stock",
        "qty",
        "price",
        "type",
        "charges"
    ])
