import os
import json
import firebase_admin
from firebase_admin import credentials, db
import pyrebase
from flask import Flask, request, session, redirect, url_for, render_template, jsonify

# ✅ Added imports for Google Sheets
from google.oauth2 import service_account
from googleapiclient.discovery import build

# -------------------- Flask App --------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "fallback-secret")

# -------------------- Firebase Setup --------------------
firebase_web_config = json.loads(os.environ["FIREBASE_CONFIG"])
firebase = pyrebase.initialize_app(firebase_web_config)
pb_auth = firebase.auth()

firebase_admin_config = json.loads(os.environ["FIREBASE_ADMIN_SDK"])
cred = credentials.Certificate(firebase_admin_config)

if not firebase_admin._apps:
    firebase_admin.initialize_app(cred, {
        "databaseURL": firebase_web_config["databaseURL"]
    })

# ✅ Google Sheets Setup (Using environment variable)
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
  # e.g., from sheet URL
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

try:
    google_creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
    if not google_creds_json:
        raise ValueError("Missing GOOGLE_SHEETS_CREDENTIALS environment variable")

    google_creds_dict = json.loads(google_creds_json)
    creds = service_account.Credentials.from_service_account_info(
        google_creds_dict, scopes=SCOPES
    )

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
    return render_template("success.html")

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

    cart_ref.set([])  # clear cart

    # ✅ Push to Google Sheets
    if sheet:
        try:
            values = []
            for item in items:
                values.append([
                    user_id,
                    item.get("name"),
                    item.get("quantity"),
                    item.get("price")
                ])
            sheet.values().append(
                spreadsheetId=SPREADSHEET_ID,
                range="Sheet1!A1",
                valueInputOption="RAW",
                body={"values": values}
            ).execute()
            print(f"✅ Order for {user_id} added to Google Sheet.")
        except Exception as e:
            print("⚠️ Failed to add to Google Sheet:", e)

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

    # ✅ Also log quick order to Google Sheet
    if sheet:
        try:
            sheet.values().append(
                spreadsheetId=SPREADSHEET_ID,
                range="Sheet1!A1",
                valueInputOption="RAW",
                body={"values": [[user_id, item_name, qty, price]]}
            ).execute()
            print(f"✅ Quick order for {user_id} added to Google Sheet.")
        except Exception as e:
            print("⚠️ Failed to log quick order:", e)

    return jsonify({"success": True, "message": f"Order placed for {item_name}"}), 200

# -------------------- Run App --------------------
if __name__ == '__main__':
    app.run(debug=True)
