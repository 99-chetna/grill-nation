from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from firebase_admin import credentials, db, initialize_app
import os, json, tempfile

# ML libs
import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity

app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = "f4d9b1c6b0f2479bbf1f38f847aab23e41f2dc0a62291f4a31a1e4c6e32bcb10"   # needed for session

# -------------------- Firebase Initialization --------------------
firebase_config = os.environ.get('FIREBASE_CONFIG')

if firebase_config:
    firebase_dict = json.loads(firebase_config)
    if "private_key" in firebase_dict:
        firebase_dict["private_key"] = firebase_dict["private_key"].replace("\\n", "\n")

    with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.json') as temp_file:
        json.dump(firebase_dict, temp_file)
        temp_file_path = temp_file.name

    cred = credentials.Certificate(temp_file_path)
    initialize_app(cred, {
        'databaseURL': 'https://recommendation-system-27e6b-default-rtdb.firebaseio.com/'
    })
else:
    raise RuntimeError("FIREBASE_CONFIG environment variable is not set.")


# -------------------- Helper: Recommendation Logic --------------------
def generate_recommendations(user_id: str):
    """Generate detailed recommendations for a user."""
    orders_ref = db.reference("orders")
    orders = orders_ref.get() or {}

    # Build user -> items purchased mapping
    user_items = {}
    for uid, user_orders in (orders.items() if isinstance(orders, dict) else []):
        items_bought = []
        if isinstance(user_orders, dict):
            for _, details in user_orders.items():
                if isinstance(details, dict) and "items" in details and isinstance(details["items"], list):
                    for it in details["items"]:
                        name = (it.get("name") if isinstance(it, dict) else None)
                        if name:
                            items_bought.append(name)
        user_items[uid] = items_bought

    # --- collaborative filtering ---
    recommendations = []
    if user_id in user_items and len(user_items[user_id]) > 0:
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
                    recommendations = [k for k, _ in sorted(candidate_scores.items(),
                                                           key=lambda x: x[1], reverse=True)[:8]]

    # --- fallback to popular items if no recommendations ---
    if not recommendations:
        all_counts = {}
        for items in user_items.values():
            for it in items:
                all_counts[it] = all_counts.get(it, 0) + 1
        recommendations = [k for k, _ in sorted(all_counts.items(), key=lambda x: x[1], reverse=True)[:8]]

    # --- join with menu ---
    menu_ref = db.reference("menu")
    menu_data = menu_ref.get() or {}

    def find_menu_item(name: str):
        name = name.strip().lower()
        if isinstance(menu_data, dict):
            for key, value in menu_data.items():
                if isinstance(value, dict) and key.strip().lower() == name:
                    return {
                        "name": key,
                        "price": value.get("price"),
                        "image": value.get("image"),
                        "description": value.get("description", "")
                    }
                if isinstance(value, list):
                    for it in value:
                        if isinstance(it, dict) and it.get("name", "").strip().lower() == name:
                            return {
                                "name": it.get("name"),
                                "price": it.get("price"),
                                "image": it.get("image"),
                                "description": it.get("description", "")
                            }
        return {"name": name.title(), "price": None, "image": None, "description": ""}

    return [find_menu_item(n) for n in recommendations]


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


# -------------------- Auth --------------------
@app.route('/login', methods=['POST'])
def login():
    user_id = request.form.get("user_id")
    if user_id:
        session["user_id"] = user_id
        return redirect(url_for("dashboard"))
    return redirect(url_for("signup"))


# -------------------- Cart --------------------
@app.route("/add_to_cart", methods=["POST"])
def add_to_cart():
    if "user_id" not in session:
        return jsonify({"success": False, "message": "User not logged in"}), 401

    payload = request.get_json(silent=True) or {}
    item_name = payload.get("item")
    qty = int(payload.get("quantity", 1))

    if not item_name:
        return jsonify({"success": False, "message": "No item provided"}), 400

    user_id = session["user_id"]
    cart_ref = db.reference(f"carts/{user_id}/{item_name}")

    current = cart_ref.get()
    if current and isinstance(current, dict) and "quantity" in current:
        cart_ref.update({"quantity": int(current["quantity"]) + qty})
    else:
        cart_ref.set({"quantity": qty})

    return jsonify({"success": True, "message": f"{item_name} added to cart"}), 200


# -------------------- Dashboard with Recommendations --------------------
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
    data = request.get_json(silent=True) or {}
    items = data.get("items", [])

    if not items:
        return jsonify({"success": False, "message": "Cart is empty"}), 400

    # Save new order into Firebase
    order_ref = db.reference(f"orders/{user_id}")
    new_order_ref = order_ref.push()
    new_order_ref.set({"items": items})

    return jsonify({"success": True, "message": "Order placed successfully"})
                

# -------------------- Recommendations API --------------------
@app.route("/recommendations")
def recommendations():
    if "user_id" not in session:
        return jsonify([])

    user_id = session["user_id"]
    detailed_recommendations = generate_recommendations(user_id)
    return jsonify(detailed_recommendations)


# -------------------- Run App --------------------
if __name__ == '__main__':
    app.run(debug=True)
