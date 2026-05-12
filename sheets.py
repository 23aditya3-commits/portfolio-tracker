import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials

def get_client():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]

    creds_dict = st.secrets["gcp_service_account"]

    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        creds_dict, scope
    )

    return gspread.authorize(creds)


def get_sheet():
    client = get_client()

    sheet_name = st.secrets["sheets"]["sheet_name"]

    sheet = client.open(sheet_name).worksheet("transactions")
    return sheet
