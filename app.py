from flask import Flask, render_template, request, redirect, url_for
from firebase_admin import credentials, db, initialize_app
import os

# Initialize Flask
app = Flask(__name__, template_folder='templates', static_folder='static')

# Firebase Initialization
cred = credentials.Certificate("firebaseconfig.json")
initialize_app(cred, {
    'databaseURL': 'https://recommendation-system-27e6b-default-rtdb.firebaseio.com/'
})

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


if __name__ == '__main__':
   
    app.run(debug=True)