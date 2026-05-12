import gspread
import pandas as pd
from oauth2client.service_account import ServiceAccountCredentials

SHEET_NAME = "portfolio_tracker"

def get_client():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]

    creds = ServiceAccountCredentials.from_json_keyfile_name(
        "credentials.json", scope
    )

    client = gspread.authorize(creds)
    return client


def load_transactions():
    client = get_client()
    sheet = client.open(SHEET_NAME).worksheet("transactions")

    data = sheet.get_all_records()
    return pd.DataFrame(data)


def add_transaction(row):
    client = get_client()
    sheet = client.open(SHEET_NAME).worksheet("transactions")

    sheet.append_row([
        row["date"],
        row["stock"],
        row["qty"],
        row["price"],
        row["type"],
        row["charges"]
    ])
