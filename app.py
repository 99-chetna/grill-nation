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
    """Generate recommendations for a user (name + price)."""
    orders_ref = db.reference("orders")
    orders = orders_ref.get() or {}

    # Build user -> items purchased mapping
    user_items = {}
    for uid, user_orders in (orders.items() if isinstance(orders, dict) else []):
        items_bought = []
        if isinstance(user_orders, dict):
            for _, details in user_orders.items():
                if not isinstance(details, dict):
                    continue
                items = details.get("items")
                # handle list
                if isinstance(items, list):
                    for it in items:
                        if isinstance(it, dict) and "name" in it:
                            items_bought.append(it["name"].strip())
                # handle dict (in case items are stored differently)
                elif isinstance(items, dict):
                    for _, it in items.items():
                        if isinstance(it, dict) and "name" in it:
                            items_bought.append(it["name"].strip())
        if items_bought:
            user_items[uid] = items_bought

    # --- collaborative filtering ---
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

    # --- fallback to popular items ---
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
        name_l = name.strip().lower()
        if isinstance(menu_data, dict):
            # direct key match
            if name_l in (k.strip().lower() for k in menu_data.keys()):
                for key, value in menu_data.items():
                    if key.strip().lower() == name_l:
                        return {"name": key, "price": value.get("price")}
            # look inside lists
            for _, value in menu_data.items():
                if isinstance(value, list):
                    for it in value:
                        if isinstance(it, dict) and it.get("name", "").strip().lower() == name_l:
                            return {"name": it.get("name"), "price": it.get("price")}
        return {"name": name, "price": None}

    # ✅ Debug prints go here, inside the function
    print("✅ User items mapping:", user_items)
    print("✅ Recommendations raw:", recommendations)

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
        user_id = request.form.get("user_id")
        if user_id:
            session["user_id"] = user_id
            return redirect(url_for("dashboard"))
        return redirect(url_for("signup"))
    return render_template("login.html")


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

    # Append new item
    cart.append({"name": item_name, "quantity": qty, "price": price})
    cart_ref.set(cart)

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
@app.route("/place_order", methods=["POST"])
def place_order():
    if "user_id" not in session:
        return jsonify({"success": False, "message": "User not logged in"}), 401

    user_id = session["user_id"]
    cart_ref = db.reference(f"carts/{user_id}")
    items = cart_ref.get() or []

    if not items:
        return jsonify({"success": False, "message": "Cart is empty"}), 400

    # Save new order into Firebase
    order_ref = db.reference(f"orders/{user_id}")
    new_order_ref = order_ref.push()
    new_order_ref.set({"items": items})

    # ✅ Clear cart
    cart_ref.set([])

    return jsonify({"success": True, "message": "Order placed successfully"})

# -------------------- recommendtion route --------------------

@app.route("/api/recommendations")
def api_recommendations():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify([])
    return jsonify(generate_recommendations(user_id))


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

    # Save new order directly in Firebase
    order_ref = db.reference(f"orders/{user_id}")
    new_order_ref = order_ref.push()
    new_order_ref.set({
        "items": [{"name": item_name, "quantity": qty, "price": price}]
    })

    return jsonify({"success": True, "message": f"Order placed for {item_name}"}), 200




# -------------------- Run App --------------------
if __name__ == '__main__':
    app.run(debug=True)
