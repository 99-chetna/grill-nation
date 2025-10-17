import os
import json
import time
import firebase_admin
from firebase_admin import credentials, db
import pyrebase
from flask import Flask, request, redirect, url_for, render_template, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build

# -------------------- Flask App --------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "fallback-secret")

# -------------------- Firebase Setup --------------------
firebase_web_config_json = os.environ.get("FIREBASE_CONFIG")
firebase_admin_json = os.environ.get("FIREBASE_ADMIN_SDK")

if not firebase_web_config_json:
    raise RuntimeError("❌ Missing FIREBASE_CONFIG environment variable")
if not firebase_admin_json:
    raise RuntimeError("❌ Missing FIREBASE_ADMIN_SDK environment variable")

firebase_web_config = json.loads(firebase_web_config_json)
firebase = pyrebase.initialize_app(firebase_web_config)
pb_auth = firebase.auth()

firebase_admin_config = json.loads(firebase_admin_json)
cred = credentials.Certificate(firebase_admin_config)

if not firebase_admin._apps:
    database_url = firebase_web_config.get("databaseURL")
    if not database_url:
        raise RuntimeError("❌ FIREBASE_CONFIG must include databaseURL")
    firebase_admin.initialize_app(cred, {"databaseURL": database_url})

# -------------------- Google Sheets Setup --------------------
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

try:
    google_creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
    if not google_creds_json:
        raise ValueError("❌ Missing GOOGLE_SHEETS_CREDENTIALS environment variable")

    google_creds_dict = json.loads(google_creds_json)
    creds = service_account.Credentials.from_service_account_info(
        google_creds_dict, scopes=SCOPES
    )
    sheet_service = build("sheets", "v4", credentials=creds)
    sheet = sheet_service.spreadsheets()
    print("✅ Connected to Google Sheets successfully.")
except Exception as e:
    print("⚠️ Google Sheets connection failed:", e)
    sheet = None


# -------------------- Helper: Write to Google Sheets --------------------
def append_rows_to_sheet(rows):
    """Instantly append rows to Google Sheets."""
    if not sheet or not SPREADSHEET_ID:
        print("⚠️ Sheets not configured (missing credentials or ID)")
        return False
    try:
        sheet.values().append(
            spreadsheetId=SPREADSHEET_ID,
            range="Sheet1!A1",
            valueInputOption="RAW",
            body={"values": rows}
        ).execute()
        print(f"✅ Appended {len(rows)} rows to Google Sheet.")
        return True
    except Exception as e:
        print("⚠️ Failed to append to Google Sheet:", e)
        return False


# -------------------- Routes --------------------
@app.route('/')
def homepage():
    return render_template('homepage.html')

@app.route('/menu')
def menu():
    return render_template('menu.html')

@app.route('/signup')
def signup():
    return render_template("signup.html")

@app.route("/ordersummary")
def order_summary():
    return render_template("ordersummary.html")

@app.route('/cart')
def cart():
    return render_template('cart.html')

@app.route("/success")
def success():
    return render_template('success.html')


# -------------------- Auto-sync on Place Order --------------------
@app.route("/place_order", methods=["POST"])
def place_order():
    """Place order and instantly push to Google Sheets using user UID."""
    payload = request.get_json(silent=True) or {}
    user_id = payload.get("uid")  # ✅ Now coming directly from frontend

    if not user_id:
        return jsonify({"success": False, "message": "Missing user ID"}), 400

    # Fetch user info
    user_ref = db.reference(f"users/{user_id}")
    user_info = user_ref.get() or {}
    customer_name = user_info.get("name", "")
    phone = user_info.get("phone", "")
    address = user_info.get("address", "")

    # Get order history (latest order)
    orders_ref = db.reference(f"orders/{user_id}/history")
    all_orders = orders_ref.get() or {}

    if not all_orders:
        return jsonify({"success": False, "message": "No orders found"}), 400

    # Get the most recent order
    latest_order = list(all_orders.values())[-1]
    items = latest_order.get("items", [])
    timestamp = latest_order.get("timestamp", time.strftime("%Y-%m-%d %H:%M:%S"))

    if not items:
        return jsonify({"success": False, "message": "No items found"}), 400

    # Create rows for Google Sheets
    rows = [
        [user_id, customer_name, phone, address,
         item.get("name"), item.get("quantity"), item.get("price"), "", timestamp]
        for item in items
    ]

    append_rows_to_sheet(rows)

    return jsonify({"success": True, "message": "Order placed and synced!"})


# -------------------- Auto-sync on Quick Order --------------------
@app.route("/quick_order", methods=["POST"])
def quick_order():
    """Instantly push quick order to Google Sheets."""
    payload = request.get_json(silent=True) or {}
    user_id = payload.get("uid")

    if not user_id:
        return jsonify({"success": False, "message": "Missing user ID"}), 400

    # Fetch user info
    user_ref = db.reference(f"users/{user_id}")
    user_info = user_ref.get() or {}
    customer_name = user_info.get("name", "")
    phone = user_info.get("phone", "")
    address = user_info.get("address", "")

    item_name = payload.get("item")
    qty = int(payload.get("quantity", 1))
    price = payload.get("price", 0)

    if not item_name:
        return jsonify({"success": False, "message": "No item provided"}), 400

    # Save order in Firebase
    order_ref = db.reference(f"orders/{user_id}/history")
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    order_ref.push({
        "items": [{"name": item_name, "quantity": qty, "price": price}],
        "timestamp": timestamp
    })

    # Add to Google Sheet
    rows = [[user_id, customer_name, phone, address,
             item_name, qty, price, "", timestamp]]
    append_rows_to_sheet(rows)

    return jsonify({"success": True, "message": "Quick order placed and synced!"})


# -------------------- Run App --------------------
if __name__ == '__main__':
    app.run(debug=True)
