import os
import json
import firebase_admin
from firebase_admin import credentials, db
import pyrebase
from flask import Flask, request, session, redirect, url_for, render_template, jsonify

# Google Sheets
from google.oauth2 import service_account
from googleapiclient.discovery import build

# -------------------- Flask App --------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "fallback-secret")

# -------------------- Firebase Setup --------------------
firebase_web_config_json = os.environ.get("FIREBASE_CONFIG")
firebase_admin_json = os.environ.get("FIREBASE_ADMIN_SDK")

if not firebase_web_config_json:
    raise RuntimeError("Missing FIREBASE_CONFIG environment variable")
if not firebase_admin_json:
    raise RuntimeError("Missing FIREBASE_ADMIN_SDK environment variable")

firebase_web_config = json.loads(firebase_web_config_json)
firebase = pyrebase.initialize_app(firebase_web_config)
pb_auth = firebase.auth()

firebase_admin_config = json.loads(firebase_admin_json)
cred = credentials.Certificate(firebase_admin_config)

if not firebase_admin._apps:
    database_url = firebase_web_config.get("databaseURL")
    if not database_url:
        raise RuntimeError("FIREBASE_CONFIG must include databaseURL")
    firebase_admin.initialize_app(cred, {"databaseURL": database_url})

# -------------------- Google Sheets Setup --------------------
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

try:
    google_creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
    if not google_creds_json:
        raise ValueError("Missing GOOGLE_SHEETS_CREDENTIALS environment variable")

    google_creds_dict = json.loads(google_creds_json)
    creds = service_account.Credentials.from_service_account_info(google_creds_dict, scopes=SCOPES)
    sheet_service = build("sheets", "v4", credentials=creds)
    sheet = sheet_service.spreadsheets()
    print("✅ Connected to Google Sheets successfully via environment variable.")
except Exception as e:
    print("⚠️ Google Sheets connection failed:", e)
    sheet = None

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

# -------------------- Auth --------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get("email")
        password = request.form.get("password")
        try:
            user = pb_auth.sign_in_with_email_and_password(email, password)
            info = pb_auth.get_account_info(user['idToken'])
            uid = info['users'][0]['localId']
            session["user_id"] = uid
            return redirect(url_for("dashboard"))
        except Exception as e:
            print("❌ Login error:", e)
            return render_template("login.html", error="Invalid email or password")
    return render_template("login.html")

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for("homepage"))

# -------------------- Cart --------------------
@app.route("/add_to_cart", methods=["POST"])
def add_to_cart():
    if "user_id" not in session:
        return jsonify({"success": False, "message": "User not logged in"}), 401

    payload = request.get_json(silent=True) or {}
    item_name = payload.get("item")
    qty = int(payload.get("quantity", 1))
    price = payload.get("price", 0)

    if not item_name:
        return jsonify({"success": False, "message": "No item provided"}), 400

    user_id = session["user_id"]
    cart_ref = db.reference(f"carts/{user_id}")
    cart = cart_ref.get() or []
    cart.append({"name": item_name, "quantity": qty, "price": price})
    cart_ref.set(cart)

    return jsonify({"success": True, "message": f"{item_name} added to cart"}), 200

# -------------------- Dashboard --------------------
@app.route('/dashboard')
def dashboard():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("signup"))
    return render_template('dashboard.html')

# -------------------- Helper: Write to Google Sheets --------------------
def append_rows_to_sheet(rows):
    """rows: list of lists"""
    if not sheet or not SPREADSHEET_ID:
        print("⚠️ Sheets not configured (sheet or SPREADSHEET_ID missing)")
        return False
    try:
        sheet.values().append(
            spreadsheetId=SPREADSHEET_ID,
            range="Sheet1!A1",
            valueInputOption="RAW",
            body={"values": rows}
        ).execute()
        return True
    except Exception as e:
        print("⚠️ Failed to append to Google Sheet:", e)
        return False

