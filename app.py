from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from firebase_admin import credentials, db, initialize_app
import os, json, tempfile

# ML libs used only inside /dashboard
import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity

app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = "your_secret_key_here"   # needed for session

# Firebase Initialization
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


# -------------------- Your existing routes --------------------
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
# ----------------------------------------------------------------


@app.route('/login', methods=['POST'])
def login():
    user_id = request.form.get("user_id")
    if user_id:
        session["user_id"] = user_id
        return redirect(url_for("dashboard"))
    return redirect(url_for("signup"))


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


# -------------------- Dashboard with Collaborative Filtering + Popular Fallback --------------------
@app.route('/dashboard')
def dashboard():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("signup"))

    orders_ref = db.reference("orders")
    orders = orders_ref.get() or {}

    # Build user -> items purchased
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

    # --- collaborative filtering part ---
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
                    recommendations = [k for k, _ in sorted(candidate_scores.items(), key=lambda x: x[1], reverse=True)[:8]]

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
        if isinstance(menu_data, dict) and name in menu_data and isinstance(menu_data[name], dict):
            d = menu_data[name]
            return {
                "name": name,
                "price": d.get("price"),
                "image": d.get("image"),
                "description": d.get("description", "")
            }
        for cat, items in (menu_data.items() if isinstance(menu_data, dict) else []):
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, dict) and it.get("name") == name:
                        return {
                            "name": it.get("name"),
                            "price": it.get("price"),
                            "image": it.get("image"),
                            "description": it.get("description", "")
                        }
        return {"name": name, "price": None, "image": None, "description": ""}

    detailed_recommendations = [find_menu_item(n) for n in recommendations]

    return render_template('dashboard.html', recommendations=detailed_recommendations)


if __name__ == '__main__':
    app.run(debug=True)
