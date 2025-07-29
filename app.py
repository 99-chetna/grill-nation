from flask import Flask, render_template, request, redirect, url_for, jsonify
from firebase_admin import credentials, db, initialize_app
from collections import Counter
import os
import json
import tempfile

# Initialize Flask
app = Flask(__name__, template_folder='templates', static_folder='static')

# Firebase Initialization using environment variable
firebase_config = os.environ.get('FIREBASE_CONFIG')

if firebase_config:
    firebase_dict = json.loads(firebase_config)

    # Fix the private key newlines
    if "private_key" in firebase_dict:
        firebase_dict["private_key"] = firebase_dict["private_key"].replace("\\n", "\n")

    # Write to temporary file
    with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.json') as temp_file:
        json.dump(firebase_dict, temp_file)
        temp_file_path = temp_file.name

    # Initialize Firebase
    cred = credentials.Certificate(temp_file_path)
    initialize_app(cred, {
        'databaseURL': 'https://recommendation-system-27e6b-default-rtdb.firebaseio.com/'
    })
else:
    raise RuntimeError("FIREBASE_CONFIG environment variable is not set.")


# ------------------- ROUTES -------------------

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

@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.html')

@app.route('/cart')
def cart():
    return render_template('cart.html')


# ------------------- RECOMMENDATION ROUTE -------------------
@app.route("/recommendations", methods=["GET"])
def get_recommendations():
    uid = request.args.get("uid")
    if not uid:
        return jsonify({"error": "User ID required"}), 400

    user_ref = db.reference(f"orders/{uid}/history")
    user_orders = user_ref.get()

    if not user_orders:
        # Handle new user: recommend globally popular items
        all_orders = db.reference("orders").get() or {}
        global_counts = Counter()
        for user_data in all_orders.values():
            history = user_data.get("history", {})
            for order in history.values():
                items = order.get("items", {})
                global_counts.update(items)
        popular_items = [item for item, _ in global_counts.most_common(3)]
        return jsonify({"recommendations": popular_items})

    # Count frequency of items for this user
    user_counts = Counter()
    for order in user_orders.values():
        items = order.get("items", {})
        user_counts.update(items)

    top_items = [item for item, _ in user_counts.most_common(3)]
    return jsonify({"recommendations": top_items})


# ------------------- MAIN -------------------
if __name__ == '__main__':
    app.run(debug=True)
