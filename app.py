# app.py
import os
import time
import json
from datetime import datetime
from urllib.parse import urlparse

import bcrypt
import requests
import gspread
from flask import (
    Flask, render_template, request, redirect, url_for, session, flash, jsonify
)
from oauth2client.service_account import ServiceAccountCredentials

# --- Config from env ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID", "7726404086").strip()
ONEHPING_API_KEY = os.getenv("ONEHPING_API_KEY", "").strip()
ONEHPING_API_URL = os.getenv(
    "ONEHPING_API_URL",
    "https://app.1hping.com/external/api/campaign/create?culture=vi-VN",
)
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "").strip()
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "change-me")

if not (TELEGRAM_BOT_TOKEN and ONEHPING_API_KEY and GOOGLE_SHEET_ID):
    raise RuntimeError("Missing required env: TELEGRAM_BOT_TOKEN, ONEHPING_API_KEY, GOOGLE_SHEET_ID")

# --- Google creds ---
# Accept GOOGLE_SA_JSON (full json string) or GOOGLE_SA_FILE (path)
sa_json = os.getenv("GOOGLE_SA_JSON")
sa_file = os.getenv("GOOGLE_SA_FILE")
if sa_json:
    sa_info = json.loads(sa_json)
    credentials = ServiceAccountCredentials.from_json_keyfile_dict(
        sa_info, scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    )
elif sa_file and os.path.exists(sa_file):
    credentials = ServiceAccountCredentials.from_json_keyfile_name(
        sa_file, scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    )
else:
    raise RuntimeError("Provide GOOGLE_SA_JSON or GOOGLE_SA_FILE")

gc = gspread.authorize(credentials)
sheet = gc.open_by_key(GOOGLE_SHEET_ID)

# Utility
def now_str():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def sanitize_urls(raw_text: str):
    """Tách các URL từ textarea; chỉ lấy http/https; loại trùng, giữ thứ tự"""
    candidates = []
    for part in raw_text.replace("\r", "\n").splitlines():
        s = part.strip()
        if not s:
            continue
        # nếu có nhiều url trong 1 dòng, split by space
        for tok in s.split():
            tok = tok.strip().strip(",;")
            parsed = urlparse(tok)
            if parsed.scheme in ("http", "https") and parsed.netloc:
                candidates.append(tok)
    # dedupe keep order
    seen = set()
    out = []
    for u in candidates:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

def check_credentials(username: str, password: str):
    """Kiểm tra user/password so với Google Sheet (bcrypt)"""
    ws = sheet.worksheet("users")
    # đọc tất cả
    records = ws.get_all_records()
    for r in records:
        if str(r.get("username")) == username:
            phash = r.get("password_hash")
            if not phash:
                return False
            try:
                ok = bcrypt.checkpw(password.encode(), phash.encode())
                return ok
            except Exception:
                return False
    return False

def append_log(row):
    """Append row to logs sheet"""
    try:
        ws = sheet.worksheet("logs")
    except Exception:
        # nếu chưa có sheet logs, tạo
        ws = sheet.add_worksheet("logs", rows=1000, cols=10)
        ws.append_row(["timestamp","username","campaign_name","number_of_day","urls_count","response_status","response_body"])
    ws.append_row(row)

def send_telegram_message(chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code, r.text
    except Exception as e:
        return None, str(e)

def create_campaign_1hping(campaign_name: str, number_of_day: int, urls: list):
    headers = {
        "ApiKey": ONEHPING_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "CampaignName": campaign_name,
        "NumberOfDay": number_of_day,
        "Urls": urls,
    }
    r = requests.post(ONEHPING_API_URL, headers=headers, json=payload, timeout=120)
    # Try return json or text
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text

# --- Flask app ---
app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

@app.route("/")
def index():
    if session.get("username"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username","").strip()
        password = request.form.get("password","").strip()
        if not username or not password:
            flash("Vui lòng nhập username và password", "danger")
            return redirect(url_for("login"))
        if check_credentials(username, password):
            session["username"] = username
            flash("Đăng nhập thành công", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Tên đăng nhập hoặc mật khẩu không đúng", "danger")
            return redirect(url_for("login"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard", methods=["GET","POST"])
def dashboard():
    if not session.get("username"):
        return redirect(url_for("login"))
    username = session["username"]
    if request.method == "POST":
        raw_urls = request.form.get("urls","")
        days = request.form.get("days","1").strip()
        try:
            days = int(days)
            if days < 1 or days > 365:
                flash("Số ngày phải từ 1 đến 365", "danger")
                return redirect(url_for("dashboard"))
        except ValueError:
            flash("Số ngày không hợp lệ", "danger")
            return redirect(url_for("dashboard"))
        urls = sanitize_urls(raw_urls)
        if not urls:
            flash("Không tìm thấy URL hợp lệ (http/https)", "danger")
            return redirect(url_for("dashboard"))

        # Tạo campaign name: username_timestamp (an toàn hơn TelegramName_UserID vì site không có tg id)
        timestamp = int(time.time())
        campaign_name = f"{username}_{timestamp}"

        # Gọi API 1hping
        status, resp = create_campaign_1hping(campaign_name, days, urls)
        resp_str = json.dumps(resp, ensure_ascii=False) if not isinstance(resp, str) else resp

        # Ghi log vào Google Sheet
        log_row = [now_str(), username, campaign_name, str(days), str(len(urls)), str(status), resp_str[:32000]]
        try:
            append_log(log_row)
        except Exception as e:
            # Ignore append failure but inform admin
            send_telegram_message(ADMIN_TELEGRAM_ID, f"[ALERT] Ghi logs thất bại: {e}")

        # Gửi log về Telegram admin
        tg_text = (
            f"[1hping] New campaign\n"
            f"User: {username}\n"
            f"Campaign: {campaign_name}\n"
            f"Days: {days}\n"
            f"URLs: {len(urls)}\n"
            f"Status: {status}\n"
            f"Resp: {str(resp)[:1000]}"
        )
        send_telegram_message(ADMIN_TELEGRAM_ID, tg_text)

        flash("Đã tạo chiến dịch. Kết quả đã gửi về Telegram admin.", "success")
        return redirect(url_for("dashboard"))

    return render_template("dashboard.html", username=username)

# Simple health check
@app.route("/health")
def health():
    return jsonify({"status":"ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
