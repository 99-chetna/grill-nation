from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from firebase_admin import credentials, db, initialize_app
import os, json, tempfile

# ML libs used only inside /dashboard; keeping import here is fine
import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity

app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = "your_secret_key_here"   # needed for session

# Firebase Initialization (unchanged)
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


# -------------------- Your existing routes (unchanged) --------------------
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
# -------------------------------------------------------------------------


# Example login route (replace with real Firebase Auth handshake as needed)
@app.route('/login', methods=['POST'])
def login():
    user_id = request.form.get("user_id")   # assume Firebase returns uid
    if user_id:
        session["user_id"] = user_id
        return redirect(url_for("dashboard"))
    return redirect(url_for("signup"))


# Add-to-cart API so the “Add to Cart” button on recommendations works
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


# -------------------- Dashboard with Collaborative Filtering --------------------
@app.route('/dashboard')
def dashboard():
    user_id = session.get("user_id")   # show only for logged-in users
    if not user_id:
        return redirect(url_for("signup"))

    # 1) Pull orders from Firebase: expected structure:
    # orders/{uid}/{order_id}/items -> [ {"name": "...", "price": 123}, ... ]
    orders_ref = db.reference("orders")
    orders = orders_ref.get() or {}

    # Build user -> list of item names they purchased
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

    # If the current user has no history, return empty recommendations
    if user_id not in user_items or len(user_items[user_id]) == 0:
        return render_template("dashboard.html", recommendations=[])

    # 2) Create user–item binary matrix (rows=users, cols=items)
    all_items = sorted({name for items in user_items.values() for name in items})
    if not all_items:
        return render_template("dashboard.html", recommendations=[])

    df = pd.DataFrame(0, index=list(user_items.keys()), columns=all_items, dtype=int)
    for uid, items in user_items.items():
        for name in items:
            if name in df.columns:
                df.at[uid, name] = 1

    # Guard if user row missing (shouldn’t happen given above checks)
    if user_id not in df.index:
        return render_template("dashboard.html", recommendations=[])

    # If row is all zeros (no purchases), nothing to recommend
    if df.loc[user_id].sum() == 0:
        return render_template("dashboard.html", recommendations=[])

    # 3) Compute user–user cosine similarity
    sim_matrix = cosine_similarity(df.values)
    sim_df = pd.DataFrame(sim_matrix, index=df.index, columns=df.index)

    # 4) Rank similar users (exclude self)
    similar_users = (
        sim_df[user_id]
        .drop(user_id)
        .sort_values(ascending=False)
    )

    # 5) Aggregate candidate items from top similar users that the user hasn't purchased
    user_history = set(user_items[user_id])
    candidate_scores = {}  # item -> weighted score by similarity

    for other_uid, sim_score in similar_users.items():
        if sim_score <= 0:
            continue
        for item_name in user_items.get(other_uid, []):
            if item_name in user_history:
                continue
            # Weighted by similarity (simple, effective)
            candidate_scores[item_name] = candidate_scores.get(item_name, 0) + float(sim_score)

    if not candidate_scores:
        return render_template("dashboard.html", recommendations=[])

    # Sort candidates by score (desc) and keep top-N
    top_items = [k for k, _ in sorted(candidate_scores.items(), key=lambda x: x[1], reverse=True)[:8]]

    # 6) Join with menu to get price/image/description
    # Expected menu structures supported:
    #   a) { "Burgers": [ { "name": "...", "price": ..., "image": "...", "description": "..."}, ...], ... }
    #   b) { "Original Whopper Veg": { "price": ..., "image": "...", "description": "..."}, ... }  (flat)
    menu_ref = db.reference("menu")
    menu_data = menu_ref.get() or {}

    def find_menu_item(name: str):
        # flat dict style
        if isinstance(menu_data, dict) and name in menu_data and isinstance(menu_data[name], dict):
            d = menu_data[name]
            return {
                "name": name,
                "price": d.get("price"),
                "image": d.get("image"),
                "description": d.get("description", "")
            }
        # category -> list style
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
        # fallback if not found in menu
        return {"name": name, "price": None, "image": None, "description": ""}

    detailed_recommendations = [find_menu_item(n) for n in top_items]

    return render_template('dashboard.html', recommendations=detailed_recommendations)



if __name__ == '__main__':
    app.run(debug=True)
