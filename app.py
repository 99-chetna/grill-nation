import os
import json
import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity
import firebase_admin
from firebase_admin import credentials, db
import pyrebase
from flask import Flask, request, session, redirect, url_for, render_template, jsonify

# -------------------- Flask App --------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "fallback-secret")

# -------------------- Firebase Setup --------------------
# Pyrebase (Auth only)
firebase_web_config = json.loads(os.environ["FIREBASE_CONFIG"])
firebase = pyrebase.initialize_app(firebase_web_config)
pb_auth = firebase.auth()

# Admin SDK (Realtime DB, Storage, Firestore)
firebase_admin_config = json.loads(os.environ["FIREBASE_ADMIN_SDK"])
cred = credentials.Certificate(firebase_admin_config)

if not firebase_admin._apps:  # ✅ prevents multiple initializations
    firebase_admin.initialize_app(cred, {
        "databaseURL": firebase_web_config["databaseURL"]
    })

# -------------------- Recommendation Logic --------------------
def generate_recommendations(user_id: str):
    """Generate recommendations for a user (name + price)."""
    # ---------------- Orders ----------------
    orders_ref = db.reference("orders")
    orders = orders_ref.get() or {}
    print("📦 Orders from DB:", orders)

    # Build user -> items mapping
    user_items = {}
    for uid, user_orders in (orders.items() if isinstance(orders, dict) else []):
        items_bought = []
        if isinstance(user_orders, dict):
            for _, details in user_orders.items():
                if not isinstance(details, dict):
                    continue
                items = details.get("items")
                if isinstance(items, list):
                    for it in items:
                        if isinstance(it, dict) and "name" in it:
                            items_bought.append(it["name"].strip())
                elif isinstance(items, dict):
                    for _, it in items.items():
                        if isinstance(it, dict) and "name" in it:
                            items_bought.append(it["name"].strip())
        if items_bought:
            user_items[uid] = items_bought

    print("👤 User → Items mapping:", user_items)

    # ---------------- Collaborative Filtering ----------------
    recommendations = []
    if user_id in user_items and user_items[user_id]:
        all_items = sorted({name for items in user_items.values() for name in items})
        if all_items:
            df = pd.DataFrame(0, index=list(user_items.keys()), columns=all_items, dtype=int)
            for uid, items in user_items.items():
                for name in items:
                    if name in df.columns:
                        df.at[uid, name] = 1

            if df.loc[user_id].sum() > 0:
                sim_matrix = cosine_similarity(df.values)
                sim_df = pd.DataFrame(sim_matrix, index=df.index, columns=df.index)

                similar_users = sim_df[user_id].drop(user_id).sort_values(ascending=False)
                print("🔗 Similar users:", similar_users.to_dict())

                user_history = set(user_items[user_id])
                candidate_scores = {}
                for other_uid, sim_score in similar_users.items():
                    if sim_score <= 0:
                        continue
                    for item_name in user_items.get(other_uid, []):
                        if item_name in user_history:
                            continue
                        candidate_scores[item_name] = candidate_scores.get(item_name, 0) + float(sim_score)

                if candidate_scores:
                    recommendations = [
                        k for k, _ in sorted(candidate_scores.items(),
                                             key=lambda x: x[1], reverse=True)[:8]
                    ]

    # ---------------- Popular Fallback ----------------
    if not recommendations:
        all_counts = {}
        for items in user_items.values():
            for it in items:
                all_counts[it] = all_counts.get(it, 0) + 1
        recommendations = [k for k, _ in sorted(all_counts.items(), key=lambda x: x[1], reverse=True)[:8]]

    print("✨ Raw recommendations:", recommendations)

    # ---------------- Join with Menu ----------------
    menu_ref = db.reference("menu")
    menu_data = menu_ref.get() or {}
    print("📖 Menu data:", menu_data)

    def normalize(text: str) -> str:
        return "".join(text.lower().split())

    def find_menu_item(name: str):
        target = normalize(name)
        if isinstance(menu_data, dict):
            # Case 1: direct key match
            for key, value in menu_data.items():
                if normalize(key) == target and isinstance(value, dict):
                    return {"name": key, "price": value.get("price")}

                # Case 2: inside category list
                if isinstance(value, list):
                    for it in value:
                        if isinstance(it, dict) and normalize(it.get("name", "")) == target:
                            return {"name": it.get("name"), "price": it.get("price")}

            # Case 3: fuzzy/substring match
            for key, value in menu_data.items():
                if isinstance(value, list):
                    for it in value:
                        if isinstance(it, dict) and target in normalize(it.get("name", "")):
                            return {"name": it.get("name"), "price": it.get("price")}
        # Fallback
        return {"name": name, "price": None}

    final_recs = [find_menu_item(n) for n in recommendations]
    print("✅ Final recommendations:", final_recs)

    return final_recs

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
    user_id = session.get("user_id")
    recommendations = []
    if user_id:
        recommendations = generate_recommendations(user_id)
    return render_template("success.html", recommendations=recommendations)

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

    detailed_recommendations = generate_recommendations(user_id)
    return render_template('dashboard.html', recommendations=detailed_recommendations)

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
    return jsonify({"success": True, "message": "Order placed successfully"})

# -------------------- API --------------------
@app.route("/api/recommendations", methods=["GET"])
def api_recommendations():
    """API endpoint to fetch recommendations for the logged-in user."""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "User not logged in", "recommendations": []}), 401

    try:
        recs = generate_recommendations(user_id)
        return jsonify({"user_id": user_id, "recommendations": recs}), 200
    except Exception as e:
        print("❌ Error generating recommendations:", str(e))
        return jsonify({"error": "Failed to generate recommendations", "details": str(e)}), 500


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

    return jsonify({"success": True, "message": f"Order placed for {item_name}"}), 200

@app.route("/api/recommendations/<test_user>")
def api_recommendations_test(test_user):
    return jsonify(generate_recommendations(test_user))


# -------------------- Run App --------------------
if __name__ == '__main__':
    app.run(debug=True)
