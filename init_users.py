# init_users.py
import os
import json
import bcrypt
import gspread
from oauth2client.service_account import ServiceAccountCredentials

GOOGLE_SA_JSON = os.getenv("GOOGLE_SA_JSON")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")

if not (GOOGLE_SA_JSON and GOOGLE_SHEET_ID):
    raise RuntimeError("Set GOOGLE_SA_JSON and GOOGLE_SHEET_ID")

creds = json.loads(GOOGLE_SA_JSON)
credentials = ServiceAccountCredentials.from_json_keyfile_dict(
    creds, scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
)
gc = gspread.authorize(credentials)
sheet = gc.open_by_key(GOOGLE_SHEET_ID)

try:
    ws = sheet.worksheet("users")
except Exception:
    ws = sheet.add_worksheet("users", rows=100, cols=10)
    ws.append_row(["username","password_hash","fullname"])

def add_user(username, raw_password, fullname=""):
    ph = bcrypt.hashpw(raw_password.encode(), bcrypt.gensalt()).decode()
    ws.append_row([username, ph, fullname])
    print("Added", username)

if __name__ == "__main__":
    # ví dụ: python init_users.py
    add_user("alice", "Password123", "Alice")
    add_user("bob", "Password456", "Bob")
