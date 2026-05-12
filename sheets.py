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

    # Read credentials from Streamlit Secrets
    creds_dict = dict(st.secrets["gcp_service_account"])

    # Authenticate
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

    sheet = client.open(sheet_name).worksheet("transactions")

    return sheet


# ---------------- LOAD DATA ----------------

def load_transactions():

    sheet = get_sheet()

    data = sheet.get_all_records()

    df = pd.DataFrame(data)

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


# ---------------- OPTIONAL: DELETE ALL DATA ----------------

def clear_transactions():

    sheet = get_sheet()

    sheet.clear()

    # Re-add headers
    sheet.append_row([
        "date",
        "stock",
        "qty",
        "price",
        "type",
        "charges"
    ])