# -------------------- Place Order --------------------
@app.route("/place_order", methods=["POST"])
def place_order():
    if "user_id" not in session:
        return jsonify({"success": False, "message": "User not logged in"}), 401

    user_id = session["user_id"]
    cart_ref = db.reference(f"carts/{user_id}")
    items = cart_ref.get() or []

    if not items:
        return jsonify({"success": False, "message": "Cart is empty"}), 400

    order_ref = db.reference(f"orders/{user_id}")
    new_order_ref = order_ref.push()
    new_order_ref.set({"items": items})

    cart_ref.set([])

    rows = [[user_id, item.get("name"), item.get("quantity"), item.get("price")] for item in items]
    if append_rows_to_sheet(rows):
        print(f"✅ Order for {user_id} added to Google Sheet.")
    else:
        print(f"⚠️ Failed to add order for {user_id} to Google Sheet.")

    return jsonify({"success": True, "message": "Order placed successfully"})

# -------------------- Dev Login --------------------
@app.route("/devlogin/<uid>")
def devlogin(uid):
    session["user_id"] = uid
    return redirect(url_for("success"))

# -------------------- Quick Order --------------------
@app.route("/quick_order", methods=["POST"])
def quick_order():
    if "user_id" not in session:
        return jsonify({"success": False, "message": "User not logged in"}), 401

    user_id = session["user_id"]
    data = request.get_json(silent=True) or {}
    item_name = data.get("item")
    qty = int(data.get("quantity", 1))
    price = data.get("price", 0)

    if not item_name:
        return jsonify({"success": False, "message": "No item provided"}), 400

    order_ref = db.reference(f"orders/{user_id}")
    new_order_ref = order_ref.push()
    new_order_ref.set({
        "items": [{"name": item_name, "quantity": qty, "price": price}]
    })

    if append_rows_to_sheet([[user_id, item_name, qty, price]]):
        print(f"✅ Quick order for {user_id} added to Google Sheet.")
    else:
        print(f"⚠️ Failed to log quick order for {user_id} to Google Sheet.")

    return jsonify({"success": True, "message": f"Order placed for {item_name}"}), 200

# -------------------- Sync Old Firebase Orders --------------------
@app.route("/sync_old_data", methods=["GET"])
def sync_old_data():
    """
    Sync existing Firebase Realtime DB orders:
    orders -> user_id -> history -> order_id -> items
    """
    try:
        orders_ref = db.reference("orders")
        all_orders = orders_ref.get()

        if not all_orders:
            return jsonify({"message": "No existing orders found"}), 404

        rows = [["User ID", "Customer Name", "Phone", "Address", "Item", "Quantity", "Price", "Total", "Timestamp"]]

        for user_id, user_data in all_orders.items():
            history = user_data.get("history", {})
            if not isinstance(history, dict):
                continue

            for order_id, order_data in history.items():
                address = order_data.get("address", "")
                customer_name = order_data.get("name", "")
                phone = order_data.get("phone", "")
                timestamp = order_data.get("timestamp", "")
                total = order_data.get("total", "")

                items = order_data.get("items", [])
                if isinstance(items, dict):
                    items = items.values()

                for item in items:
                    item_name = item.get("name", "")
                    price = item.get("price", "")
                    qty = item.get("quantity", "")
                    rows.append([user_id, customer_name, phone, address, item_name, qty, price, total, timestamp])

        if len(rows) <= 1:
            return jsonify({"message": "No data found to sync"}), 200

        success = append_rows_to_sheet(rows)
        if success:
            return jsonify({"success": True, "message": f"{len(rows)-1} records synced."}), 200
        else:
            return jsonify({"success": False, "message": "Failed to write to sheet"}), 500

    except Exception as e:
        print("⚠️ Sync failed:", e)
        return jsonify({"success": False, "error": str(e)}), 500

# -------------------- Run App --------------------
if __name__ == '__main__':
    app.run(debug=True)
