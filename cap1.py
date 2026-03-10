# ============================================================
# GRAMIN SMARTCARE  - Rural Pharmacy Management System
# ============================================================

import os
import json
import random
import base64
import re
import requests
from datetime import datetime, timedelta, timezone
from functools import wraps
from math import radians, cos, sin, asin, sqrt
from flask import (
    Flask, render_template_string, redirect, url_for,
    flash, request, jsonify, session, render_template
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import func, desc
from jinja2 import BaseLoader, TemplateNotFound as Jinja2TemplateNotFound

import firebase_admin
from firebase_admin import credentials, auth as firebase_auth, firestore

# ============================================================
# APP CONFIGURATION
# ============================================================

app = Flask(__name__)
app.config['SECRET_KEY'] = 'gramin-smartcare-secret-2024'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///pharmacy.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Firebase Configuration
FIREBASE_CONFIG = {
    "apiKey": "Api key here",
    "authDomain": "gramin-smartcare.firebaseapp.com",
    "projectId": "gramin-smartcare",
    "storageBucket": "gramin-smartcare.firebasestorage.app",
    "messagingSenderId": "73081284160",
    "appId": "1:73081284160:web:ef711986a83dcc00e861a5",
    "measurementId": "G-BTQNK50HH8"
}

GEMINI_API_KEY = "AIzaSyB_5vE2M_7M31ZiHCgkBlXv1vpKcuYWd9Y"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-lite:generateContent"

OPENROUTER_API_KEY = "Enter your api key here"

# Initialize Firebase Admin SDK
try:
    if not firebase_admin._apps:
        if os.path.exists('firebase-service-account.json'):
            cred = credentials.Certificate('firebase-service-account.json')
            firebase_admin.initialize_app(cred)
        else:
            firebase_admin.initialize_app(options={'projectId': 'gramin-smartcare'})
    firestore_db = firestore.client()
except Exception as e:
    print(f"⚠️ Firebase Admin init warning: {e}")
    firestore_db = None

db = SQLAlchemy(app)

# ============================================================
# DATABASE MODELS
# ============================================================

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    firebase_uid = db.Column(db.String(128), unique=True, nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    full_name = db.Column(db.String(150))
    phone = db.Column(db.String(20))
    organization = db.Column(db.String(150))
    address = db.Column(db.Text)
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_authenticated(self):
        return True

    @property
    def is_active(self):
        return True

    def get_id(self):
        return str(self.id)


class Category(db.Model):
    __tablename__ = 'categories'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.Text)
    icon = db.Column(db.String(50))
    products = db.relationship('Product', backref='category', lazy=True)


class Product(db.Model):
    __tablename__ = 'products'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=False)
    description = db.Column(db.Text)
    unit_price = db.Column(db.Float, nullable=False)
    current_stock = db.Column(db.Integer, default=0)
    minimum_stock = db.Column(db.Integer, default=10)
    maximum_stock = db.Column(db.Integer, default=500)
    unit = db.Column(db.String(30), default='units')
    manufacturer = db.Column(db.String(150))
    expiry_date = db.Column(db.Date)
    is_prescription = db.Column(db.Boolean, default=False)
    added_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    sales = db.relationship('SalesRecord', backref='product', lazy=True)

    @property
    def stock_status(self):
        if self.current_stock <= 0:
            return 'out_of_stock'
        elif self.current_stock <= self.minimum_stock:
            return 'critical'
        elif self.current_stock <= self.minimum_stock * 2:
            return 'low'
        return 'adequate'

    @property
    def expiry_status(self):
        if not self.expiry_date:
            return 'unknown'
        days_left = (self.expiry_date - datetime.now(timezone.utc).date()).days
        if days_left <= 0:
            return 'expired'
        elif days_left <= 30:
            return 'expiring_soon'
        elif days_left <= 90:
            return 'expiring_3months'
        return 'valid'

    @property
    def days_until_expiry(self):
        if not self.expiry_date:
            return 999
        return (self.expiry_date - datetime.now(timezone.utc).date()).days

    @property
    def predicted_stock_days(self):
        recent = SalesRecord.query.filter_by(product_id=self.id).filter(
            SalesRecord.sale_date >= datetime.now(timezone.utc) - timedelta(days=30)
        ).all()
        if not recent:
            return 999
        total = sum(s.quantity for s in recent)
        daily = total / 30
        if daily == 0:
            return 999
        return int(self.current_stock / daily)


class SalesRecord(db.Model):
    __tablename__ = 'sales_records'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    total_price = db.Column(db.Float, nullable=False)
    sale_date = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    buyer_type = db.Column(db.String(50))


class Order(db.Model):
    __tablename__ = 'orders'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    pharmacist_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    status = db.Column(db.String(30), default='pending')
    order_type = db.Column(db.String(20), default='new')
    is_monthly = db.Column(db.Boolean, default=False)
    total_amount = db.Column(db.Float, default=0)
    order_date = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    prescription_data = db.Column(db.Text)
    notes = db.Column(db.Text)
    user = db.relationship('User', foreign_keys=[user_id], backref='orders_placed')
    pharmacist = db.relationship('User', foreign_keys=[pharmacist_id], backref='orders_received')
    items = db.relationship('OrderItem', backref='order', lazy=True)


class OrderItem(db.Model):
    __tablename__ = 'order_items'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    total_price = db.Column(db.Float, nullable=False)
    product = db.relationship('Product', backref='order_items')


class Customer(db.Model):
    __tablename__ = 'customers'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    is_monthly = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    added_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    bills = db.relationship('Bill', backref='customer', lazy=True)
    monthly_medicines = db.relationship('MonthlyMedicine', backref='customer', lazy=True)


class Bill(db.Model):
    __tablename__ = 'bills'
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=True)
    bill_date = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    total_amount = db.Column(db.Float, default=0)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    items = db.relationship('BillItem', backref='bill', lazy=True)
    pharmacist = db.relationship('User', foreign_keys=[created_by], backref='bills_created')


class BillItem(db.Model):
    __tablename__ = 'bill_items'
    id = db.Column(db.Integer, primary_key=True)
    bill_id = db.Column(db.Integer, db.ForeignKey('bills.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    total_price = db.Column(db.Float, nullable=False)
    product = db.relationship('Product', backref='bill_items')


class MonthlyMedicine(db.Model):
    __tablename__ = 'monthly_medicines'
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    added_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    product = db.relationship('Product', backref='monthly_subscriptions')


class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    type = db.Column(db.String(30), default='order')
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=True)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    user = db.relationship('User', backref='notifications')

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def get_current_user():
    user_id = session.get('user_id')
    if user_id:
        return db.session.get(User, user_id)
    return None


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = get_current_user()
        if not user:
            flash('Please login to continue.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def pharmacist_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = get_current_user()
        if not user or user.role != 'pharmacist':
            flash('Access denied. Pharmacist only.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function


def doctor_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = get_current_user()
        if not user or user.role != 'doctor':
            flash('Access denied. Doctor only.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function


def patient_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = get_current_user()
        if not user or user.role != 'patient':
            flash('Access denied. Patient only.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function


def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    return 6371 * c


def get_nearby_pharmacies(lat, lon, radius_km=50):
    pharmacists = User.query.filter_by(role='pharmacist').filter(
        User.latitude.isnot(None), User.longitude.isnot(None)
    ).all()
    nearby = []
    for p in pharmacists:
        dist = haversine(lat, lon, p.latitude, p.longitude)
        if dist <= radius_km:
            product_count = Product.query.filter_by(added_by=p.id).filter(Product.current_stock > 0).count()
            nearby.append({
                'pharmacist': p,
                'distance': round(dist, 1),
                'product_count': product_count
            })
    nearby.sort(key=lambda x: x['distance'])
    return nearby


def analyze_prescription(image_base64):
    """Analyze prescription using OpenRouter (free)"""
    try:
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
           "model": "google/gemini-2.0-flash-001",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "You are a medical prescription reader AI. Analyze this prescription image and extract all medicines listed. For each medicine, provide: medicine name, dosage, quantity needed. Return ONLY a JSON array like this format, nothing else: [{\"name\": \"Paracetamol 500mg\", \"dosage\": \"1 tablet twice daily\", \"quantity\": 10}, ...]. If you cannot read the prescription clearly, return an empty array []. Do not include any explanation, only the JSON array."
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            }
                        }
                    ]
                }
            ]
        }

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=60
        )

        if response.status_code == 200:
            result = response.json()
            text = result['choices'][0]['message']['content'].strip()
            if text.startswith('```json'): text = text[7:]
            if text.startswith('```'): text = text[3:]
            if text.endswith('```'): text = text[:-3]
            text = text.strip()
            print("✅ Prescription analyzed successfully")
            return json.loads(text)
        else:
            print(f"⚠️ OpenRouter error: {response.status_code} - {response.text}")
            return []
    except Exception as e:
        print(f"⚠️ Prescription analysis error: {e}")
        return []


def get_unread_notification_count(user_id):
    return Notification.query.filter_by(user_id=user_id, is_read=False).count()

# ============================================================
# CSS STYLES
# ============================================================

CSS = """
:root {
    --primary: #667eea;
    --primary-dark: #5a67d8;
    --secondary: #764ba2;
    --accent: #f093fb;
    --success: #43e97b;
    --warning: #fee140;
    --danger: #fa709a;
    --dark: #1a1a2e;
    --darker: #16213e;
    --light-bg: #f0f2f5;
    --card-bg: #ffffff;
    --shadow: 0 4px 20px rgba(0,0,0,0.08);
    --shadow-hover: 0 8px 30px rgba(0,0,0,0.15);
    --radius: 16px;
    --radius-sm: 10px;
    --transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Poppins', sans-serif; background: var(--light-bg); color: #333; overflow-x: hidden; }

.navbar { background: linear-gradient(135deg, var(--dark), var(--darker)); padding: 0.5rem 1rem; box-shadow: 0 4px 20px rgba(0,0,0,0.2); z-index: 1050; }
.navbar-brand { display: flex; align-items: center; gap: 0.5rem; }
.brand-text { font-weight: 700; font-size: 1.3rem; background: linear-gradient(135deg, var(--primary), var(--accent)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.brand-sub { display: block; font-size: 0.65rem; opacity: 0.7; letter-spacing: 1px; text-transform: uppercase; margin-top: -5px; }
.nav-link { color: rgba(255,255,255,0.8) !important; font-weight: 500; font-size: 0.9rem; padding: 0.7rem 1rem !important; border-radius: 8px; transition: var(--transition); margin: 0 2px; }
.nav-link:hover { color: white !important; background: rgba(255,255,255,0.1); }
.user-avatar { width: 32px; height: 32px; border-radius: 50%; background: linear-gradient(135deg, var(--primary), var(--accent)); display: inline-flex; align-items: center; justify-content: center; color: white; font-weight: 700; font-size: 0.8rem; margin-right: 8px; }
.user-menu { display: flex !important; align-items: center; }
.btn-login { border: 1px solid rgba(255,255,255,0.3) !important; border-radius: 8px !important; }
.btn-register { background: linear-gradient(135deg, var(--primary), var(--secondary)) !important; border-radius: 8px !important; color: white !important; }

.hero-section { background: linear-gradient(135deg, #0f0c29, #302b63, #24243e); min-height: 85vh; display: flex; align-items: center; position: relative; overflow: hidden; padding: 6rem 0 4rem; }
.hero-section::before { content: ''; position: absolute; top: -50%; right: -30%; width: 80%; height: 200%; background: radial-gradient(ellipse, rgba(102,126,234,0.15), transparent 60%); animation: heroGlow 8s ease-in-out infinite; }
@keyframes heroGlow { 0%, 100% { transform: translate(0,0) scale(1); } 50% { transform: translate(-5%,5%) scale(1.1); } }
.hero-badge { display: inline-block; background: rgba(102,126,234,0.2); border: 1px solid rgba(102,126,234,0.3); color: var(--primary); padding: 0.4rem 1.2rem; border-radius: 50px; font-size: 0.85rem; font-weight: 500; margin-bottom: 1.5rem; }
.hero-title { color: white; font-size: 3.5rem; font-weight: 800; line-height: 1.2; margin-bottom: 1.5rem; }
.gradient-text { background: linear-gradient(135deg, var(--primary), var(--accent), var(--success)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-size: 200% auto; animation: textShine 3s ease-in-out infinite; }
@keyframes textShine { 0%, 100% { background-position: 0% center; } 50% { background-position: 200% center; } }
.hero-subtitle { color: rgba(255,255,255,0.7); font-size: 1.15rem; line-height: 1.8; margin-bottom: 2rem; max-width: 550px; }
.hero-buttons { display: flex; gap: 1rem; flex-wrap: wrap; }
.btn-hero-primary { background: linear-gradient(135deg, var(--primary), var(--secondary)); border: none; color: white; padding: 0.8rem 2rem; border-radius: 12px; font-weight: 600; transition: var(--transition); box-shadow: 0 4px 15px rgba(102,126,234,0.4); }
.btn-hero-primary:hover { transform: translateY(-3px); box-shadow: 0 8px 25px rgba(102,126,234,0.5); color: white; }
.btn-hero-outline { border: 2px solid rgba(255,255,255,0.3); color: white; padding: 0.8rem 2rem; border-radius: 12px; font-weight: 600; transition: var(--transition); background: transparent; text-decoration: none; }
.btn-hero-outline:hover { background: rgba(255,255,255,0.1); border-color: white; color: white; transform: translateY(-3px); }

.hero-stats-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1.2rem; }
.hero-stat-card { background: rgba(255,255,255,0.08); backdrop-filter: blur(20px); border: 1px solid rgba(255,255,255,0.12); border-radius: var(--radius); padding: 1.5rem; text-align: center; transition: var(--transition); }
.hero-stat-card:hover { transform: translateY(-5px); background: rgba(255,255,255,0.12); }
.stat-icon { width: 50px; height: 50px; border-radius: 12px; display: flex; align-items: center; justify-content: center; margin: 0 auto 0.8rem; font-size: 1.3rem; color: white; }
.bg-gradient-blue { background: linear-gradient(135deg, #667eea, #764ba2); }
.bg-gradient-green { background: linear-gradient(135deg, #43e97b, #38f9d7); }
.bg-gradient-orange { background: linear-gradient(135deg, #f093fb, #f5576c); }
.bg-gradient-purple { background: linear-gradient(135deg, #a18cd1, #fbc2eb); }
.bg-gradient-red { background: linear-gradient(135deg, #f5576c, #ff6b6b); }
.stat-number { color: white; font-size: 1.6rem; font-weight: 700; }
.stat-label { color: rgba(255,255,255,0.7); font-size: 0.8rem; margin-top: 0.3rem; }

.section-badge { display: inline-block; background: linear-gradient(135deg, rgba(102,126,234,0.1), rgba(118,75,162,0.1)); color: var(--primary); padding: 0.3rem 1rem; border-radius: 50px; font-size: 0.85rem; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 1rem; }
.section-title { font-size: 2.2rem; font-weight: 700; color: var(--dark); }
.section-desc { color: #666; max-width: 600px; margin: 0 auto; }

.feature-card { background: var(--card-bg); border-radius: var(--radius); padding: 2rem; box-shadow: var(--shadow); transition: var(--transition); border: 1px solid rgba(0,0,0,0.05); height: 100%; }
.feature-card:hover { transform: translateY(-8px); box-shadow: var(--shadow-hover); }
.feature-icon { width: 60px; height: 60px; border-radius: 16px; background: linear-gradient(135deg, rgba(102,126,234,0.1), rgba(118,75,162,0.1)); display: flex; align-items: center; justify-content: center; font-size: 1.5rem; color: var(--primary); margin-bottom: 1.2rem; }
.feature-card h4 { font-weight: 600; margin-bottom: 0.8rem; color: var(--dark); }
.feature-card p { color: #666; line-height: 1.6; margin-bottom: 0; }

.auth-container { min-height: calc(100vh - 76px); display: flex; align-items: center; padding: 2rem 0; background: linear-gradient(135deg, #f5f7fa, #c3cfe2); }
.auth-card { background: var(--card-bg); border-radius: var(--radius); padding: 2.5rem; box-shadow: var(--shadow-hover); border: 1px solid rgba(0,0,0,0.05); }
.auth-icon { width: 70px; height: 70px; border-radius: 50%; background: linear-gradient(135deg, var(--primary), var(--secondary)); display: flex; align-items: center; justify-content: center; margin: 0 auto 1rem; color: white; font-size: 1.5rem; }
.auth-header h2 { font-weight: 700; color: var(--dark); }
.auth-header p { color: #888; }
.role-selector { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 1rem; }
.role-option input[type="radio"] { display: none; }
.role-card { text-align: center; padding: 1.2rem; border: 2px solid #e0e0e0; border-radius: var(--radius-sm); cursor: pointer; transition: var(--transition); }
.role-card i { font-size: 1.8rem; color: #999; display: block; margin-bottom: 0.5rem; transition: var(--transition); }
.role-card span { display: block; font-weight: 600; color: #555; }
.role-card small { color: #999; font-size: 0.75rem; }
.role-option input:checked + .role-card { border-color: var(--primary); background: rgba(102,126,234,0.05); box-shadow: 0 0 0 3px rgba(102,126,234,0.15); }
.role-option input:checked + .role-card i { color: var(--primary); }
.auth-footer a { color: var(--primary); font-weight: 600; text-decoration: none; }

.main-content { margin-top: 56px; min-height: calc(100vh - 56px); }
.page-header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 1rem; }
.page-header h2 { font-weight: 700; color: var(--dark); margin-bottom: 0; }

.stat-card-modern { border-radius: var(--radius); padding: 0; overflow: hidden; box-shadow: var(--shadow); transition: var(--transition); color: white; }
.stat-card-modern:hover { transform: translateY(-5px); box-shadow: var(--shadow-hover); }
.stat-card-body { padding: 1.5rem; display: flex; align-items: center; gap: 1rem; }
.stat-card-icon { width: 55px; height: 55px; border-radius: 14px; background: rgba(255,255,255,0.2); display: flex; align-items: center; justify-content: center; font-size: 1.3rem; flex-shrink: 0; }
.stat-card-info h3 { font-weight: 700; font-size: 1.8rem; margin-bottom: 0; line-height: 1; }
.stat-card-info p { margin-bottom: 0; opacity: 0.9; font-size: 0.85rem; }
.stat-card-footer { padding: 0.6rem 1.5rem; background: rgba(0,0,0,0.1); }
.stat-card-footer a { color: rgba(255,255,255,0.9); text-decoration: none; font-size: 0.8rem; font-weight: 500; }

.chart-card { background: var(--card-bg); border-radius: var(--radius); box-shadow: var(--shadow); overflow: hidden; border: 1px solid rgba(0,0,0,0.05); }
.chart-header { padding: 1.2rem 1.5rem; border-bottom: 1px solid rgba(0,0,0,0.06); }
.chart-header h5 { font-weight: 600; margin-bottom: 0; color: var(--dark); font-size: 1rem; }
.chart-body { padding: 1.5rem; }

.filter-bar { background: var(--card-bg); border-radius: var(--radius); padding: 1.5rem; box-shadow: var(--shadow); }
.product-card { background: var(--card-bg); border-radius: var(--radius); padding: 1.5rem; box-shadow: var(--shadow); transition: var(--transition); border: 1px solid rgba(0,0,0,0.05); height: 100%; display: flex; flex-direction: column; }
.product-card:hover { transform: translateY(-5px); box-shadow: var(--shadow-hover); }
.product-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.8rem; }
.product-category { font-size: 0.75rem; color: #888; font-weight: 500; }
.stock-badge { font-size: 0.7rem; padding: 0.25rem 0.6rem; border-radius: 50px; font-weight: 600; }
.stock-out_of_stock { background: #fdecea; color: #d32f2f; }
.stock-critical { background: #fff3e0; color: #e65100; }
.stock-low { background: #fff8e1; color: #f9a825; }
.stock-adequate { background: #e8f5e9; color: #2e7d32; }
.product-name { font-weight: 600; color: var(--dark); font-size: 1rem; margin-bottom: 0.8rem; line-height: 1.3; }
.product-details { flex: 1; }
.detail-row { display: flex; justify-content: space-between; padding: 0.3rem 0; font-size: 0.85rem; border-bottom: 1px solid rgba(0,0,0,0.04); }
.product-actions { margin-top: auto; }

.expiry-badge { font-size: 0.7rem; padding: 0.25rem 0.6rem; border-radius: 50px; font-weight: 600; }
.expiry-expired { background: #fdecea; color: #d32f2f; }
.expiry-expiring_soon { background: #fff3e0; color: #e65100; }
.expiry-expiring_3months { background: #fff8e1; color: #f9a825; }
.expiry-valid { background: #e8f5e9; color: #2e7d32; }
.expiry-unknown { background: #f5f5f5; color: #999; }

.customer-card { background: var(--card-bg); border-radius: var(--radius); padding: 1.5rem; box-shadow: var(--shadow); transition: var(--transition); border: 1px solid rgba(0,0,0,0.05); }
.customer-card:hover { transform: translateY(-3px); box-shadow: var(--shadow-hover); }
.monthly-badge { background: linear-gradient(135deg, var(--primary), var(--secondary)); color: white; padding: 0.25rem 0.8rem; border-radius: 50px; font-size: 0.75rem; font-weight: 600; }

.notification-badge { position: absolute; top: -5px; right: -5px; background: #e74c3c; color: white; border-radius: 50%; width: 20px; height: 20px; font-size: 0.7rem; display: flex; align-items: center; justify-content: center; font-weight: 700; }
.notification-bell { position: relative; display: inline-block; }
.notification-item { padding: 1rem; border-bottom: 1px solid rgba(0,0,0,0.05); transition: var(--transition); }
.notification-item:hover { background: rgba(102,126,234,0.03); }
.notification-item.unread { border-left: 3px solid var(--primary); background: rgba(102,126,234,0.02); }

.pharmacy-card { background: var(--card-bg); border-radius: var(--radius); padding: 1.5rem; box-shadow: var(--shadow); transition: var(--transition); border: 1px solid rgba(0,0,0,0.05); }
.pharmacy-card:hover { transform: translateY(-3px); box-shadow: var(--shadow-hover); }
.distance-badge { background: linear-gradient(135deg, #43e97b, #38f9d7); color: #1a1a2e; padding: 0.25rem 0.8rem; border-radius: 50px; font-size: 0.75rem; font-weight: 600; }

.prescription-upload { border: 2px dashed rgba(102,126,234,0.4); border-radius: var(--radius); padding: 3rem 2rem; text-align: center; background: rgba(102,126,234,0.02); transition: var(--transition); cursor: pointer; }
.prescription-upload:hover { border-color: var(--primary); background: rgba(102,126,234,0.05); }
.prescription-upload.dragover { border-color: var(--primary); background: rgba(102,126,234,0.1); }
.prescription-preview { max-width: 300px; max-height: 300px; border-radius: var(--radius-sm); margin-top: 1rem; }

.order-status-badge { padding: 0.3rem 0.8rem; border-radius: 50px; font-size: 0.75rem; font-weight: 600; }
.status-pending { background: #fff3e0; color: #e65100; }
.status-confirmed { background: #e3f2fd; color: #1565c0; }
.status-ready { background: #e8f5e9; color: #2e7d32; }
.status-delivered { background: #f3e5f5; color: #7b1fa2; }
.status-cancelled { background: #fdecea; color: #d32f2f; }

.profile-avatar-lg { width: 80px; height: 80px; border-radius: 50%; background: linear-gradient(135deg, var(--primary), var(--secondary)); display: flex; align-items: center; justify-content: center; color: white; font-size: 2rem; font-weight: 700; margin: 0 auto; }

.footer { background: var(--dark); color: rgba(255,255,255,0.8); padding: 3rem 0 1.5rem; margin-top: 4rem; }
.footer h5 { color: white; font-weight: 600; margin-bottom: 1rem; }
.footer a { color: rgba(255,255,255,0.7); text-decoration: none; transition: var(--transition); }
.footer a:hover { color: var(--primary); }
.footer hr { border-color: rgba(255,255,255,0.1); }

.flash-alert { border: none; border-radius: var(--radius-sm); box-shadow: var(--shadow); animation: slideDown 0.3s ease-out; }
@keyframes slideDown { from { transform: translateY(-20px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }

.form-control, .form-select { border-radius: var(--radius-sm); border: 2px solid #e0e0e0; padding: 0.6rem 1rem; transition: var(--transition); }
.form-control:focus, .form-select:focus { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(102,126,234,0.15); }
.form-label { font-weight: 500; color: #555; font-size: 0.9rem; }
.btn-primary { background: linear-gradient(135deg, var(--primary), var(--primary-dark)); border: none; border-radius: var(--radius-sm); font-weight: 600; transition: var(--transition); }
.btn-primary:hover { transform: translateY(-2px); box-shadow: 0 4px 15px rgba(102,126,234,0.4); background: linear-gradient(135deg, var(--primary), var(--secondary)); }

.table { font-size: 0.9rem; }
.table thead th { background: rgba(102,126,234,0.05); border-bottom: 2px solid rgba(102,126,234,0.1); font-weight: 600; color: #555; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.5px; }
.table-hover tbody tr:hover { background: rgba(102,126,234,0.03); }
.empty-state { color: #999; }

.bill-item-row { display: flex; align-items: center; gap: 0.5rem; padding: 0.5rem 0; border-bottom: 1px solid rgba(0,0,0,0.05); }

.cta-card { background: linear-gradient(135deg, var(--primary), var(--secondary)); border-radius: var(--radius); padding: 4rem 2rem; color: white; }
.cta-card h2 { font-weight: 700; font-size: 2rem; margin-bottom: 0.5rem; }
.cta-card p { opacity: 0.9; font-size: 1.1rem; margin-bottom: 2rem; }

@media (max-width: 768px) {
    .hero-title { font-size: 2rem; }
    .hero-stats-grid { gap: 0.8rem; }
    .hero-stat-card { padding: 1rem; }
    .stat-number { font-size: 1.2rem; }
    .page-header { flex-direction: column; align-items: flex-start; }
    .section-title { font-size: 1.5rem; }
    .role-selector { grid-template-columns: 1fr; }
}
@media (max-width: 576px) {
    .hero-section { padding: 5rem 0 2rem; }
    .hero-title { font-size: 1.8rem; }
    .hero-buttons { flex-direction: column; }
    .hero-buttons .btn, .hero-buttons a { width: 100%; }
    .auth-card { padding: 1.5rem; }
}
::-webkit-scrollbar { width: 8px; }
::-webkit-scrollbar-track { background: #f1f1f1; }
::-webkit-scrollbar-thumb { background: #c4c4c4; border-radius: 10px; }
"""

# ============================================================
# HTML TEMPLATES
# ============================================================

BASE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title|default('Gramin SmartCare') }}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>""" + CSS + """</style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark fixed-top">
        <div class="container-fluid">
            <a class="navbar-brand" href="/">
                <i class="fas fa-clinic-medical me-2"></i>
                <span class="brand-text">Gramin SmartCare</span>
            </a>
            <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav">
                <span class="navbar-toggler-icon"></span>
            </button>
            <div class="collapse navbar-collapse" id="navbarNav">
                <ul class="navbar-nav me-auto">
                    <li class="nav-item"><a class="nav-link" href="/"><i class="fas fa-home me-1"></i>Home</a></li>
                    {% if current_user %}
                        {% if current_user.role == 'pharmacist' %}
                        <li class="nav-item"><a class="nav-link" href="/dashboard"><i class="fas fa-tachometer-alt me-1"></i>Dashboard</a></li>
                        <li class="nav-item"><a class="nav-link" href="/inventory"><i class="fas fa-boxes me-1"></i>Inventory</a></li>
                        <li class="nav-item"><a class="nav-link" href="/billing"><i class="fas fa-file-invoice-dollar me-1"></i>Billing</a></li>
                        <li class="nav-item"><a class="nav-link" href="/customers"><i class="fas fa-users me-1"></i>Customers</a></li>
                        <li class="nav-item"><a class="nav-link" href="/pharmacist_orders"><i class="fas fa-shopping-bag me-1"></i>Orders</a></li>
                        {% elif current_user.role == 'doctor' %}
                        <li class="nav-item"><a class="nav-link" href="/dashboard"><i class="fas fa-tachometer-alt me-1"></i>Dashboard</a></li>
                        <li class="nav-item"><a class="nav-link" href="/check_medicines"><i class="fas fa-pills me-1"></i>Medicines</a></li>
                        <li class="nav-item"><a class="nav-link" href="/nearby_pharmacies"><i class="fas fa-store me-1"></i>Pharmacies</a></li>
                        {% elif current_user.role == 'patient' %}
                        <li class="nav-item"><a class="nav-link" href="/dashboard"><i class="fas fa-tachometer-alt me-1"></i>Dashboard</a></li>
                        <li class="nav-item"><a class="nav-link" href="/browse_medicines"><i class="fas fa-pills me-1"></i>Medicines</a></li>
                        <li class="nav-item"><a class="nav-link" href="/upload_prescription"><i class="fas fa-file-medical me-1"></i>Prescription</a></li>
                        <li class="nav-item"><a class="nav-link" href="/my_orders"><i class="fas fa-shopping-bag me-1"></i>My Orders</a></li>
                        <li class="nav-item"><a class="nav-link" href="/nearby_pharmacies"><i class="fas fa-store me-1"></i>Nearby</a></li>
                        {% endif %}
                    {% endif %}
                </ul>
                <ul class="navbar-nav">
                    {% if current_user %}
                    {% if current_user.role == 'pharmacist' %}
                    <li class="nav-item">
                        <a class="nav-link" href="/notifications">
                            <span class="notification-bell">
                                <i class="fas fa-bell"></i>
                                {% if notif_count and notif_count > 0 %}<span class="notification-badge">{{ notif_count }}</span>{% endif %}
                            </span>
                        </a>
                    </li>
                    {% endif %}
                    <li class="nav-item dropdown">
                        <a class="nav-link dropdown-toggle user-menu" href="#" data-bs-toggle="dropdown">
                            <div class="user-avatar">{{ current_user.full_name[0] if current_user.full_name else 'U' }}</div>
                            {{ current_user.full_name or current_user.username }}
                            <span class="badge bg-{{ 'info' if current_user.role == 'pharmacist' else 'success' if current_user.role == 'doctor' else 'primary' }} ms-1">{{ current_user.role|title }}</span>
                        </a>
                        <ul class="dropdown-menu dropdown-menu-end">
                            <li><a class="dropdown-item" href="/profile"><i class="fas fa-user me-2"></i>Profile</a></li>
                            <li><hr class="dropdown-divider"></li>
                            <li><a class="dropdown-item" href="/logout"><i class="fas fa-sign-out-alt me-2"></i>Logout</a></li>
                        </ul>
                    </li>
                    {% else %}
                    <li class="nav-item"><a class="nav-link btn-login" href="/login"><i class="fas fa-sign-in-alt me-1"></i>Login</a></li>
                    <li class="nav-item ms-2"><a class="nav-link btn-register" href="/register"><i class="fas fa-user-plus me-1"></i>Register</a></li>
                    {% endif %}
                </ul>
            </div>
        </div>
    </nav>
    <div class="container-fluid mt-5 pt-4">
        {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}{% for cat, msg in messages %}
        <div class="alert alert-{{ cat }} alert-dismissible fade show mx-3 mt-2 flash-alert">
            <i class="fas fa-{{ 'check-circle' if cat == 'success' else 'exclamation-triangle' if cat == 'warning' else 'info-circle' if cat == 'info' else 'times-circle' }} me-2"></i>{{ msg }}
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        </div>
        {% endfor %}{% endif %}{% endwith %}
    </div>
    <main class="main-content">{% block content %}{% endblock %}</main>
    <footer class="footer">
        <div class="container">
            <div class="row">
                <div class="col-md-4"><h5><i class="fas fa-clinic-medical me-2"></i>Gramin SmartCare</h5><p>AI-powered pharmacy management for rural India.</p></div>
                <div class="col-md-4"><h5>Quick Links</h5><ul class="list-unstyled"><li><a href="/">Home</a></li><li><a href="/login">Login</a></li><li><a href="/register">Register</a></li></ul></div>
                <div class="col-md-4"><h5>Contact</h5><p><i class="fas fa-phone me-2"></i>+91 1800-XXX-XXXX</p><p><i class="fas fa-envelope me-2"></i>support@graminsmartcare.in</p></div>
            </div>
            <hr><p class="text-center mb-0">&copy; 2026 Gramin SmartCare. Built for Rural India.</p>
        </div>
    </footer>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        setTimeout(()=>{document.querySelectorAll('.flash-alert').forEach(a=>{a.classList.remove('show');setTimeout(()=>a.remove(),300);});},5000);
    </script>
    {% block scripts %}{% endblock %}
</body>
</html>
"""

INDEX_HTML = """
{% extends "base.html" %}
{% block content %}
<section class="hero-section">
    <div class="container position-relative">
        <div class="row align-items-center" style="min-height:75vh;">
            <div class="col-lg-7">
                <span class="hero-badge"><i class="fas fa-heartbeat me-2"></i>Smart Healthcare</span>
                <h1 class="hero-title">Gramin<br><span class="gradient-text">SmartCare</span></h1>
                <p class="hero-subtitle">AI-powered pharmacy management — Order medicines, upload prescriptions, and connect with nearby pharmacies.</p>
                <div class="hero-buttons">
                    {% if not current_user %}
                    <a href="/register" class="btn btn-hero-primary btn-lg"><i class="fas fa-rocket me-2"></i>Get Started</a>
                    <a href="/login" class="btn-hero-outline btn-lg"><i class="fas fa-sign-in-alt me-2"></i>Login</a>
                    {% else %}
                    <a href="/dashboard" class="btn btn-hero-primary btn-lg"><i class="fas fa-tachometer-alt me-2"></i>Go to Dashboard</a>
                    {% endif %}
                </div>
            </div>
            <div class="col-lg-5">
                <div class="hero-stats-grid">
                    <div class="hero-stat-card"><div class="stat-icon bg-gradient-blue"><i class="fas fa-pills"></i></div><div class="stat-number">{{ total_products }}</div><div class="stat-label">Products Available</div></div>
                    <div class="hero-stat-card"><div class="stat-icon bg-gradient-green"><i class="fas fa-store"></i></div><div class="stat-number">{{ total_pharmacies }}</div><div class="stat-label">Pharmacies</div></div>
                    <div class="hero-stat-card"><div class="stat-icon bg-gradient-orange"><i class="fas fa-user-md"></i></div><div class="stat-number">{{ total_doctors }}</div><div class="stat-label">Doctors</div></div>
                    <div class="hero-stat-card"><div class="stat-icon bg-gradient-purple"><i class="fas fa-users"></i></div><div class="stat-number">{{ total_patients }}</div><div class="stat-label">Patients</div></div>
                </div>
            </div>
        </div>
    </div>
</section>
<section class="features-section py-5">
    <div class="container">
        <div class="section-header text-center mb-5">
            <span class="section-badge">Features</span>
            <h2 class="section-title">For Everyone in Healthcare</h2>
        </div>
        <div class="row g-4">
            {% for icon, title, desc in [('fa-user','For Patients','Order medicines online, upload prescriptions — AI reads them automatically. Track monthly medicines.'),('fa-pills','For Pharmacists','Manage inventory, billing, customer database. Get order notifications. Auto stock deduction.'),('fa-user-md','For Doctors','Check medicine availability at nearby pharmacies. Help patients find the right pharmacy.'),('fa-brain','AI Prescription Reader','Upload prescription photo — Gemini AI identifies medicines and creates your order automatically.'),('fa-map-marker-alt','Nearby Pharmacies','GPS-based pharmacy finder. See distance, available medicines, and place orders instantly.'),('fa-sync','Monthly Orders','Set up recurring monthly medicine orders. Never miss your regular medicines.')] %}
            <div class="col-lg-4 col-md-6">
                <div class="feature-card"><div class="feature-icon"><i class="fas {{ icon }}"></i></div><h4>{{ title }}</h4><p>{{ desc }}</p></div>
            </div>
            {% endfor %}
        </div>
    </div>
</section>
<section class="cta-section py-5">
    <div class="container"><div class="cta-card text-center"><h2>Join Gramin SmartCare Today</h2><p>Whether you're a patient, pharmacist, or doctor</p>
        <div class="d-flex justify-content-center gap-3 flex-wrap">
            <a href="/register?role=patient" class="btn btn-light btn-lg"><i class="fas fa-user me-2"></i>I'm a Patient</a>
            <a href="/register?role=pharmacist" class="btn btn-outline-light btn-lg"><i class="fas fa-pills me-2"></i>I'm a Pharmacist</a>
            <a href="/register?role=doctor" class="btn btn-outline-light btn-lg"><i class="fas fa-user-md me-2"></i>I'm a Doctor</a>
        </div>
    </div></div>
</section>
{% endblock %}
"""

LOGIN_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="auth-container">
    <div class="container"><div class="row justify-content-center"><div class="col-lg-5 col-md-7">
        <div class="auth-card">
            <div class="auth-header text-center"><div class="auth-icon"><i class="fas fa-sign-in-alt"></i></div><h2>Welcome Back</h2><p>Sign in to your account</p></div>
            <form method="POST" id="loginForm">
                <div class="mb-3"><label class="form-label"><i class="fas fa-envelope me-2"></i>Email</label><input type="email" name="email" id="loginEmail" class="form-control form-control-lg" placeholder="Enter email" required></div>
                <div class="mb-4"><label class="form-label"><i class="fas fa-lock me-2"></i>Password</label>
                    <div class="input-group"><input type="password" name="password" id="loginPassword" class="form-control form-control-lg" placeholder="Enter password" required>
                    <button class="btn btn-outline-secondary" type="button" onclick="let i=document.getElementById('loginPassword');i.type=i.type==='password'?'text':'password';"><i class="fas fa-eye"></i></button></div></div>
                <button type="submit" class="btn btn-primary btn-lg w-100 mb-3" id="loginBtn"><i class="fas fa-sign-in-alt me-2"></i>Sign In</button>
            </form>
            <div class="auth-footer text-center">
                <p>Don't have an account? <a href="/register">Register here</a></p>
            </div>
        </div>
    </div></div></div>
</div>
{% endblock %}
{% block scripts %}
<script src="https://www.gstatic.com/firebasejs/10.7.1/firebase-app-compat.js"></script>
<script src="https://www.gstatic.com/firebasejs/10.7.1/firebase-auth-compat.js"></script>
<script>
const firebaseConfig = {
    apiKey: "AIzaSyDT0h7T88k51e9JtazrGRUaRi6liiqQhiU",
    authDomain: "gramin-smartcare.firebaseapp.com",
    projectId: "gramin-smartcare",
    storageBucket: "gramin-smartcare.firebasestorage.app",
    messagingSenderId: "73081284160",
    appId: "1:73081284160:web:ef711986a83dcc00e861a5"
};
firebase.initializeApp(firebaseConfig);

document.getElementById('loginForm').addEventListener('submit', async function(e) {
    e.preventDefault();
    const btn = document.getElementById('loginBtn');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i>Signing In...';
    const email = document.getElementById('loginEmail').value;
    const password = document.getElementById('loginPassword').value;
    try {
        const userCredential = await firebase.auth().signInWithEmailAndPassword(email, password);
        const idToken = await userCredential.user.getIdToken();
        const response = await fetch('/auth/login', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id_token: idToken, email: email})
        });
        const data = await response.json();
        if (data.success) {
            window.location.href = '/dashboard';
        } else {
            alert(data.error || 'Login failed');
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-sign-in-alt me-2"></i>Sign In';
        }
    } catch (error) {
        let msg = 'Login failed';
        if (error.code === 'auth/user-not-found') msg = 'No account found with this email';
        else if (error.code === 'auth/wrong-password') msg = 'Incorrect password';
        else if (error.code === 'auth/invalid-email') msg = 'Invalid email address';
        else if (error.code === 'auth/invalid-credential') msg = 'Invalid email or password';
        alert(msg);
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-sign-in-alt me-2"></i>Sign In';
    }
});
</script>
{% endblock %}
"""

REGISTER_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="auth-container">
    <div class="container"><div class="row justify-content-center"><div class="col-lg-7 col-md-9">
        <div class="auth-card">
            <div class="auth-header text-center"><div class="auth-icon"><i class="fas fa-user-plus"></i></div><h2>Create Account</h2><p>Join Gramin SmartCare</p></div>
            <form method="POST" id="registerForm">
                <div class="mb-4"><label class="form-label"><i class="fas fa-user-tag me-2"></i>Register As</label>
                    <div class="role-selector">
                        <label class="role-option"><input type="radio" name="role" value="patient" {{ 'checked' if request.args.get('role')=='patient' or not request.args.get('role') }} required><div class="role-card"><i class="fas fa-user"></i><span>Patient</span><small>Order medicines</small></div></label>
                        <label class="role-option"><input type="radio" name="role" value="pharmacist" {{ 'checked' if request.args.get('role')=='pharmacist' }} required><div class="role-card"><i class="fas fa-pills"></i><span>Pharmacist</span><small>Manage pharmacy</small></div></label>
                        <label class="role-option"><input type="radio" name="role" value="doctor" {{ 'checked' if request.args.get('role')=='doctor' }} required><div class="role-card"><i class="fas fa-user-md"></i><span>Doctor</span><small>Check availability</small></div></label>
                    </div></div>
                <div class="row g-3">
                    <div class="col-md-6"><label class="form-label">Full Name</label><input type="text" name="full_name" id="regFullName" class="form-control" required></div>
                    <div class="col-md-6"><label class="form-label">Username</label><input type="text" name="username" id="regUsername" class="form-control" required></div>
                    <div class="col-md-6"><label class="form-label">Email</label><input type="email" name="email" id="regEmail" class="form-control" required></div>
                    <div class="col-md-6"><label class="form-label">Phone</label><input type="tel" name="phone" id="regPhone" class="form-control"></div>
                    <div class="col-12"><label class="form-label">Organization / Hospital / Pharmacy</label><input type="text" name="organization" id="regOrg" class="form-control"></div>
                    <div class="col-12"><label class="form-label">Address</label><textarea name="address" id="regAddress" class="form-control" rows="2"></textarea></div>
                    <div class="col-md-6"><label class="form-label">Password</label><input type="password" name="password" id="regPassword" class="form-control" required minlength="6"></div>
                    <div class="col-md-6"><label class="form-label">Confirm Password</label><input type="password" name="confirm_password" id="regConfirmPassword" class="form-control" required minlength="6"></div>
                </div>
                <input type="hidden" name="latitude" id="regLat">
                <input type="hidden" name="longitude" id="regLng">
                <button type="submit" class="btn btn-primary btn-lg w-100 mt-4 mb-3" id="registerBtn"><i class="fas fa-user-plus me-2"></i>Create Account</button>
            </form>
            <div class="auth-footer text-center"><p>Already have an account? <a href="/login">Sign in here</a></p></div>
        </div>
    </div></div></div>
</div>
{% endblock %}
{% block scripts %}
<script src="https://www.gstatic.com/firebasejs/10.7.1/firebase-app-compat.js"></script>
<script src="https://www.gstatic.com/firebasejs/10.7.1/firebase-auth-compat.js"></script>
<script>
const firebaseConfig = {
    apiKey: "AIzaSyDT0h7T88k51e9JtazrGRUaRi6liiqQhiU",
    authDomain: "gramin-smartcare.firebaseapp.com",
    projectId: "gramin-smartcare",
    storageBucket: "gramin-smartcare.firebasestorage.app",
    messagingSenderId: "73081284160",
    appId: "1:73081284160:web:ef711986a83dcc00e861a5"
};
firebase.initializeApp(firebaseConfig);

if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(function(pos) {
        document.getElementById('regLat').value = pos.coords.latitude;
        document.getElementById('regLng').value = pos.coords.longitude;
    }, function() { console.log('Location denied'); });
}

document.getElementById('registerForm').addEventListener('submit', async function(e) {
    e.preventDefault();
    const btn = document.getElementById('registerBtn');
    const pw = document.getElementById('regPassword').value;
    const cpw = document.getElementById('regConfirmPassword').value;
    if (pw !== cpw) { alert('Passwords do not match!'); return; }
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i>Creating Account...';
    const email = document.getElementById('regEmail').value;
    const role = document.querySelector('input[name="role"]:checked').value;
    try {
        const userCredential = await firebase.auth().createUserWithEmailAndPassword(email, pw);
        const idToken = await userCredential.user.getIdToken();
        const uid = userCredential.user.uid;
        const response = await fetch('/auth/register', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                id_token: idToken, uid: uid, email: email, role: role,
                full_name: document.getElementById('regFullName').value,
                username: document.getElementById('regUsername').value,
                phone: document.getElementById('regPhone').value,
                organization: document.getElementById('regOrg').value,
                address: document.getElementById('regAddress').value,
                latitude: document.getElementById('regLat').value || null,
                longitude: document.getElementById('regLng').value || null
            })
        });
        const data = await response.json();
        if (data.success) {
            window.location.href = '/dashboard';
        } else {
            await userCredential.user.delete();
            alert(data.error || 'Registration failed');
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-user-plus me-2"></i>Create Account';
        }
    } catch (error) {
        let msg = 'Registration failed';
        if (error.code === 'auth/email-already-in-use') msg = 'Email already registered';
        else if (error.code === 'auth/weak-password') msg = 'Password must be at least 6 characters';
        else if (error.code === 'auth/invalid-email') msg = 'Invalid email address';
        alert(msg);
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-user-plus me-2"></i>Create Account';
    }
});
</script>
{% endblock %}
"""

DASHBOARD_PATIENT_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container-fluid px-4 py-4">
    <div class="page-header mb-4">
        <div><h2><i class="fas fa-user me-2"></i>Welcome, {{ current_user.full_name }}!</h2><p class="text-muted">Your health dashboard</p></div>
    </div>
    <div class="row g-4 mb-4">
        <div class="col-md-3"><div class="stat-card-modern bg-gradient-blue"><div class="stat-card-body"><div class="stat-card-icon"><i class="fas fa-shopping-bag"></i></div><div class="stat-card-info"><h3>{{ stats.total_orders }}</h3><p>Total Orders</p></div></div></div></div>
        <div class="col-md-3"><div class="stat-card-modern bg-gradient-orange"><div class="stat-card-body"><div class="stat-card-icon"><i class="fas fa-clock"></i></div><div class="stat-card-info"><h3>{{ stats.pending_orders }}</h3><p>Pending Orders</p></div></div></div></div>
        <div class="col-md-3"><div class="stat-card-modern bg-gradient-green"><div class="stat-card-body"><div class="stat-card-icon"><i class="fas fa-sync"></i></div><div class="stat-card-info"><h3>{{ stats.monthly_orders }}</h3><p>Monthly Orders</p></div></div></div></div>
        <div class="col-md-3"><div class="stat-card-modern bg-gradient-purple"><div class="stat-card-body"><div class="stat-card-icon"><i class="fas fa-store"></i></div><div class="stat-card-info"><h3>{{ stats.nearby_pharmacies }}</h3><p>Nearby Pharmacies</p></div></div></div></div>
    </div>
    <div class="row g-4 mb-4">
        <div class="col-md-6">
            <div class="chart-card">
                <div class="chart-header"><h5><i class="fas fa-bolt me-2 text-warning"></i>Quick Actions</h5></div>
                <div class="chart-body"><div class="d-grid gap-2">
                    <a href="/browse_medicines" class="btn btn-outline-primary btn-lg"><i class="fas fa-pills me-2"></i>Browse & Order Medicines</a>
                    <a href="/upload_prescription" class="btn btn-outline-success btn-lg"><i class="fas fa-file-medical me-2"></i>Upload Prescription (AI Reader)</a>
                    <a href="/nearby_pharmacies" class="btn btn-outline-info btn-lg"><i class="fas fa-map-marker-alt me-2"></i>Find Nearby Pharmacies</a>
                    <a href="/my_orders" class="btn btn-outline-warning btn-lg"><i class="fas fa-shopping-bag me-2"></i>View My Orders</a>
                </div></div>
            </div>
        </div>
        <div class="col-md-6">
            <div class="chart-card">
                <div class="chart-header"><h5><i class="fas fa-history me-2"></i>Recent Orders</h5></div>
                <div class="chart-body p-0"><div class="table-responsive"><table class="table table-hover mb-0"><thead><tr><th>#</th><th>Pharmacy</th><th>Amount</th><th>Status</th><th>Date</th></tr></thead><tbody>
                    {% for order in stats.recent_orders %}
                    <tr><td>#{{ order.id }}</td><td>{{ order.pharmacist.organization if order.pharmacist else 'N/A' }}</td><td>₹{{ "%.2f"|format(order.total_amount) }}</td>
                        <td><span class="order-status-badge status-{{ order.status }}">{{ order.status|title }}</span></td>
                        <td>{{ order.order_date.strftime('%d %b') }}</td></tr>
                    {% else %}<tr><td colspan="5" class="text-center text-muted py-4">No orders yet. <a href="/browse_medicines">Start ordering!</a></td></tr>{% endfor %}
                </tbody></table></div></div>
            </div>
        </div>
    </div>
</div>
{% endblock %}
"""

BROWSE_MEDICINES_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container-fluid px-4 py-4">
    <div class="page-header mb-4">
        <div><h2><i class="fas fa-pills me-2"></i>Browse Medicines</h2><p class="text-muted">Order from nearby pharmacies</p></div>
        <a href="/upload_prescription" class="btn btn-success"><i class="fas fa-file-medical me-2"></i>Upload Prescription</a>
    </div>
    <div class="filter-bar mb-4"><form method="GET" class="row g-3 align-items-end">
        <div class="col-md-3"><label class="form-label">Search</label><div class="input-group"><span class="input-group-text"><i class="fas fa-search"></i></span><input type="text" name="search" class="form-control" placeholder="Search medicines..." value="{{ search }}"></div></div>
        <div class="col-md-3"><label class="form-label">Category</label><select name="category" class="form-select"><option value="">All</option>{% for c in categories %}<option value="{{ c.id }}" {{ 'selected' if selected_category == c.id }}>{{ c.icon }} {{ c.name }}</option>{% endfor %}</select></div>
        <div class="col-md-3"><label class="form-label">Pharmacy</label><select name="pharmacy_id" class="form-select"><option value="">All Nearby</option>{% for p in pharmacies %}<option value="{{ p.id }}" {{ 'selected' if selected_pharmacy == p.id }}>{{ p.organization or p.full_name }} ({{ p.distance if p.distance else '?' }} km)</option>{% endfor %}</select></div>
        <div class="col-md-3"><button type="submit" class="btn btn-primary w-100"><i class="fas fa-filter me-2"></i>Filter</button></div>
    </form></div>
    <div class="row g-3">
        {% for p in products %}
        <div class="col-xl-3 col-lg-4 col-md-6">
            <div class="product-card">
                <div class="product-header"><span class="product-category">{{ p.category.icon }} {{ p.category.name }}</span>
                    <span class="stock-badge stock-{{ p.stock_status }}">{% if p.stock_status=='out_of_stock' %}Out{% elif p.stock_status=='critical' %}Low{% else %}In Stock{% endif %}</span></div>
                <h5 class="product-name">{{ p.name }}</h5>
                <div class="product-details">
                    <div class="detail-row"><span><i class="fas fa-rupee-sign me-1"></i>Price:</span><strong>₹{{ "%.2f"|format(p.unit_price) }}/{{ p.unit }}</strong></div>
                    <div class="detail-row"><span><i class="fas fa-cubes me-1"></i>Available:</span><strong class="text-{{ 'danger' if p.stock_status in ['out_of_stock','critical'] else 'success' }}">{{ p.current_stock }} {{ p.unit }}</strong></div>
                    <div class="detail-row"><span><i class="fas fa-store me-1"></i>Pharmacy:</span><span>{{ p.pharmacist_name }}</span></div>
                    {% if p.expiry_date %}
                    <div class="detail-row"><span><i class="fas fa-calendar me-1"></i>Expiry:</span><span class="expiry-badge expiry-{{ p.expiry_status }}">{{ p.expiry_date.strftime('%b %Y') }}</span></div>
                    {% endif %}
                </div>
                <div class="product-actions mt-3">
                    {% if p.current_stock > 0 %}
                    <form method="POST" action="/add_to_order" class="d-flex gap-2 align-items-end">
                        <input type="hidden" name="product_id" value="{{ p.id }}">
                        <input type="number" name="quantity" class="form-control form-control-sm" value="1" min="1" max="{{ p.current_stock }}" style="width:70px;">
                        <div class="form-check form-check-inline mb-0">
                            <input type="checkbox" name="is_monthly" class="form-check-input" id="monthly_{{ p.id }}">
                            <label class="form-check-label small" for="monthly_{{ p.id }}">Monthly</label>
                        </div>
                        <button type="submit" class="btn btn-sm btn-success flex-fill"><i class="fas fa-cart-plus me-1"></i>Order</button>
                    </form>
                    {% else %}
                    <button class="btn btn-sm btn-outline-danger w-100" disabled><i class="fas fa-times me-1"></i>Out of Stock</button>
                    {% endif %}
                </div>
            </div>
        </div>
        {% endfor %}
    </div>
    {% if not products %}<div class="empty-state text-center py-5"><i class="fas fa-pills fa-3x text-muted mb-3"></i><h4>No medicines found</h4><p class="text-muted">Try adjusting your filters or check nearby pharmacies</p></div>{% endif %}
</div>
{% endblock %}
"""

UPLOAD_PRESCRIPTION_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container py-4"><div class="row justify-content-center"><div class="col-lg-8">
    <div class="chart-card">
        <div class="chart-header"><h5><i class="fas fa-file-medical me-2"></i>Upload Prescription</h5></div>
        <div class="chart-body">
            <p class="text-muted mb-4">Upload your prescription and AI will identify medicines, or enter them manually.</p>
            
            <!-- AI Upload Section -->
            <div class="prescription-upload" id="dropZone" onclick="document.getElementById('prescriptionFile').click();">
                <i class="fas fa-cloud-upload-alt fa-3x text-primary mb-3"></i>
                <h5>Drop prescription image here</h5>
                <p class="text-muted">or click to browse (JPG, PNG)</p>
                <input type="file" id="prescriptionFile" accept="image/*" style="display:none;" onchange="handleFile(this)">
            </div>
            <img id="prescriptionPreview" class="prescription-preview d-none mx-auto d-block" alt="Preview">
            
            <div id="aiLoading" class="d-none text-center py-4">
                <div class="spinner-border text-primary" role="status"></div>
                <p class="mt-2 text-muted">AI is reading your prescription...</p>
            </div>

            <!-- Manual Entry Section -->
            <div class="mt-4 p-3 border rounded">
                <h6><i class="fas fa-keyboard me-2"></i>Or Enter Medicines Manually:</h6>
                <div id="manualMeds">
                    <div class="row g-2 mb-2 manual-row" id="manRow0">
                        <div class="col-5"><input type="text" class="form-control form-control-sm" placeholder="Medicine name" id="manName0"></div>
                        <div class="col-3"><input type="text" class="form-control form-control-sm" placeholder="Dosage" id="manDosage0"></div>
                        <div class="col-2"><input type="number" class="form-control form-control-sm" placeholder="Qty" value="1" min="1" id="manQty0"></div>
                        <div class="col-2"><button class="btn btn-sm btn-outline-danger w-100" onclick="removeManRow(0)"><i class="fas fa-times"></i></button></div>
                    </div>
                </div>
                <button class="btn btn-sm btn-outline-primary mt-2" onclick="addManRow()"><i class="fas fa-plus me-1"></i>Add Medicine</button>
            </div>

            <!-- AI Results Section -->
            <div id="aiResults" class="d-none mt-4">
                <h5 class="mb-3"><i class="fas fa-brain me-2 text-primary"></i>AI Detected Medicines:</h5>
                <div id="detectedMedicines"></div>
            </div>

            <!-- Order Section -->
            <div class="mt-4">
                <div class="mb-3">
                    <label class="form-label">Select Pharmacy</label>
                    <select id="pharmacySelect" class="form-select">
                        <option value="">Select nearby pharmacy</option>
                        {% for p in pharmacies %}
                        <option value="{{ p.id }}">{{ p.organization or p.full_name }} ({{ p.distance }} km)</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="form-check mb-3">
                    <input type="checkbox" class="form-check-input" id="isMonthlyOrder">
                    <label class="form-check-label" for="isMonthlyOrder"><strong>Monthly prescription</strong></label>
                </div>
                <button class="btn btn-success btn-lg w-100" id="placeOrderBtn" onclick="placeOrder()"><i class="fas fa-shopping-cart me-2"></i>Place Order</button>
            </div>
        </div>
    </div>
    
    <div class="chart-card mt-4">
        <div class="chart-header"><h5><i class="fas fa-info-circle me-2"></i>Or Browse Directly</h5></div>
        <div class="chart-body">
            <a href="/browse_medicines" class="btn btn-primary"><i class="fas fa-pills me-2"></i>Browse Medicines</a>
        </div>
    </div>
</div></div></div>
{% endblock %}
{% block scripts %}
<script>
let detectedMeds = [];
let manCount = 1;

// === AI UPLOAD ===
const dropZone = document.getElementById('dropZone');
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', e => { e.preventDefault(); dropZone.classList.remove('dragover'); if(e.dataTransfer.files[0]) processFile(e.dataTransfer.files[0]); });

function handleFile(input) { if(input.files[0]) processFile(input.files[0]); }

function processFile(file) {
    if(!file.type.startsWith('image/')) { alert('Please upload an image'); return; }
    const reader = new FileReader();
    reader.onload = async function(e) {
        document.getElementById('prescriptionPreview').src = e.target.result;
        document.getElementById('prescriptionPreview').classList.remove('d-none');
        dropZone.style.display = 'none';
        document.getElementById('aiLoading').classList.remove('d-none');
        document.getElementById('aiResults').classList.add('d-none');
        
        const base64 = e.target.result.split(',')[1];
        try {
            const response = await fetch('/api/analyze_prescription', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({image: base64})
            });
            const data = await response.json();
            document.getElementById('aiLoading').classList.add('d-none');
            
            if(data.medicines && data.medicines.length > 0) {
                detectedMeds = data.medicines;
                let html = '<div class="table-responsive"><table class="table"><thead><tr><th>Medicine</th><th>Dosage</th><th>Qty</th><th>Status</th></tr></thead><tbody>';
                data.medicines.forEach((med, i) => {
                    const found = med.matched_product_id ? true : false;
                    html += '<tr><td><strong>'+med.name+'</strong></td><td>'+(med.dosage||'N/A')+'</td>';
                    html += '<td><input type="number" class="form-control form-control-sm" value="'+(med.quantity||1)+'" min="1" style="width:70px;" onchange="detectedMeds['+i+'].quantity=parseInt(this.value)"></td>';
                    html += '<td>'+(found ? '<span class="badge bg-success">Available</span>' : '<span class="badge bg-warning">Check pharmacy</span>')+'</td></tr>';
                });
                html += '</tbody></table></div>';
                document.getElementById('detectedMedicines').innerHTML = html;
                document.getElementById('aiResults').classList.remove('d-none');
            } else {
                document.getElementById('detectedMedicines').innerHTML = '<div class="alert alert-warning"><i class="fas fa-exclamation-triangle me-2"></i>Could not detect medicines. Please enter them manually below.</div>';
                document.getElementById('aiResults').classList.remove('d-none');
            }
        } catch(err) {
            document.getElementById('aiLoading').classList.add('d-none');
            alert('Error analyzing. Please enter medicines manually.');
        }
    };
    reader.readAsDataURL(file);
}

// === MANUAL ENTRY ===
function addManRow() {
    const idx = manCount++;
    const div = document.createElement('div');
    div.className = 'row g-2 mb-2 manual-row'; div.id = 'manRow'+idx;
    div.innerHTML = '<div class="col-5"><input type="text" class="form-control form-control-sm" placeholder="Medicine name" id="manName'+idx+'"></div><div class="col-3"><input type="text" class="form-control form-control-sm" placeholder="Dosage" id="manDosage'+idx+'"></div><div class="col-2"><input type="number" class="form-control form-control-sm" placeholder="Qty" value="1" min="1" id="manQty'+idx+'"></div><div class="col-2"><button class="btn btn-sm btn-outline-danger w-100" onclick="removeManRow('+idx+')"><i class="fas fa-times"></i></button></div>';
    document.getElementById('manualMeds').appendChild(div);
}

function removeManRow(idx) {
    const row = document.getElementById('manRow'+idx);
    if(row && document.querySelectorAll('.manual-row').length > 1) row.remove();
}

// === PLACE ORDER ===
async function placeOrder() {
    const pharmacyId = document.getElementById('pharmacySelect').value;
    if(!pharmacyId) { alert('Please select a pharmacy'); return; }
    
    // Combine AI detected + manual entries
    let medicines = [...detectedMeds];
    
    // Add manual entries
    for(let i = 0; i < manCount; i++) {
        const name = document.getElementById('manName'+i);
        const dosage = document.getElementById('manDosage'+i);
        const qty = document.getElementById('manQty'+i);
        if(name && name.value.trim()) {
            medicines.push({
                name: name.value.trim(),
                dosage: dosage ? dosage.value.trim() || 'As prescribed' : 'As prescribed',
                quantity: qty ? parseInt(qty.value) || 1 : 1
            });
        }
    }
    
    if(medicines.length === 0) { alert('Upload prescription or enter medicines manually'); return; }
    
    const isMonthly = document.getElementById('isMonthlyOrder').checked;
    const btn = document.getElementById('placeOrderBtn');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i>Placing Order...';
    
    try {
        const response = await fetch('/api/place_prescription_order', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ medicines: medicines, pharmacy_id: parseInt(pharmacyId), is_monthly: isMonthly })
        });
        const data = await response.json();
        if(data.success) { window.location.href = '/my_orders?msg=Order placed!'; }
        else { alert(data.error || 'Failed'); btn.disabled = false; btn.innerHTML = '<i class="fas fa-shopping-cart me-2"></i>Place Order'; }
    } catch(err) { alert('Error'); btn.disabled = false; btn.innerHTML = '<i class="fas fa-shopping-cart me-2"></i>Place Order'; }
}
</script>
{% endblock %}
"""

MY_ORDERS_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container-fluid px-4 py-4">
    <div class="page-header mb-4">
        <div><h2><i class="fas fa-shopping-bag me-2"></i>My Orders</h2><p class="text-muted">Track your medicine orders</p></div>
        <div class="d-flex gap-2">
            <a href="/browse_medicines" class="btn btn-primary"><i class="fas fa-plus me-2"></i>New Order</a>
            <a href="/upload_prescription" class="btn btn-success"><i class="fas fa-file-medical me-2"></i>Upload Prescription</a>
        </div>
    </div>
    <div class="chart-card">
        <div class="chart-body p-0"><div class="table-responsive"><table class="table table-hover mb-0"><thead><tr><th>Order #</th><th>Pharmacy</th><th>Items</th><th>Total</th><th>Type</th><th>Status</th><th>Date</th><th>Action</th></tr></thead><tbody>
            {% for order in orders %}
            <tr>
                <td>#{{ order.id }}</td>
                <td>{{ order.pharmacist.organization if order.pharmacist else 'N/A' }}</td>
                <td>{{ order.items|length }}</td>
                <td>₹{{ "%.2f"|format(order.total_amount) }}</td>
                <td>{% if order.is_monthly %}<span class="monthly-badge"><i class="fas fa-sync me-1"></i>Monthly</span>{% else %}<span class="badge bg-secondary">One-time</span>{% endif %}</td>
                <td><span class="order-status-badge status-{{ order.status }}">{{ order.status|title }}</span></td>
                <td>{{ order.order_date.strftime('%d %b %Y') }}</td>
                <td><a href="/order_detail/{{ order.id }}" class="btn btn-sm btn-outline-primary"><i class="fas fa-eye"></i></a></td>
            </tr>
            {% else %}<tr><td colspan="8" class="text-center text-muted py-4">No orders yet.</td></tr>{% endfor %}
        </tbody></table></div></div>
    </div>
</div>
{% endblock %}
"""

ORDER_DETAIL_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container py-4"><div class="row justify-content-center"><div class="col-lg-8">
    <div class="chart-card">
        <div class="chart-header d-flex justify-content-between align-items-center">
            <h5><i class="fas fa-receipt me-2"></i>Order #{{ order.id }}</h5>
            <span class="order-status-badge status-{{ order.status }}">{{ order.status|title }}</span>
        </div>
        <div class="chart-body">
            <div class="row mb-4">
                <div class="col-md-6">
                    <h6 class="text-muted">Pharmacy</h6>
                    {% if order.pharmacist %}<p class="mb-1"><strong>{{ order.pharmacist.organization or order.pharmacist.full_name }}</strong></p>
                    <p class="mb-0"><i class="fas fa-phone me-1"></i>{{ order.pharmacist.phone or 'N/A' }}</p>{% endif %}
                </div>
                <div class="col-md-6 text-md-end">
                    <h6 class="text-muted">Order Info</h6>
                    <p class="mb-1">Date: {{ order.order_date.strftime('%d %b %Y %H:%M') }}</p>
                    <p class="mb-0">Type: {% if order.is_monthly %}<span class="monthly-badge">Monthly</span>{% else %}One-time{% endif %}</p>
                </div>
            </div>
            <div class="table-responsive">
                <table class="table"><thead><tr><th>#</th><th>Medicine</th><th>Qty</th><th>Price</th><th>Total</th></tr></thead><tbody>
                    {% for item in order.items %}
                    <tr><td>{{ loop.index }}</td><td>{{ item.product.name }}</td><td>{{ item.quantity }}</td><td>₹{{ "%.2f"|format(item.unit_price) }}</td><td>₹{{ "%.2f"|format(item.total_price) }}</td></tr>
                    {% endfor %}
                </tbody>
                <tfoot><tr class="table-primary"><td colspan="4" class="text-end fw-bold">Total:</td><td class="fw-bold fs-5">₹{{ "%.2f"|format(order.total_amount) }}</td></tr></tfoot>
                </table>
            </div>
            <div class="mt-3"><a href="/my_orders" class="btn btn-outline-secondary"><i class="fas fa-arrow-left me-2"></i>Back to Orders</a></div>
        </div>
    </div>
</div></div></div>
{% endblock %}
"""

NEARBY_PHARMACIES_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container-fluid px-4 py-4">
    <div class="page-header mb-4"><div><h2><i class="fas fa-map-marker-alt me-2"></i>Nearby Pharmacies</h2><p class="text-muted">Find pharmacies near you</p></div></div>
    <div id="locationStatus" class="alert alert-info mb-4"><i class="fas fa-spinner fa-spin me-2"></i>Detecting your location...</div>
    <div class="row g-3" id="pharmacyList">
        {% for item in nearby %}
        <div class="col-xl-4 col-md-6">
            <div class="pharmacy-card">
                <div class="d-flex justify-content-between align-items-start mb-3">
                    <div>
                        <h5 class="mb-1"><i class="fas fa-store me-2 text-primary"></i>{{ item.pharmacist.organization or item.pharmacist.full_name }}</h5>
                        <p class="text-muted mb-0">{{ item.pharmacist.full_name }}</p>
                    </div>
                    <span class="distance-badge"><i class="fas fa-map-marker-alt me-1"></i>{{ item.distance }} km</span>
                </div>
                <div class="mb-3">
                    <p class="mb-1"><i class="fas fa-phone me-1 text-muted"></i>{{ item.pharmacist.phone or 'N/A' }}</p>
                    <p class="mb-1"><i class="fas fa-map me-1 text-muted"></i>{{ item.pharmacist.address or 'N/A' }}</p>
                    <p class="mb-0"><i class="fas fa-pills me-1 text-muted"></i><strong>{{ item.product_count }}</strong> medicines available</p>
                </div>
                {% if current_user.role == 'patient' %}
                <a href="/browse_medicines?pharmacy_id={{ item.pharmacist.id }}" class="btn btn-primary btn-sm w-100"><i class="fas fa-shopping-cart me-2"></i>Browse & Order</a>
                {% endif %}   
            </div>
        </div>
        {% else %}
        <div class="col-12"><div class="empty-state text-center py-5"><i class="fas fa-map-marker-alt fa-3x text-muted mb-3"></i><h4>No pharmacies found nearby</h4><p class="text-muted">Allow location access or try again later</p></div></div>
        {% endfor %}
    </div>
</div>
{% endblock %}
{% block scripts %}
<script>
let locationUpdated = false;
if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(function(pos) {
        document.getElementById('locationStatus').innerHTML = '<i class="fas fa-check-circle me-2"></i>Location detected! Showing nearby pharmacies.';
        document.getElementById('locationStatus').className = 'alert alert-success mb-4';
        if(!locationUpdated) {
            locationUpdated = true;
            fetch('/api/update_location', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({lat: pos.coords.latitude, lng: pos.coords.longitude})
            }).then(r => r.json()).then(data => {
                if(data.success && document.querySelectorAll('.pharmacy-card').length === 0) {
                    if(!sessionStorage.getItem('pharmacy_reloaded')) {
                        sessionStorage.setItem('pharmacy_reloaded', 'true');
                        window.location.reload();
                    }
                }
            });
        }
    }, function() {
        document.getElementById('locationStatus').innerHTML = '<i class="fas fa-exclamation-triangle me-2"></i>Location access denied. Please enable location.';
        document.getElementById('locationStatus').className = 'alert alert-warning mb-4';
    });
} else {
    document.getElementById('locationStatus').innerHTML = '<i class="fas fa-times-circle me-2"></i>Geolocation not supported.';
    document.getElementById('locationStatus').className = 'alert alert-danger mb-4';
}
window.addEventListener('beforeunload', function() { sessionStorage.removeItem('pharmacy_reloaded'); });
</script>
{% endblock %}
"""

# ============================================================
# PHARMACIST UI TEMPLATES
# ============================================================

DASHBOARD_PHARMACIST_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container-fluid px-4 py-4">
    <div class="page-header mb-4">
        <div><h2><i class="fas fa-pills me-2"></i>Pharmacist Dashboard</h2><p class="text-muted">{{ current_user.organization or 'My Pharmacy' }}</p></div>
        <div class="d-flex gap-2">
            <a href="/inventory" class="btn btn-primary"><i class="fas fa-boxes me-2"></i>Inventory</a>
            <a href="/billing" class="btn btn-success"><i class="fas fa-file-invoice-dollar me-2"></i>New Bill</a>
        </div>
    </div>
    <div class="row g-4 mb-4">
        <div class="col-md-3"><div class="stat-card-modern bg-gradient-blue"><div class="stat-card-body"><div class="stat-card-icon"><i class="fas fa-pills"></i></div><div class="stat-card-info"><h3>{{ stats.total_products }}</h3><p>Total Products</p></div></div><div class="stat-card-footer"><a href="/inventory">View Inventory <i class="fas fa-arrow-right ms-1"></i></a></div></div></div>
        <div class="col-md-3"><div class="stat-card-modern bg-gradient-red"><div class="stat-card-body"><div class="stat-card-icon"><i class="fas fa-exclamation-triangle"></i></div><div class="stat-card-info"><h3>{{ stats.low_stock }}</h3><p>Low Stock Items</p></div></div><div class="stat-card-footer"><a href="/inventory?filter=low">View Low Stock <i class="fas fa-arrow-right ms-1"></i></a></div></div></div>
        <div class="col-md-3"><div class="stat-card-modern bg-gradient-orange"><div class="stat-card-body"><div class="stat-card-icon"><i class="fas fa-shopping-bag"></i></div><div class="stat-card-info"><h3>{{ stats.pending_orders }}</h3><p>Pending Orders</p></div></div><div class="stat-card-footer"><a href="/pharmacist_orders">View Orders <i class="fas fa-arrow-right ms-1"></i></a></div></div></div>
        <div class="col-md-3"><div class="stat-card-modern bg-gradient-green"><div class="stat-card-body"><div class="stat-card-icon"><i class="fas fa-rupee-sign"></i></div><div class="stat-card-info"><h3>₹{{ "%.0f"|format(stats.today_revenue) }}</h3><p>Today's Revenue</p></div></div><div class="stat-card-footer"><a href="/billing">View Bills <i class="fas fa-arrow-right ms-1"></i></a></div></div></div>
    </div>
    <div class="row g-4 mb-4">
        <div class="col-md-4">
            <div class="chart-card"><div class="chart-header"><h5><i class="fas fa-exclamation-circle me-2 text-danger"></i>Expiring Soon</h5></div>
                <div class="chart-body p-0"><div class="table-responsive"><table class="table table-hover mb-0"><thead><tr><th>Medicine</th><th>Expiry</th><th>Days</th></tr></thead><tbody>
                    {% for p in stats.expiring_soon %}<tr><td>{{ p.name }}</td><td>{{ p.expiry_date.strftime('%d %b %Y') if p.expiry_date else 'N/A' }}</td><td><span class="expiry-badge expiry-{{ p.expiry_status }}">{{ p.days_until_expiry }}d</span></td></tr>
                    {% else %}<tr><td colspan="3" class="text-center text-muted py-3">None</td></tr>{% endfor %}
                </tbody></table></div></div>
            </div>
        </div>
        <div class="col-md-4">
            <div class="chart-card"><div class="chart-header"><h5><i class="fas fa-arrow-down me-2 text-warning"></i>Low Stock</h5></div>
                <div class="chart-body p-0"><div class="table-responsive"><table class="table table-hover mb-0"><thead><tr><th>Medicine</th><th>Stock</th><th>Status</th></tr></thead><tbody>
                    {% for p in stats.low_stock_items %}<tr><td>{{ p.name }}</td><td>{{ p.current_stock }} {{ p.unit }}</td><td><span class="stock-badge stock-{{ p.stock_status }}">{{ p.stock_status|replace('_',' ')|title }}</span></td></tr>
                    {% else %}<tr><td colspan="3" class="text-center text-muted py-3">All adequate</td></tr>{% endfor %}
                </tbody></table></div></div>
            </div>
        </div>
        <div class="col-md-4">
            <div class="chart-card"><div class="chart-header"><h5><i class="fas fa-shopping-bag me-2 text-primary"></i>Recent Orders</h5></div>
                <div class="chart-body p-0"><div class="table-responsive"><table class="table table-hover mb-0"><thead><tr><th>Order</th><th>Patient</th><th>Status</th></tr></thead><tbody>
                    {% for o in stats.recent_orders %}<tr><td>#{{ o.id }}</td><td>{{ o.user.full_name }}</td><td><span class="order-status-badge status-{{ o.status }}">{{ o.status|title }}</span></td></tr>
                    {% else %}<tr><td colspan="3" class="text-center text-muted py-3">No orders</td></tr>{% endfor %}
                </tbody></table></div></div>
            </div>
        </div>
    </div>
    <div class="row g-4">
        <div class="col-md-6">
            <div class="chart-card"><div class="chart-header"><h5><i class="fas fa-chart-bar me-2"></i>Sales (Last 7 Days)</h5></div>
                <div class="chart-body"><canvas id="salesChart" height="200"></canvas></div>
            </div>
        </div>
        <div class="col-md-6">
            <div class="chart-card"><div class="chart-header"><h5><i class="fas fa-bolt me-2 text-warning"></i>Quick Actions</h5></div>
                <div class="chart-body"><div class="d-grid gap-2">
                    <a href="/add_product" class="btn btn-outline-primary btn-lg"><i class="fas fa-plus-circle me-2"></i>Add Product</a>
                    <a href="/billing" class="btn btn-outline-success btn-lg"><i class="fas fa-file-invoice-dollar me-2"></i>Create Bill</a>
                    <a href="/customers" class="btn btn-outline-info btn-lg"><i class="fas fa-users me-2"></i>Customers</a>
                    <a href="/pharmacist_orders" class="btn btn-outline-warning btn-lg"><i class="fas fa-shopping-bag me-2"></i>Orders ({{ stats.pending_orders }})</a>
                </div></div>
            </div>
        </div>
    </div>
</div>
{% endblock %}
{% block scripts %}
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script>
const ctx = document.getElementById('salesChart');
if(ctx) { new Chart(ctx, { type: 'bar', data: { labels: {{ stats.sales_labels|tojson }}, datasets: [{ label: 'Revenue (₹)', data: {{ stats.sales_data|tojson }}, backgroundColor: 'rgba(102,126,234,0.6)', borderColor: 'rgba(102,126,234,1)', borderWidth: 1, borderRadius: 8 }] }, options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } } }); }
</script>
{% endblock %}
"""

INVENTORY_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container-fluid px-4 py-4">
    <div class="page-header mb-4">
        <div><h2><i class="fas fa-boxes me-2"></i>Inventory</h2><p class="text-muted">Manage stock</p></div>
        <a href="/add_product" class="btn btn-primary"><i class="fas fa-plus-circle me-2"></i>Add Product</a>
    </div>
    <div class="filter-bar mb-4"><form method="GET" class="row g-3 align-items-end">
        <div class="col-md-3"><label class="form-label">Search</label><input type="text" name="search" class="form-control" placeholder="Search..." value="{{ search }}"></div>
        <div class="col-md-2"><label class="form-label">Category</label><select name="category" class="form-select"><option value="">All</option>{% for c in categories %}<option value="{{ c.id }}" {{ 'selected' if selected_category == c.id }}>{{ c.icon }} {{ c.name }}</option>{% endfor %}</select></div>
        <div class="col-md-2"><label class="form-label">Stock</label><select name="filter" class="form-select"><option value="">All</option><option value="low" {{ 'selected' if stock_filter == 'low' }}>Low</option><option value="critical" {{ 'selected' if stock_filter == 'critical' }}>Critical</option><option value="out" {{ 'selected' if stock_filter == 'out' }}>Out</option></select></div>
        <div class="col-md-2"><label class="form-label">Expiry</label><select name="expiry" class="form-select"><option value="">All</option><option value="expired" {{ 'selected' if expiry_filter == 'expired' }}>Expired</option><option value="expiring_soon" {{ 'selected' if expiry_filter == 'expiring_soon' }}>30 days</option></select></div>
        <div class="col-md-1"><button type="submit" class="btn btn-primary w-100"><i class="fas fa-filter"></i></button></div>
        <div class="col-md-2"><a href="/inventory" class="btn btn-outline-secondary w-100">Reset</a></div>
    </form></div>
    <div class="row g-3">
        {% for p in products %}
        <div class="col-xl-3 col-lg-4 col-md-6">
            <div class="product-card">
                <div class="product-header"><span class="product-category">{{ p.category.icon }} {{ p.category.name }}</span><span class="stock-badge stock-{{ p.stock_status }}">{{ p.stock_status|replace('_',' ')|title }}</span></div>
                <h5 class="product-name">{{ p.name }}</h5>
                <div class="product-details">
                    <div class="detail-row"><span>Price:</span><strong>₹{{ "%.2f"|format(p.unit_price) }}/{{ p.unit }}</strong></div>
                    <div class="detail-row"><span>Stock:</span><strong class="text-{{ 'danger' if p.stock_status in ['out_of_stock','critical'] else 'success' }}">{{ p.current_stock }}</strong></div>
                    {% if p.expiry_date %}<div class="detail-row"><span>Expiry:</span><span class="expiry-badge expiry-{{ p.expiry_status }}">{{ p.expiry_date.strftime('%d %b %Y') }}</span></div>{% endif %}
                    <div class="detail-row"><span>Predicted Days:</span><strong>{{ p.predicted_stock_days if p.predicted_stock_days < 999 else '∞' }}</strong></div>
                </div>
                <div class="product-actions mt-3 d-flex gap-2">
                    <a href="/edit_product/{{ p.id }}" class="btn btn-sm btn-outline-primary flex-fill"><i class="fas fa-edit me-1"></i>Edit</a>
                    <button class="btn btn-sm btn-outline-success flex-fill" data-bs-toggle="modal" data-bs-target="#restockModal{{ p.id }}"><i class="fas fa-plus me-1"></i>Restock</button>
                </div>
            </div>
        </div>
        <div class="modal fade" id="restockModal{{ p.id }}" tabindex="-1"><div class="modal-dialog"><div class="modal-content">
            <div class="modal-header"><h5 class="modal-title">Restock: {{ p.name }}</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
            <form method="POST" action="/restock_product/{{ p.id }}"><div class="modal-body">
                <p>Current: <strong>{{ p.current_stock }}</strong></p>
                <div class="mb-3"><label class="form-label">Add Quantity</label><input type="number" name="quantity" class="form-control" min="1" required></div>
                <div class="mb-3"><label class="form-label">New Expiry (optional)</label><input type="date" name="expiry_date" class="form-control"></div>
            </div><div class="modal-footer"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button><button type="submit" class="btn btn-success">Restock</button></div></form>
        </div></div></div>
        {% endfor %}
    </div>
    {% if not products %}<div class="empty-state text-center py-5"><i class="fas fa-boxes fa-3x text-muted mb-3"></i><h4>No products</h4><a href="/add_product" class="btn btn-primary mt-2">Add Product</a></div>{% endif %}
</div>
{% endblock %}
"""

ADD_PRODUCT_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container py-4"><div class="row justify-content-center"><div class="col-lg-8">
    <div class="chart-card">
        <div class="chart-header"><h5><i class="fas fa-plus-circle me-2"></i>{{ 'Edit' if product else 'Add' }} Product</h5></div>
        <div class="chart-body"><form method="POST"><div class="row g-3">
            <div class="col-md-8"><label class="form-label">Name *</label><input type="text" name="name" class="form-control" value="{{ product.name if product else '' }}" required></div>
            <div class="col-md-4"><label class="form-label">Category *</label><select name="category_id" class="form-select" required>{% for c in categories %}<option value="{{ c.id }}" {{ 'selected' if product and product.category_id == c.id }}>{{ c.icon }} {{ c.name }}</option>{% endfor %}</select></div>
            <div class="col-12"><label class="form-label">Description</label><textarea name="description" class="form-control" rows="2">{{ product.description if product else '' }}</textarea></div>
            <div class="col-md-4"><label class="form-label">Price (₹) *</label><input type="number" name="unit_price" class="form-control" step="0.01" min="0" value="{{ product.unit_price if product else '' }}" required></div>
            <div class="col-md-4"><label class="form-label">Stock *</label><input type="number" name="current_stock" class="form-control" min="0" value="{{ product.current_stock if product else 0 }}" required></div>
            <div class="col-md-4"><label class="form-label">Unit</label><select name="unit" class="form-select"><option value="tablets" {{ 'selected' if product and product.unit == 'tablets' }}>Tablets</option><option value="capsules" {{ 'selected' if product and product.unit == 'capsules' }}>Capsules</option><option value="bottles" {{ 'selected' if product and product.unit == 'bottles' }}>Bottles</option><option value="units" {{ 'selected' if product and product.unit == 'units' }}>Units</option><option value="strips" {{ 'selected' if product and product.unit == 'strips' }}>Strips</option><option value="tubes" {{ 'selected' if product and product.unit == 'tubes' }}>Tubes</option><option value="ml" {{ 'selected' if product and product.unit == 'ml' }}>ML</option><option value="grams" {{ 'selected' if product and product.unit == 'grams' }}>Grams</option><option value="vials" {{ 'selected' if product and product.unit == 'vials' }}>Vials</option></select></div>
            <div class="col-md-4"><label class="form-label">Min Stock</label><input type="number" name="minimum_stock" class="form-control" min="0" value="{{ product.minimum_stock if product else 10 }}"></div>
            <div class="col-md-4"><label class="form-label">Expiry Date</label><input type="date" name="expiry_date" class="form-control" value="{{ product.expiry_date.strftime('%Y-%m-%d') if product and product.expiry_date else '' }}"></div>
            <div class="col-md-4"><label class="form-label">Manufacturer</label><input type="text" name="manufacturer" class="form-control" value="{{ product.manufacturer if product else '' }}"></div>
            <div class="col-md-4"><div class="form-check mt-4"><input type="checkbox" name="is_prescription" class="form-check-input" id="isPrescription" {{ 'checked' if product and product.is_prescription }}><label class="form-check-label" for="isPrescription">Prescription Required</label></div></div>
        </div>
        <div class="mt-4 d-flex gap-2"><button type="submit" class="btn btn-primary btn-lg"><i class="fas fa-save me-2"></i>{{ 'Update' if product else 'Add' }}</button><a href="/inventory" class="btn btn-outline-secondary btn-lg">Back</a></div>
        </form></div>
    </div>
</div></div></div>
{% endblock %}
"""

BILLING_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container-fluid px-4 py-4">
    <div class="page-header mb-4"><div><h2><i class="fas fa-file-invoice-dollar me-2"></i>Billing</h2></div></div>
    <div class="row g-4">
        <div class="col-lg-7">
            <div class="chart-card"><div class="chart-header"><h5><i class="fas fa-plus me-2"></i>New Bill</h5></div>
                <div class="chart-body"><form method="POST" action="/create_bill" id="billForm">
                    <div class="row g-3 mb-3">
                        <div class="col-md-6"><label class="form-label">Customer</label>
                            <select name="customer_id" id="customerSelect" class="form-select" required>
                                <option value="">Select customer</option>
                                {% for c in customers %}<option value="{{ c.id }}">{{ c.name }} ({{ c.phone }})</option>{% endfor %}
                                <option value="new">+ New Customer</option>
                            </select></div>
                        <div class="col-md-6" id="newCustomerFields" style="display:none;">
                            <label class="form-label">New Customer</label>
                            <input type="text" name="new_customer_name" class="form-control mb-1" placeholder="Name">
                            <input type="tel" name="new_customer_phone" class="form-control" placeholder="Phone">
                        </div>
                    </div>
                    <h6><i class="fas fa-pills me-2"></i>Items</h6>
                    <div id="billItems">
                        <div class="bill-item-row" id="billRow0">
                            <select name="product_0" class="form-select form-select-sm product-select" onchange="updatePrice(0)" style="flex:3;" required>
                                <option value="">Select medicine</option>
                                {% for p in products %}<option value="{{ p.id }}" data-price="{{ p.unit_price }}" data-stock="{{ p.current_stock }}">{{ p.name }} (₹{{ "%.2f"|format(p.unit_price) }})</option>{% endfor %}
                            </select>
                            <input type="number" name="qty_0" class="form-control form-control-sm" value="1" min="1" onchange="updatePrice(0)" style="flex:1;">
                            <span class="fw-bold" id="price_0" style="flex:1;text-align:right;">₹0.00</span>
                            <button type="button" class="btn btn-sm btn-outline-danger" onclick="removeRow(0)"><i class="fas fa-times"></i></button>
                        </div>
                    </div>
                    <button type="button" class="btn btn-sm btn-outline-primary mt-2" onclick="addBillItem()"><i class="fas fa-plus me-1"></i>Add Item</button>
                    <div class="mt-3 p-3 bg-light rounded d-flex justify-content-between"><h5 class="mb-0">Total:</h5><h4 class="mb-0 text-primary" id="billTotal">₹0.00</h4></div>
                    <button type="submit" class="btn btn-success btn-lg w-100 mt-3"><i class="fas fa-check-circle me-2"></i>Create Bill</button>
                </form></div>
            </div>
        </div>
        <div class="col-lg-5">
            <div class="chart-card"><div class="chart-header"><h5><i class="fas fa-history me-2"></i>Recent Bills</h5></div>
                <div class="chart-body p-0"><table class="table table-hover mb-0"><thead><tr><th>#</th><th>Customer</th><th>Amount</th><th>Date</th><th></th></tr></thead><tbody>
                    {% for b in recent_bills %}<tr><td>#{{ b.id }}</td><td>{{ b.customer.name }}</td><td>₹{{ "%.2f"|format(b.total_amount) }}</td><td>{{ b.bill_date.strftime('%d %b') }}</td><td><a href="/bill_detail/{{ b.id }}" class="btn btn-sm btn-outline-primary"><i class="fas fa-eye"></i></a></td></tr>
                    {% else %}<tr><td colspan="5" class="text-center text-muted py-3">No bills</td></tr>{% endfor %}
                </tbody></table></div>
            </div>
        </div>
    </div>
</div>
{% endblock %}
{% block scripts %}
<script>
let itemCount = 1;
document.getElementById('customerSelect').addEventListener('change', function() {
    document.getElementById('newCustomerFields').style.display = this.value === 'new' ? 'block' : 'none';
});
function addBillItem() {
    const idx = itemCount++;
    const products = document.querySelector('.product-select').innerHTML;
    const row = document.createElement('div');
    row.className = 'bill-item-row'; row.id = 'billRow' + idx;
    row.innerHTML = '<select name="product_'+idx+'" class="form-select form-select-sm product-select" onchange="updatePrice('+idx+')" style="flex:3;" required>'+products+'</select><input type="number" name="qty_'+idx+'" class="form-control form-control-sm" value="1" min="1" onchange="updatePrice('+idx+')" style="flex:1;"><span class="fw-bold" id="price_'+idx+'" style="flex:1;text-align:right;">₹0.00</span><button type="button" class="btn btn-sm btn-outline-danger" onclick="removeRow('+idx+')"><i class="fas fa-times"></i></button>';
    document.getElementById('billItems').appendChild(row);
}
function removeRow(idx) { const row = document.getElementById('billRow'+idx); if(row && document.querySelectorAll('.bill-item-row').length > 1) { row.remove(); calcTotal(); } }
function updatePrice(idx) {
    const sel = document.querySelector('[name="product_'+idx+'"]');
    const qty = document.querySelector('[name="qty_'+idx+'"]');
    if(sel && qty && sel.value) { const price = parseFloat(sel.options[sel.selectedIndex].dataset.price)||0; document.getElementById('price_'+idx).textContent = '₹'+(price*parseInt(qty.value||1)).toFixed(2); }
    calcTotal();
}
function calcTotal() { let t=0; document.querySelectorAll('[id^="price_"]').forEach(el=>{t+=parseFloat(el.textContent.replace('₹',''))||0;}); document.getElementById('billTotal').textContent='₹'+t.toFixed(2); }
</script>
{% endblock %}
"""

BILL_DETAIL_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container py-4"><div class="row justify-content-center"><div class="col-lg-8">
    <div class="chart-card">
        <div class="chart-header d-flex justify-content-between"><h5><i class="fas fa-receipt me-2"></i>Bill #{{ bill.id }}</h5><button class="btn btn-sm btn-outline-primary" onclick="window.print()"><i class="fas fa-print me-1"></i>Print</button></div>
        <div class="chart-body">
            <div class="row mb-4">
                <div class="col-md-6"><h6 class="text-muted">Customer</h6><p><strong>{{ bill.customer.name }}</strong><br><i class="fas fa-phone me-1"></i>{{ bill.customer.phone }}</p></div>
                <div class="col-md-6 text-md-end"><h6 class="text-muted">Date</h6><p>{{ bill.bill_date.strftime('%d %b %Y %H:%M') }}</p></div>
            </div>
            <table class="table"><thead><tr><th>#</th><th>Medicine</th><th>Qty</th><th>Price</th><th>Total</th></tr></thead><tbody>
                {% for item in bill.items %}<tr><td>{{ loop.index }}</td><td>{{ item.product.name }}</td><td>{{ item.quantity }}</td><td>₹{{ "%.2f"|format(item.unit_price) }}</td><td>₹{{ "%.2f"|format(item.total_price) }}</td></tr>{% endfor %}
            </tbody><tfoot><tr class="table-primary"><td colspan="4" class="text-end fw-bold">Total:</td><td class="fw-bold fs-5">₹{{ "%.2f"|format(bill.total_amount) }}</td></tr></tfoot></table>
            <a href="/billing" class="btn btn-outline-secondary"><i class="fas fa-arrow-left me-2"></i>Back</a>
        </div>
    </div>
</div></div></div>
{% endblock %}
"""

CUSTOMERS_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container-fluid px-4 py-4">
    <div class="page-header mb-4"><div><h2><i class="fas fa-users me-2"></i>Customers</h2></div>
        <button class="btn btn-primary" data-bs-toggle="modal" data-bs-target="#addCustomerModal"><i class="fas fa-user-plus me-2"></i>Add</button></div>
    <div class="row g-3">
        {% for c in customers %}
        <div class="col-xl-4 col-md-6">
            <div class="customer-card">
                <div class="d-flex justify-content-between mb-2">
                    <div><h5 class="mb-1"><i class="fas fa-user me-2 text-primary"></i>{{ c.name }}</h5><p class="text-muted mb-0">{{ c.phone }}</p></div>
                    {% if c.is_monthly %}<span class="monthly-badge"><i class="fas fa-sync me-1"></i>Monthly</span>{% endif %}
                </div>
                {% if c.monthly_medicines %}<div class="mb-2"><small class="fw-bold">Monthly:</small><br>{% for mm in c.monthly_medicines %}<span class="badge bg-light text-dark border me-1">{{ mm.product.name }} x{{ mm.quantity }}</span>{% endfor %}</div>{% endif %}
                <div class="d-flex gap-2 mt-2">
                    <a href="/customer_detail/{{ c.id }}" class="btn btn-sm btn-outline-primary flex-fill"><i class="fas fa-eye me-1"></i>View</a>
                    <button class="btn btn-sm btn-outline-success flex-fill" data-bs-toggle="modal" data-bs-target="#monthlyModal{{ c.id }}"><i class="fas fa-sync me-1"></i>Monthly</button>
                </div>
            </div>
        </div>
        <div class="modal fade" id="monthlyModal{{ c.id }}" tabindex="-1"><div class="modal-dialog"><div class="modal-content">
            <div class="modal-header"><h5>Monthly: {{ c.name }}</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
            <form method="POST" action="/add_monthly_medicine/{{ c.id }}"><div class="modal-body">
                {% if c.monthly_medicines %}<ul class="list-group mb-3">{% for mm in c.monthly_medicines %}<li class="list-group-item d-flex justify-content-between">{{ mm.product.name }} x{{ mm.quantity }}<a href="/remove_monthly_medicine/{{ mm.id }}" class="btn btn-sm btn-outline-danger"><i class="fas fa-times"></i></a></li>{% endfor %}</ul>{% endif %}
                <select name="product_id" class="form-select mb-2" required><option value="">Select</option>{% for p in products %}<option value="{{ p.id }}">{{ p.name }}</option>{% endfor %}</select>
                <input type="number" name="quantity" class="form-control" min="1" value="1" required>
            </div><div class="modal-footer"><button type="submit" class="btn btn-success">Add</button></div></form>
        </div></div></div>
        {% endfor %}
    </div>
    {% if not customers %}<div class="empty-state text-center py-5"><h4>No customers</h4></div>{% endif %}
</div>
<div class="modal fade" id="addCustomerModal" tabindex="-1"><div class="modal-dialog"><div class="modal-content">
    <div class="modal-header"><h5>Add Customer</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
    <form method="POST" action="/add_customer"><div class="modal-body">
        <div class="mb-3"><label class="form-label">Name *</label><input type="text" name="name" class="form-control" required></div>
        <div class="mb-3"><label class="form-label">Phone *</label><input type="tel" name="phone" class="form-control" required></div>
        <div class="form-check"><input type="checkbox" name="is_monthly" class="form-check-input" id="isMC"><label class="form-check-label" for="isMC">Monthly Customer</label></div>
    </div><div class="modal-footer"><button type="submit" class="btn btn-primary">Add</button></div></form>
</div></div></div>
{% endblock %}
"""

CUSTOMER_DETAIL_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container py-4"><div class="row justify-content-center"><div class="col-lg-10">
    <div class="chart-card mb-4">
        <div class="chart-header d-flex justify-content-between"><h5><i class="fas fa-user me-2"></i>{{ customer.name }}</h5>{% if customer.is_monthly %}<span class="monthly-badge">Monthly</span>{% endif %}</div>
        <div class="chart-body"><div class="row"><div class="col-md-4"><p><i class="fas fa-phone me-2"></i>{{ customer.phone }}</p></div><div class="col-md-4"><p>Since: {{ customer.created_at.strftime('%d %b %Y') }}</p></div><div class="col-md-4"><p>Bills: {{ customer.bills|length }}</p></div></div></div>
    </div>
    {% if customer.monthly_medicines %}<div class="chart-card mb-4"><div class="chart-header"><h5>Monthly Medicines</h5></div><div class="chart-body"><div class="row g-2">{% for mm in customer.monthly_medicines %}<div class="col-md-4"><div class="p-3 border rounded"><strong>{{ mm.product.name }}</strong><br>Qty: {{ mm.quantity }}</div></div>{% endfor %}</div></div></div>{% endif %}
    <div class="chart-card"><div class="chart-header"><h5>Bill History</h5></div>
        <div class="chart-body p-0"><table class="table mb-0"><thead><tr><th>#</th><th>Amount</th><th>Date</th><th></th></tr></thead><tbody>
            {% for b in customer.bills %}<tr><td>#{{ b.id }}</td><td>₹{{ "%.2f"|format(b.total_amount) }}</td><td>{{ b.bill_date.strftime('%d %b %Y') }}</td><td><a href="/bill_detail/{{ b.id }}" class="btn btn-sm btn-outline-primary"><i class="fas fa-eye"></i></a></td></tr>{% else %}<tr><td colspan="4" class="text-center text-muted py-3">No bills</td></tr>{% endfor %}
        </tbody></table></div>
    </div>
    <a href="/customers" class="btn btn-outline-secondary mt-3">Back</a>
</div></div></div>
{% endblock %}
"""

PHARMACIST_ORDERS_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container-fluid px-4 py-4">
    <div class="page-header mb-4"><div><h2><i class="fas fa-shopping-bag me-2"></i>Orders</h2></div></div>
    <ul class="nav nav-tabs mb-4">
        <li class="nav-item"><a class="nav-link {{ 'active' if status_filter == 'pending' }}" href="/pharmacist_orders?status=pending">Pending <span class="badge bg-warning text-dark">{{ counts.pending }}</span></a></li>
        <li class="nav-item"><a class="nav-link {{ 'active' if status_filter == 'confirmed' }}" href="/pharmacist_orders?status=confirmed">Confirmed <span class="badge bg-info">{{ counts.confirmed }}</span></a></li>
        <li class="nav-item"><a class="nav-link {{ 'active' if status_filter == 'ready' }}" href="/pharmacist_orders?status=ready">Ready <span class="badge bg-success">{{ counts.ready }}</span></a></li>
        <li class="nav-item"><a class="nav-link {{ 'active' if not status_filter or status_filter == 'all' }}" href="/pharmacist_orders?status=all">All</a></li>
    </ul>
    <div class="chart-card"><div class="chart-body p-0"><table class="table table-hover mb-0"><thead><tr><th>#</th><th>Patient</th><th>Items</th><th>Total</th><th>Type</th><th>Status</th><th>Date</th><th>Actions</th></tr></thead><tbody>
        {% for order in orders %}
        <tr>
            <td>#{{ order.id }}</td>
            <td>{{ order.user.full_name }}<br><small class="text-muted">{{ order.user.phone or '' }}</small></td>
            <td>{% for item in order.items %}<small>{{ item.product.name }} x{{ item.quantity }}</small><br>{% endfor %}</td>
            <td>₹{{ "%.2f"|format(order.total_amount) }}</td>
            <td>{% if order.is_monthly %}<span class="monthly-badge">Monthly</span>{% else %}<span class="badge bg-secondary">One-time</span>{% endif %}</td>
            <td><span class="order-status-badge status-{{ order.status }}">{{ order.status|title }}</span></td>
            <td>{{ order.order_date.strftime('%d %b %H:%M') }}</td>
            <td><div class="d-flex gap-1">
                {% if order.status == 'pending' %}
                <form method="POST" action="/update_order_status/{{ order.id }}" class="d-inline"><input type="hidden" name="status" value="confirmed"><button class="btn btn-sm btn-success"><i class="fas fa-check"></i></button></form>
                <form method="POST" action="/update_order_status/{{ order.id }}" class="d-inline"><input type="hidden" name="status" value="cancelled"><button class="btn btn-sm btn-danger"><i class="fas fa-times"></i></button></form>
                {% elif order.status == 'confirmed' %}
                <form method="POST" action="/update_order_status/{{ order.id }}" class="d-inline"><input type="hidden" name="status" value="ready"><button class="btn btn-sm btn-info"><i class="fas fa-box"></i></button></form>
                {% elif order.status == 'ready' %}
                <form method="POST" action="/update_order_status/{{ order.id }}" class="d-inline"><input type="hidden" name="status" value="delivered"><button class="btn btn-sm btn-success"><i class="fas fa-truck"></i></button></form>
                {% endif %}
                <a href="/create_bill_from_order/{{ order.id }}" class="btn btn-sm btn-outline-primary"><i class="fas fa-file-invoice"></i></a>
            </div></td>
        </tr>
        {% else %}<tr><td colspan="8" class="text-center text-muted py-4">No orders</td></tr>{% endfor %}
    </tbody></table></div></div>
</div>
{% endblock %}
"""

NOTIFICATIONS_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container py-4"><div class="row justify-content-center"><div class="col-lg-8">
    <div class="page-header mb-4"><div><h2><i class="fas fa-bell me-2"></i>Notifications</h2></div>
        {% if notifications %}<a href="/mark_all_read" class="btn btn-outline-primary btn-sm">Mark All Read</a>{% endif %}</div>
    <div class="chart-card"><div class="chart-body p-0">
        {% for n in notifications %}
        <div class="notification-item {{ 'unread' if not n.is_read }}">
            <div class="d-flex justify-content-between">
                <div><h6 class="mb-1"><i class="fas fa-{{ 'shopping-bag' if n.type == 'order' else 'bell' }} me-2"></i>{{ n.title }}</h6><p class="mb-1 text-muted">{{ n.message }}</p><small class="text-muted">{{ n.created_at.strftime('%d %b %Y %H:%M') }}</small></div>
                <div>{% if not n.is_read %}<a href="/mark_read/{{ n.id }}" class="btn btn-sm btn-outline-secondary"><i class="fas fa-check"></i></a>{% endif %}</div>
            </div>
        </div>
        {% else %}<div class="text-center py-5 text-muted"><i class="fas fa-bell-slash fa-3x mb-3"></i><h5>No notifications</h5></div>{% endfor %}
    </div></div>
</div></div></div>
{% endblock %}
"""

# ============================================================
# DOCTOR UI TEMPLATES
# ============================================================

DASHBOARD_DOCTOR_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container-fluid px-4 py-4">
    <div class="page-header mb-4"><div><h2><i class="fas fa-user-md me-2"></i>Doctor Dashboard</h2><p class="text-muted">Dr. {{ current_user.full_name }}</p></div></div>
    <div class="row g-4 mb-4">
        <div class="col-md-4"><div class="stat-card-modern bg-gradient-blue"><div class="stat-card-body"><div class="stat-card-icon"><i class="fas fa-store"></i></div><div class="stat-card-info"><h3>{{ stats.nearby_pharmacies }}</h3><p>Nearby Pharmacies</p></div></div></div></div>
        <div class="col-md-4"><div class="stat-card-modern bg-gradient-green"><div class="stat-card-body"><div class="stat-card-icon"><i class="fas fa-pills"></i></div><div class="stat-card-info"><h3>{{ stats.total_medicines }}</h3><p>Medicines Available</p></div></div></div></div>
        <div class="col-md-4"><div class="stat-card-modern bg-gradient-purple"><div class="stat-card-body"><div class="stat-card-icon"><i class="fas fa-tags"></i></div><div class="stat-card-info"><h3>{{ stats.total_categories }}</h3><p>Categories</p></div></div></div></div>
    </div>
    <div class="row g-4">
        <div class="col-md-6"><div class="chart-card"><div class="chart-header"><h5>Quick Actions</h5></div><div class="chart-body"><div class="d-grid gap-2">
            <a href="/check_medicines" class="btn btn-outline-primary btn-lg"><i class="fas fa-search me-2"></i>Check Medicines</a>
            <a href="/nearby_pharmacies" class="btn btn-outline-success btn-lg"><i class="fas fa-map-marker-alt me-2"></i>Nearby Pharmacies</a>
        </div></div></div></div>
        <div class="col-md-6"><div class="chart-card"><div class="chart-header"><h5>Nearby Pharmacies</h5></div>
            <div class="chart-body p-0"><table class="table mb-0"><thead><tr><th>Pharmacy</th><th>Distance</th><th>Medicines</th></tr></thead><tbody>
                {% for item in stats.nearby_list[:5] %}<tr><td>{{ item.pharmacist.organization or item.pharmacist.full_name }}</td><td><span class="distance-badge">{{ item.distance }} km</span></td><td>{{ item.product_count }}</td></tr>
                {% else %}<tr><td colspan="3" class="text-center text-muted py-3">Enable location</td></tr>{% endfor %}
            </tbody></table></div>
        </div></div>
    </div>
</div>
{% endblock %}
"""

CHECK_MEDICINES_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container-fluid px-4 py-4">
    <div class="page-header mb-4"><div><h2><i class="fas fa-pills me-2"></i>Check Medicines</h2></div></div>
    <div class="filter-bar mb-4"><form method="GET" class="row g-3 align-items-end">
        <div class="col-md-4"><label class="form-label">Search</label><input type="text" name="search" class="form-control" placeholder="Medicine name..." value="{{ search }}"></div>
        <div class="col-md-3"><label class="form-label">Category</label><select name="category" class="form-select"><option value="">All</option>{% for c in categories %}<option value="{{ c.id }}" {{ 'selected' if selected_category == c.id }}>{{ c.icon }} {{ c.name }}</option>{% endfor %}</select></div>
        <div class="col-md-2"><button type="submit" class="btn btn-primary w-100">Search</button></div>
    </form></div>
    <div class="row g-3">
        {% for p in products %}
        <div class="col-xl-3 col-lg-4 col-md-6">
            <div class="product-card">
                <div class="product-header"><span class="product-category">{{ p.category.icon }} {{ p.category.name }}</span><span class="stock-badge stock-{{ p.stock_status }}">{{ p.stock_status|replace('_',' ')|title }}</span></div>
                <h5 class="product-name">{{ p.name }}</h5>
                <div class="product-details">
                    <div class="detail-row"><span>Price:</span><strong>₹{{ "%.2f"|format(p.unit_price) }}</strong></div>
                    <div class="detail-row"><span>Stock:</span><strong>{{ p.current_stock }} {{ p.unit }}</strong></div>
                    <div class="detail-row"><span>Pharmacy:</span><span>{{ p.pharmacist_name }}</span></div>
                </div>
            </div>
        </div>
        {% endfor %}
    </div>
    {% if not products %}<div class="empty-state text-center py-5"><h4>No medicines found</h4></div>{% endif %}
</div>
{% endblock %}
"""

PROFILE_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container py-4"><div class="row justify-content-center"><div class="col-lg-8">
    <div class="chart-card">
        <div class="chart-header"><h5><i class="fas fa-user me-2"></i>Profile</h5></div>
        <div class="chart-body">
            <div class="text-center mb-4">
                <div class="profile-avatar-lg">{{ current_user.full_name[0] if current_user.full_name else 'U' }}</div>
                <h4 class="mt-2">{{ current_user.full_name }}</h4>
                <span class="badge bg-primary fs-6">{{ current_user.role|title }}</span>
            </div>
            <form method="POST"><div class="row g-3">
                <div class="col-md-6"><label class="form-label">Full Name</label><input type="text" name="full_name" class="form-control" value="{{ current_user.full_name }}"></div>
                <div class="col-md-6"><label class="form-label">Email</label><input type="email" class="form-control" value="{{ current_user.email }}" disabled></div>
                <div class="col-md-6"><label class="form-label">Phone</label><input type="tel" name="phone" class="form-control" value="{{ current_user.phone or '' }}"></div>
                <div class="col-md-6"><label class="form-label">Organization</label><input type="text" name="organization" class="form-control" value="{{ current_user.organization or '' }}"></div>
                <div class="col-12"><label class="form-label">Address</label><textarea name="address" class="form-control" rows="2">{{ current_user.address or '' }}</textarea></div>
            </div>
            <button type="submit" class="btn btn-primary btn-lg mt-4"><i class="fas fa-save me-2"></i>Update</button>
            </form>
        </div>
    </div>
    <div class="chart-card mt-4">
        <div class="chart-header"><h5><i class="fas fa-map-marker-alt me-2"></i>Location</h5></div>
        <div class="chart-body">
            <p>Lat: {{ current_user.latitude or 'Not set' }} | Lng: {{ current_user.longitude or 'Not set' }}</p>
            <button class="btn btn-outline-primary" onclick="updateMyLocation()"><i class="fas fa-crosshairs me-2"></i>Update Location</button>
            <div id="locationMsg" class="mt-2"></div>
        </div>
    </div>
</div></div></div>
{% endblock %}
{% block scripts %}
<script>
function updateMyLocation() {
    if(navigator.geolocation) {
        navigator.geolocation.getCurrentPosition(function(pos) {
            fetch('/api/update_location', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lat:pos.coords.latitude,lng:pos.coords.longitude})}).then(r=>r.json()).then(data=>{
                if(data.success){document.getElementById('locationMsg').innerHTML='<div class="alert alert-success">Updated!</div>';setTimeout(()=>location.reload(),1500);}
            });
        },function(){document.getElementById('locationMsg').innerHTML='<div class="alert alert-warning">Denied</div>';});
    }
}
</script>
{% endblock %}
"""

# ============================================================
# TEMPLATE REGISTRY & RENDERING
# ============================================================

TEMPLATES = {}

class StringTemplateLoader(BaseLoader):
    def get_source(self, environment, template):
        if template in TEMPLATES:
            return TEMPLATES[template], template, lambda: True
        raise Jinja2TemplateNotFound(template)

def register_templates():
    global TEMPLATES
    TEMPLATES = {
        'base.html': BASE_HTML, 'index.html': INDEX_HTML, 'login.html': LOGIN_HTML,
        'register.html': REGISTER_HTML, 'dashboard_patient.html': DASHBOARD_PATIENT_HTML,
        'browse_medicines.html': BROWSE_MEDICINES_HTML, 'upload_prescription.html': UPLOAD_PRESCRIPTION_HTML,
        'my_orders.html': MY_ORDERS_HTML, 'order_detail.html': ORDER_DETAIL_HTML,
        'nearby_pharmacies.html': NEARBY_PHARMACIES_HTML, 'dashboard_pharmacist.html': DASHBOARD_PHARMACIST_HTML,
        'inventory.html': INVENTORY_HTML, 'add_product.html': ADD_PRODUCT_HTML,
        'billing.html': BILLING_HTML, 'bill_detail.html': BILL_DETAIL_HTML,
        'customers.html': CUSTOMERS_HTML, 'customer_detail.html': CUSTOMER_DETAIL_HTML,
        'pharmacist_orders.html': PHARMACIST_ORDERS_HTML, 'notifications.html': NOTIFICATIONS_HTML,
        'dashboard_doctor.html': DASHBOARD_DOCTOR_HTML, 'check_medicines.html': CHECK_MEDICINES_HTML,
        'profile.html': PROFILE_HTML,
    }

def setup_template_loader():
    app.jinja_loader = StringTemplateLoader()

def render(template_string, **kwargs):
    user = get_current_user()
    kwargs['current_user'] = user
    kwargs['notif_count'] = get_unread_notification_count(user.id) if user else 0
    kwargs['request'] = request
    template_name = None
    for name, tmpl in TEMPLATES.items():
        if tmpl == template_string:
            template_name = name
            break
    if template_name:
        return render_template(template_name, **kwargs)
    else:
        return render_template_string(template_string, **kwargs)
    
# ============================================================
# PUBLIC ROUTES
# ============================================================

@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route('/')
def index():
    return render(INDEX_HTML, title='Gramin SmartCare',
                  total_products=Product.query.count(),
                  total_pharmacies=User.query.filter_by(role='pharmacist').count(),
                  total_doctors=User.query.filter_by(role='doctor').count(),
                  total_patients=User.query.filter_by(role='patient').count())

# ============================================================
# AUTH ROUTES
# ============================================================

@app.route('/login')
def login():
    if get_current_user(): return redirect(url_for('dashboard'))
    return render(LOGIN_HTML, title='Login')

@app.route('/register')
def register():
    if get_current_user(): return redirect(url_for('dashboard'))
    return render(REGISTER_HTML, title='Register')

@app.route('/auth/login', methods=['POST'])
def auth_login():
    try:
        data = request.get_json()
        id_token = data.get('id_token')
        email = data.get('email')
        if not id_token or not email:
            return jsonify({'success': False, 'error': 'Missing credentials'}), 400
        firebase_uid = None
        try:
            decoded = firebase_auth.verify_id_token(id_token)
            firebase_uid = decoded['uid']
        except Exception as e:
            print(f"Token verify warning: {e}")
        user = User.query.filter_by(email=email).first()
        if not user:
            return jsonify({'success': False, 'error': 'Account not found. Register first.'}), 404
        if firebase_uid and (not user.firebase_uid or user.firebase_uid != firebase_uid):
            user.firebase_uid = firebase_uid
            db.session.commit()
        session['user_id'] = user.id
        session['user_role'] = user.role
        session.permanent = True
        sync_user_to_firestore(user)
        return jsonify({'success': True, 'redirect': '/dashboard'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/auth/register', methods=['POST'])
def auth_register():
    try:
        data = request.get_json()
        email = data.get('email')
        role = data.get('role')
        full_name = data.get('full_name')
        username = data.get('username')
        if not all([email, role, full_name, username]):
            return jsonify({'success': False, 'error': 'Missing fields'}), 400
        if User.query.filter_by(email=email).first():
            return jsonify({'success': False, 'error': 'Email exists'}), 400
        if User.query.filter_by(username=username).first():
            return jsonify({'success': False, 'error': 'Username taken'}), 400
        firebase_uid = data.get('uid', f"local_{email}")
        try:
            if data.get('id_token'):
                decoded = firebase_auth.verify_id_token(data['id_token'])
                firebase_uid = decoded['uid']
        except: pass
        user = User(
            firebase_uid=firebase_uid, username=username, email=email, role=role,
            full_name=full_name, phone=data.get('phone'), organization=data.get('organization'),
            address=data.get('address'),
            latitude=float(data['latitude']) if data.get('latitude') else None,
            longitude=float(data['longitude']) if data.get('longitude') else None
        )
        user.set_password('firebase_auth')
        db.session.add(user)
        db.session.commit()
        session['user_id'] = user.id
        session['user_role'] = user.role
        session.permanent = True
        sync_user_to_firestore(user)
        return jsonify({'success': True, 'redirect': '/dashboard'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out.', 'info')
    return redirect(url_for('index'))

# ============================================================
# DASHBOARD
# ============================================================

@app.route('/dashboard')
@login_required
def dashboard():
    user = get_current_user()
    if user.role == 'pharmacist': return pharmacist_dashboard(user)
    elif user.role == 'doctor': return doctor_dashboard(user)
    elif user.role == 'patient': return patient_dashboard(user)
    return redirect(url_for('index'))

def pharmacist_dashboard(user):
    products = Product.query.filter_by(added_by=user.id).all()
    today = datetime.now(timezone.utc).date()
    today_bills = Bill.query.filter_by(created_by=user.id).filter(func.date(Bill.bill_date)==today).all()
    sales_labels, sales_data = [], []
    for i in range(6,-1,-1):
        day = today - timedelta(days=i)
        sales_labels.append(day.strftime('%a'))
        sales_data.append(sum(b.total_amount for b in Bill.query.filter_by(created_by=user.id).filter(func.date(Bill.bill_date)==day).all()))
    stats = {
        'total_products': len(products),
        'low_stock': len([p for p in products if p.stock_status in ['critical','low','out_of_stock']]),
        'low_stock_items': [p for p in products if p.stock_status in ['critical','low','out_of_stock']][:10],
        'expiring_soon': [p for p in products if p.expiry_status in ['expired','expiring_soon']][:10],
        'pending_orders': Order.query.filter_by(pharmacist_id=user.id, status='pending').count(),
        'recent_orders': Order.query.filter_by(pharmacist_id=user.id).order_by(desc(Order.order_date)).limit(5).all(),
        'today_revenue': sum(b.total_amount for b in today_bills),
        'sales_labels': sales_labels, 'sales_data': sales_data
    }
    return render(DASHBOARD_PHARMACIST_HTML, title='Dashboard', stats=stats)

def doctor_dashboard(user):
    nearby_list = get_nearby_pharmacies(user.latitude, user.longitude) if user.latitude and user.longitude else []
    stats = {'nearby_pharmacies': len(nearby_list), 'total_medicines': Product.query.filter(Product.current_stock>0).count(),
             'total_categories': Category.query.count(), 'nearby_list': nearby_list}
    return render(DASHBOARD_DOCTOR_HTML, title='Dashboard', stats=stats)

def patient_dashboard(user):
    orders = Order.query.filter_by(user_id=user.id).all()
    nearby = len(get_nearby_pharmacies(user.latitude, user.longitude)) if user.latitude and user.longitude else 0
    stats = {'total_orders': len(orders), 'pending_orders': len([o for o in orders if o.status=='pending']),
             'monthly_orders': len([o for o in orders if o.is_monthly]), 'nearby_pharmacies': nearby,
             'recent_orders': Order.query.filter_by(user_id=user.id).order_by(desc(Order.order_date)).limit(5).all()}
    return render(DASHBOARD_PATIENT_HTML, title='Dashboard', stats=stats)

# ============================================================
# PROFILE
# ============================================================

@app.route('/profile', methods=['GET','POST'])
@login_required
def profile():
    user = get_current_user()
    if request.method == 'POST':
        user.full_name = request.form.get('full_name', user.full_name)
        user.phone = request.form.get('phone', user.phone)
        user.organization = request.form.get('organization', user.organization)
        user.address = request.form.get('address', user.address)
        db.session.commit()
        sync_user_to_firestore(user)
        flash('Updated!', 'success')
        return redirect(url_for('profile'))
    return render(PROFILE_HTML, title='Profile')

# ============================================================
# PATIENT ROUTES
# ============================================================

@app.route('/browse_medicines')
@login_required
def browse_medicines():
    user = get_current_user()
    search = request.args.get('search', '')
    selected_category = request.args.get('category', type=int)
    selected_pharmacy = request.args.get('pharmacy_id', type=int)
    categories = Category.query.all()
    pharmacies = []
    all_pharmacists = User.query.filter_by(role='pharmacist').filter(User.latitude.isnot(None)).all()
    for p in all_pharmacists:
        p.distance = round(haversine(user.latitude, user.longitude, p.latitude, p.longitude), 1) if user.latitude and user.longitude else None
        pharmacies.append(p)
    if user.latitude: pharmacies.sort(key=lambda x: x.distance if x.distance else 999)
    query = Product.query.filter(Product.current_stock > 0)
    if selected_pharmacy: query = query.filter_by(added_by=selected_pharmacy)
    elif pharmacies: query = query.filter(Product.added_by.in_([p.id for p in pharmacies[:20]]))
    if search: query = query.filter(Product.name.ilike(f'%{search}%'))
    if selected_category: query = query.filter_by(category_id=selected_category)
    products = query.all()
    for p in products:
        ph = db.session.get(User, p.added_by)
        p.pharmacist_name = ph.organization or ph.full_name if ph else 'Unknown'
    return render(BROWSE_MEDICINES_HTML, title='Medicines', products=products, categories=categories,
                  pharmacies=pharmacies, search=search, selected_category=selected_category, selected_pharmacy=selected_pharmacy)

@app.route('/add_to_order', methods=['POST'])
@login_required
def add_to_order():
    user = get_current_user()
    product_id = request.form.get('product_id', type=int)
    quantity = request.form.get('quantity', 1, type=int)
    is_monthly = 'is_monthly' in request.form
    product = Product.query.get_or_404(product_id)
    if product.current_stock < quantity:
        flash('Not enough stock.', 'danger'); return redirect(url_for('browse_medicines'))
    existing = Order.query.filter_by(user_id=user.id, pharmacist_id=product.added_by, status='pending').first()
    if existing:
        ei = OrderItem.query.filter_by(order_id=existing.id, product_id=product_id).first()
        if ei: ei.quantity += quantity; ei.total_price = ei.quantity * ei.unit_price
        else: db.session.add(OrderItem(order_id=existing.id, product_id=product_id, quantity=quantity, unit_price=product.unit_price, total_price=product.unit_price*quantity))
        existing.total_amount = sum(i.total_price for i in existing.items)
        if is_monthly: existing.is_monthly = True
    else:
        order = Order(user_id=user.id, pharmacist_id=product.added_by, status='pending', is_monthly=is_monthly, total_amount=product.unit_price*quantity)
        db.session.add(order); db.session.flush()
        db.session.add(OrderItem(order_id=order.id, product_id=product_id, quantity=quantity, unit_price=product.unit_price, total_price=product.unit_price*quantity))
        db.session.add(Notification(user_id=product.added_by, title='New Order!', message=f'{user.full_name} ordered {quantity}x {product.name}', type='order', order_id=order.id))
    db.session.commit()
    sync_order_to_firestore(existing if existing else order)
    flash(f'{product.name} x{quantity} ordered!', 'success')
    return redirect(url_for('browse_medicines'))

@app.route('/upload_prescription')
@login_required
def upload_prescription():
    user = get_current_user()
    pharmacies = []
    if user.latitude and user.longitude:
        for item in get_nearby_pharmacies(user.latitude, user.longitude):
            p = item['pharmacist']; p.distance = item['distance']; pharmacies.append(p)
    else:
        pharmacies = User.query.filter_by(role='pharmacist').all()
        for p in pharmacies: p.distance = '?'
    return render(UPLOAD_PRESCRIPTION_HTML, title='Prescription', pharmacies=pharmacies)

@app.route('/my_orders')
@login_required
def my_orders():
    return render(MY_ORDERS_HTML, title='My Orders', orders=Order.query.filter_by(user_id=get_current_user().id).order_by(desc(Order.order_date)).all())

@app.route('/order_detail/<int:order_id>')
@login_required
def order_detail(order_id):
    user = get_current_user()
    order = Order.query.get_or_404(order_id)
    if order.user_id != user.id and order.pharmacist_id != user.id:
        flash('Denied.', 'danger'); return redirect(url_for('dashboard'))
    return render(ORDER_DETAIL_HTML, title=f'Order #{order.id}', order=order)

@app.route('/nearby_pharmacies')
@login_required
def nearby_pharmacies():
    user = get_current_user()
    nearby = get_nearby_pharmacies(user.latitude, user.longitude) if user.latitude and user.longitude else []
    return render(NEARBY_PHARMACIES_HTML, title='Nearby', nearby=nearby)

# ============================================================
# PHARMACIST ROUTES
# ============================================================

@app.route('/inventory')
@pharmacist_required
def inventory():
    user = get_current_user()
    search = request.args.get('search', '')
    selected_category = request.args.get('category', type=int)
    stock_filter = request.args.get('filter', '')
    expiry_filter = request.args.get('expiry', '')
    categories = Category.query.all()
    query = Product.query.filter_by(added_by=user.id)
    if search: query = query.filter(Product.name.ilike(f'%{search}%'))
    if selected_category: query = query.filter_by(category_id=selected_category)
    products = query.order_by(Product.name).all()
    if stock_filter == 'low': products = [p for p in products if p.stock_status in ['low','critical']]
    elif stock_filter == 'critical': products = [p for p in products if p.stock_status == 'critical']
    elif stock_filter == 'out': products = [p for p in products if p.stock_status == 'out_of_stock']
    if expiry_filter == 'expired': products = [p for p in products if p.expiry_status == 'expired']
    elif expiry_filter == 'expiring_soon': products = [p for p in products if p.expiry_status == 'expiring_soon']
    return render(INVENTORY_HTML, title='Inventory', products=products, categories=categories,
                  search=search, selected_category=selected_category, stock_filter=stock_filter, expiry_filter=expiry_filter)

@app.route('/add_product', methods=['GET','POST'])
@pharmacist_required
def add_product():
    user = get_current_user()
    categories = Category.query.all()
    if request.method == 'POST':
        exp = request.form.get('expiry_date')
        p = Product(name=request.form['name'], category_id=request.form['category_id'], description=request.form.get('description',''),
            unit_price=float(request.form['unit_price']), current_stock=int(request.form.get('current_stock',0)),
            minimum_stock=int(request.form.get('minimum_stock',10)), unit=request.form.get('unit','tablets'),
            manufacturer=request.form.get('manufacturer',''), expiry_date=datetime.strptime(exp,'%Y-%m-%d').date() if exp else None,
            is_prescription='is_prescription' in request.form, added_by=user.id)
        db.session.add(p); db.session.commit(); sync_product_to_firestore(p)
        flash(f'{p.name} added!', 'success'); return redirect(url_for('inventory'))
    return render(ADD_PRODUCT_HTML, title='Add Product', categories=categories, product=None)

@app.route('/edit_product/<int:pid>', methods=['GET','POST'])
@pharmacist_required
def edit_product(pid):
    user = get_current_user()
    product = Product.query.get_or_404(pid)
    if product.added_by != user.id: flash('Denied.', 'danger'); return redirect(url_for('inventory'))
    categories = Category.query.all()
    if request.method == 'POST':
        product.name = request.form['name']; product.category_id = request.form['category_id']
        product.description = request.form.get('description',''); product.unit_price = float(request.form['unit_price'])
        product.current_stock = int(request.form.get('current_stock',0)); product.minimum_stock = int(request.form.get('minimum_stock',10))
        product.unit = request.form.get('unit','tablets'); product.manufacturer = request.form.get('manufacturer','')
        product.is_prescription = 'is_prescription' in request.form
        exp = request.form.get('expiry_date')
        product.expiry_date = datetime.strptime(exp,'%Y-%m-%d').date() if exp else None
        db.session.commit(); sync_product_to_firestore(product)
        flash('Updated!', 'success'); return redirect(url_for('inventory'))
    return render(ADD_PRODUCT_HTML, title='Edit Product', categories=categories, product=product)

@app.route('/restock_product/<int:pid>', methods=['POST'])
@pharmacist_required
def restock_product(pid):
    product = Product.query.get_or_404(pid)
    if product.added_by != get_current_user().id: flash('Denied.','danger'); return redirect(url_for('inventory'))
    qty = int(request.form.get('quantity',0))
    if qty > 0:
        product.current_stock += qty
        exp = request.form.get('expiry_date')
        if exp: product.expiry_date = datetime.strptime(exp,'%Y-%m-%d').date()
        db.session.commit(); sync_product_to_firestore(product)
        flash(f'Restocked +{qty}!', 'success')
    return redirect(url_for('inventory'))

@app.route('/billing')
@pharmacist_required
def billing():
    user = get_current_user()
    return render(BILLING_HTML, title='Billing',
                  customers=Customer.query.filter_by(added_by=user.id).all(),
                  products=Product.query.filter_by(added_by=user.id).filter(Product.current_stock>0).all(),
                  recent_bills=Bill.query.filter_by(created_by=user.id).order_by(desc(Bill.bill_date)).limit(10).all())

@app.route('/create_bill', methods=['POST'])
@pharmacist_required
def create_bill():
    user = get_current_user()
    cid = request.form.get('customer_id')
    if cid == 'new':
        c = Customer(name=request.form.get('new_customer_name',''), phone=request.form.get('new_customer_phone',''), added_by=user.id)
        db.session.add(c); db.session.flush(); cid = c.id
    else: cid = int(cid)
    bill = Bill(customer_id=cid, created_by=user.id, total_amount=0)
    db.session.add(bill); db.session.flush()
    total, i = 0, 0
    while True:
        pid = request.form.get(f'product_{i}')
        qty = request.form.get(f'qty_{i}')
        if pid is None: break
        if pid and qty:
            p = db.session.get(Product, int(pid))
            if p:
                q = int(qty); t = p.unit_price * q
                db.session.add(BillItem(bill_id=bill.id, product_id=p.id, quantity=q, unit_price=p.unit_price, total_price=t))
                p.current_stock = max(0, p.current_stock - q)
                db.session.add(SalesRecord(product_id=p.id, quantity=q, total_price=t, buyer_type='walk-in'))
                total += t; sync_product_to_firestore(p)
        i += 1
    bill.total_amount = total; db.session.commit(); sync_bill_to_firestore(bill)
    flash(f'Bill #{bill.id} - ₹{total:.2f}!', 'success')
    return redirect(url_for('bill_detail', bill_id=bill.id))

@app.route('/bill_detail/<int:bill_id>')
@pharmacist_required
def bill_detail(bill_id):
    return render(BILL_DETAIL_HTML, title=f'Bill #{bill_id}', bill=Bill.query.get_or_404(bill_id))

@app.route('/customers')
@pharmacist_required
def customers():
    user = get_current_user()
    return render(CUSTOMERS_HTML, title='Customers',
                  customers=Customer.query.filter_by(added_by=user.id).all(),
                  products=Product.query.filter_by(added_by=user.id).filter(Product.current_stock>0).all(),
                  search='', customer_filter='')

@app.route('/add_customer', methods=['POST'])
@pharmacist_required
def add_customer():
    db.session.add(Customer(name=request.form['name'], phone=request.form['phone'],
                            is_monthly='is_monthly' in request.form, added_by=get_current_user().id))
    db.session.commit(); flash('Added!', 'success')
    return redirect(url_for('customers'))

@app.route('/customer_detail/<int:cid>')
@pharmacist_required
def customer_detail(cid):
    return render(CUSTOMER_DETAIL_HTML, title='Customer', customer=Customer.query.get_or_404(cid))

@app.route('/add_monthly_medicine/<int:cid>', methods=['POST'])
@pharmacist_required
def add_monthly_medicine(cid):
    pid = request.form.get('product_id', type=int)
    qty = request.form.get('quantity', 1, type=int)
    if pid:
        ex = MonthlyMedicine.query.filter_by(customer_id=cid, product_id=pid).first()
        if ex: ex.quantity = qty
        else: db.session.add(MonthlyMedicine(customer_id=cid, product_id=pid, quantity=qty))
        Customer.query.get(cid).is_monthly = True
        db.session.commit(); flash('Added!', 'success')
    return redirect(url_for('customers'))

@app.route('/remove_monthly_medicine/<int:mid>')
@pharmacist_required
def remove_monthly_medicine(mid):
    db.session.delete(MonthlyMedicine.query.get_or_404(mid)); db.session.commit()
    return redirect(url_for('customers'))

@app.route('/pharmacist_orders')
@pharmacist_required
def pharmacist_orders():
    user = get_current_user()
    sf = request.args.get('status', 'pending')
    q = Order.query.filter_by(pharmacist_id=user.id)
    if sf and sf != 'all': q = q.filter_by(status=sf)
    counts = {s: Order.query.filter_by(pharmacist_id=user.id, status=s).count() for s in ['pending','confirmed','ready','delivered']}
    return render(PHARMACIST_ORDERS_HTML, title='Orders', orders=q.order_by(desc(Order.order_date)).all(), status_filter=sf, counts=counts)

@app.route('/update_order_status/<int:oid>', methods=['POST'])
@pharmacist_required
def update_order_status(oid):
    user = get_current_user()
    order = Order.query.get_or_404(oid)
    if order.pharmacist_id != user.id: flash('Denied.','danger'); return redirect(url_for('pharmacist_orders'))
    ns = request.form.get('status')
    valid = {'pending':['confirmed','cancelled'],'confirmed':['ready','cancelled'],'ready':['delivered']}
    if ns in valid.get(order.status, []):
        order.status = ns; db.session.commit(); sync_order_to_firestore(order)
        msgs = {'confirmed':'confirmed!','ready':'ready for pickup!','delivered':'delivered!','cancelled':'cancelled.'}
        db.session.add(Notification(user_id=order.user_id, title=f'Order #{order.id}', message=f'Your order has been {msgs.get(ns,ns)}', type='order', order_id=order.id))
        if ns == 'confirmed':
            for item in order.items:
                p = db.session.get(Product, item.product_id)
                if p: p.current_stock = max(0, p.current_stock-item.quantity); sync_product_to_firestore(p)
                db.session.add(SalesRecord(product_id=item.product_id, quantity=item.quantity, total_price=item.total_price, buyer_type='online'))
        db.session.commit(); flash(f'Order #{order.id} → {ns}!', 'success')
    return redirect(url_for('pharmacist_orders'))

@app.route('/create_bill_from_order/<int:oid>')
@pharmacist_required
def create_bill_from_order(oid):
    user = get_current_user()
    order = Order.query.get_or_404(oid)
    if order.pharmacist_id != user.id: return redirect(url_for('pharmacist_orders'))
    c = Customer.query.filter_by(phone=order.user.phone or 'N/A', added_by=user.id).first()
    if not c: c = Customer(name=order.user.full_name, phone=order.user.phone or 'N/A', added_by=user.id); db.session.add(c); db.session.flush()
    bill = Bill(customer_id=c.id, order_id=order.id, created_by=user.id, total_amount=order.total_amount)
    db.session.add(bill); db.session.flush()
    for item in order.items:
        db.session.add(BillItem(bill_id=bill.id, product_id=item.product_id, quantity=item.quantity, unit_price=item.unit_price, total_price=item.total_price))
    order.status = 'delivered'; db.session.commit(); sync_bill_to_firestore(bill); sync_order_to_firestore(order)
    flash(f'Bill #{bill.id} created!', 'success')
    return redirect(url_for('bill_detail', bill_id=bill.id))

@app.route('/notifications')
@pharmacist_required
def notifications():
    return render(NOTIFICATIONS_HTML, title='Notifications',
                  notifications=Notification.query.filter_by(user_id=get_current_user().id).order_by(desc(Notification.created_at)).limit(50).all())

@app.route('/mark_read/<int:nid>')
@login_required
def mark_read(nid):
    n = Notification.query.get_or_404(nid)
    if n.user_id == get_current_user().id: n.is_read = True; db.session.commit()
    return redirect(url_for('notifications'))

@app.route('/mark_all_read')
@login_required
def mark_all_read():
    Notification.query.filter_by(user_id=get_current_user().id, is_read=False).update({'is_read': True})
    db.session.commit(); return redirect(url_for('notifications'))

# ============================================================
# DOCTOR ROUTES
# ============================================================

@app.route('/check_medicines')
@doctor_required
def check_medicines():
    search = request.args.get('search', '')
    selected_category = request.args.get('category', type=int)
    categories = Category.query.all()
    query = Product.query.filter(Product.current_stock > 0)
    if search: query = query.filter(Product.name.ilike(f'%{search}%'))
    if selected_category: query = query.filter_by(category_id=selected_category)
    products = query.all()
    for p in products:
        ph = db.session.get(User, p.added_by)
        p.pharmacist_name = ph.organization or ph.full_name if ph else 'Unknown'
    return render(CHECK_MEDICINES_HTML, title='Medicines', products=products, categories=categories,
                  search=search, selected_category=selected_category)

# ============================================================
# API ROUTES
# ============================================================

@app.route('/api/update_location', methods=['POST'])
@login_required
def api_update_location():
    user = get_current_user()
    data = request.get_json()
    if data.get('lat') and data.get('lng'):
        user.latitude = float(data['lat']); user.longitude = float(data['lng'])
        db.session.commit(); sync_user_to_firestore(user)
        return jsonify({'success': True})
    return jsonify({'success': False}), 400

@app.route('/api/analyze_prescription', methods=['POST'])
@login_required
def api_analyze_prescription():
    data = request.get_json()
    if not data.get('image'): return jsonify({'error': 'No image'}), 400
    medicines = analyze_prescription(data['image'])
    for med in medicines:
        m = Product.query.filter(Product.name.ilike(f'%{med["name"].split()[0]}%'), Product.current_stock>0).first()
        med['matched_product_id'] = m.id if m else None
    return jsonify({'medicines': medicines})

@app.route('/api/place_prescription_order', methods=['POST'])
@login_required
def api_place_prescription_order():
    user = get_current_user()
    data = request.get_json()
    pharmacy_id = data.get('pharmacy_id')
    if not pharmacy_id: return jsonify({'success': False, 'error': 'Select pharmacy'}), 400
    order = Order(user_id=user.id, pharmacist_id=pharmacy_id, status='pending', is_monthly=data.get('is_monthly',False),
                  total_amount=0, prescription_data=json.dumps(data.get('medicines',[])), notes='AI prescription')
    db.session.add(order); db.session.flush()
    total, added = 0, 0
    for med in data.get('medicines', []):
        term = med.get('name','').split()[0] if med.get('name') else ''
        if not term: continue
        p = Product.query.filter(Product.added_by==pharmacy_id, Product.name.ilike(f'%{term}%'), Product.current_stock>0).first()
        if p:
            q = int(med.get('quantity',1))
            db.session.add(OrderItem(order_id=order.id, product_id=p.id, quantity=q, unit_price=p.unit_price, total_price=p.unit_price*q))
            total += p.unit_price*q; added += 1
    if not added: db.session.rollback(); return jsonify({'success':False,'error':'No matching medicines'}), 400
    order.total_amount = total; db.session.commit()
    db.session.add(Notification(user_id=pharmacy_id, title='Prescription Order!', message=f'{user.full_name} - {added} items ₹{total:.2f}', type='order', order_id=order.id))
    db.session.commit(); sync_order_to_firestore(order)
    return jsonify({'success': True, 'order_id': order.id})

@app.route('/api/customer_orders/<int:cid>')
@login_required
def api_customer_orders(cid):
    c = Customer.query.get(cid)
    if not c or not c.user_id: return jsonify({'orders': []})
    orders = Order.query.filter_by(user_id=c.user_id).filter(Order.status.in_(['pending','confirmed','ready'])).all()
    return jsonify({'orders': [{'id':o.id,'total_amount':f'{o.total_amount:.2f}','status':o.status} for o in orders]})

# ============================================================
# FIREBASE FIRESTORE SYNC
# ============================================================

def sync_user_to_firestore(user):
    try:
        if not firestore_db: return
        doc_id = user.firebase_uid if user.firebase_uid else str(user.id)
        firestore_db.collection('users').document(doc_id).set({
            'id': user.id, 'firebase_uid': user.firebase_uid, 'username': user.username,
            'email': user.email, 'role': user.role, 'full_name': user.full_name,
            'phone': user.phone, 'organization': user.organization, 'address': user.address,
            'latitude': user.latitude, 'longitude': user.longitude,
            'updated_at': datetime.now(timezone.utc).isoformat()
        }, merge=True)
        print(f"✅ User {user.username} synced to Firestore")
    except Exception as e: print(f"⚠️ Firestore sync error (user): {e}")

def sync_product_to_firestore(product):
    try:
        if not firestore_db: return
        ph = db.session.get(User, product.added_by)
        firestore_db.collection('products').document(str(product.id)).set({
            'id': product.id, 'name': product.name, 'category_name': product.category.name if product.category else '',
            'unit_price': product.unit_price, 'current_stock': product.current_stock, 'unit': product.unit,
            'manufacturer': product.manufacturer, 'stock_status': product.stock_status,
            'added_by': product.added_by, 'added_by_uid': ph.firebase_uid if ph else None,
            'pharmacy_name': ph.organization if ph else '',
            'updated_at': datetime.now(timezone.utc).isoformat()
        }, merge=True)
        print(f"✅ Product {product.name} synced")
    except Exception as e: print(f"⚠️ Firestore sync error (product): {e}")

def sync_order_to_firestore(order):
    try:
        if not firestore_db: return
        items = [{'product_name': i.product.name if i.product else '', 'quantity': i.quantity,
                  'unit_price': i.unit_price, 'total_price': i.total_price} for i in order.items]
        firestore_db.collection('orders').document(str(order.id)).set({
            'id': order.id, 'user_id': order.user_id, 'user_uid': order.user.firebase_uid if order.user else None,
            'user_name': order.user.full_name if order.user else '',
            'pharmacist_id': order.pharmacist_id, 'pharmacist_uid': order.pharmacist.firebase_uid if order.pharmacist else None,
            'pharmacy_name': order.pharmacist.organization if order.pharmacist else '',
            'status': order.status, 'is_monthly': order.is_monthly, 'total_amount': order.total_amount,
            'order_date': order.order_date.isoformat() if order.order_date else None, 'items': items,
            'updated_at': datetime.now(timezone.utc).isoformat()
        }, merge=True)
        print(f"✅ Order #{order.id} synced")
    except Exception as e: print(f"⚠️ Firestore sync error (order): {e}")

def sync_bill_to_firestore(bill):
    try:
        if not firestore_db: return
        items = [{'product_name': i.product.name if i.product else '', 'quantity': i.quantity,
                  'unit_price': i.unit_price, 'total_price': i.total_price} for i in bill.items]
        ph = db.session.get(User, bill.created_by)
        firestore_db.collection('bills').document(str(bill.id)).set({
            'id': bill.id, 'customer_name': bill.customer.name if bill.customer else '',
            'total_amount': bill.total_amount, 'created_by_uid': ph.firebase_uid if ph else None,
            'bill_date': bill.bill_date.isoformat() if bill.bill_date else None, 'items': items,
            'updated_at': datetime.now(timezone.utc).isoformat()
        }, merge=True)
        print(f"✅ Bill #{bill.id} synced")
    except Exception as e: print(f"⚠️ Firestore sync error (bill): {e}")

def sync_notification_to_firestore(notification):
    try:
        if not firestore_db: return
        u = db.session.get(User, notification.user_id)
        firestore_db.collection('notifications').document(str(notification.id)).set({
            'id': notification.id, 'user_uid': u.firebase_uid if u else None,
            'title': notification.title, 'message': notification.message,
            'type': notification.type, 'is_read': notification.is_read,
            'created_at': notification.created_at.isoformat() if notification.created_at else None
        }, merge=True)
    except Exception as e: print(f"⚠️ Firestore sync error (notif): {e}")

# ============================================================
# DATABASE SEED - KOLHAPUR REGION
# ============================================================

def openrouter_chat(prompt, system_prompt="You are a helpful pharmacy assistant."):
    """General AI chat using OpenRouter (free)"""
    try:
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "google/gemini-2.0-flash-exp:free",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ]
        }
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers, json=payload, timeout=30
        )
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content'].strip()
        return None
    except Exception as e:
        print(f"OpenRouter chat error: {e}")
        return None
def seed_database():
    categories_data = [
        ('Pain Relief', 'Analgesics', '💊'), ('Antibiotics', 'Anti-bacterial', '🦠'),
        ('Fever & Cold', 'Antipyretics', '🤒'), ('Digestive', 'Digestive health', '🫁'),
        ('Vitamins & Supplements', 'Nutrition', '💪'), ('Skin Care', 'Dermatology', '🧴'),
        ('Eye & Ear', 'Ophthalmic', '👁️'), ('Respiratory', 'Cough & asthma', '🫁'),
        ('Diabetes', 'Anti-diabetic', '🩸'), ('Heart & BP', 'Cardiovascular', '❤️'),
        ('Women Health', 'Gynecological', '👩'), ('Baby Care', 'Pediatric', '👶'),
        ('First Aid', 'Emergency', '🩹'), ('Ayurvedic', 'Traditional', '🌿'),
        ('Surgical', 'Surgical supplies', '🏥'),
    ]
    for name, desc, icon in categories_data:
        if not Category.query.filter_by(name=name).first():
            db.session.add(Category(name=name, description=desc, icon=icon))
    db.session.commit()
    print("✅ Categories seeded")

    if User.query.count() == 0:
        print("📦 Creating demo users (Kolhapur)...")

        pharmacist = User(firebase_uid='demo_pharmacist_001', username='demo_pharmacist',
            email='pharmacist@demo.com', role='pharmacist', full_name='Rajesh Patil',
            phone='+91 9876543210', organization='Patil Medical Store',
            address='Mahadwar Road, Kolhapur, Maharashtra 416012',
            latitude=16.7050, longitude=74.2433)
        pharmacist.set_password('demo123')
        db.session.add(pharmacist)

        doctor = User(firebase_uid='demo_doctor_001', username='demo_doctor',
            email='doctor@demo.com', role='doctor', full_name='Dr. Priya Deshmukh',
            phone='+91 9876543211', organization='CPR Hospital, Kolhapur',
            address='Tarabai Park, Kolhapur 416003',
            latitude=16.6950, longitude=74.2350)
        doctor.set_password('demo123')
        db.session.add(doctor)

        patient = User(firebase_uid='demo_patient_001', username='demo_patient',
            email='patient@demo.com', role='patient', full_name='Sunita Jadhav',
            phone='+91 9876543212', address='Rajarampuri, Kolhapur 416008',
            latitude=16.6900, longitude=74.2300)
        patient.set_password('demo123')
        db.session.add(patient)

        pharmacist2 = User(firebase_uid='demo_pharmacist_002', username='demo_pharmacist2',
            email='pharmacist2@demo.com', role='pharmacist', full_name='Amit Chavan',
            phone='+91 9876543213', organization='Chavan Health Point',
            address='Station Road, Ichalkaranji 416115',
            latitude=16.6912, longitude=74.4610)
        pharmacist2.set_password('demo123')
        db.session.add(pharmacist2)

        pharmacist3 = User(firebase_uid='demo_pharmacist_003', username='demo_pharmacist3',
            email='pharmacist3@demo.com', role='pharmacist', full_name='Sachin More',
            phone='+91 9876543214', organization='More Pharma & Surgicals',
            address='Shahupuri, Kolhapur 416001',
            latitude=16.7100, longitude=74.2350)
        pharmacist3.set_password('demo123')
        db.session.add(pharmacist3)

        pharmacist4 = User(firebase_uid='demo_pharmacist_004', username='demo_pharmacist4',
            email='pharmacist4@demo.com', role='pharmacist', full_name='Vikram Kulkarni',
            phone='+91 9876543215', organization='Kulkarni Medical',
            address='Vishrambag, Sangli 416415',
            latitude=16.8524, longitude=74.5815)
        pharmacist4.set_password('demo123')
        db.session.add(pharmacist4)

        db.session.commit()
        print("✅ Demo users created (Kolhapur region)")

        # Products for all pharmacists
        all_products = [
            ('Paracetamol 500mg','Fever & Cold',2.50,500,50,'tablets','Cipla',365,False),
            ('Paracetamol 650mg','Fever & Cold',3.00,300,30,'tablets','GSK',300,False),
            ('Amoxicillin 500mg','Antibiotics',8.50,200,20,'capsules','Cipla',180,True),
            ('Azithromycin 500mg','Antibiotics',25.00,100,15,'tablets','Zydus',270,True),
            ('Cetirizine 10mg','Fever & Cold',3.00,400,40,'tablets','Dr. Reddy',400,False),
            ('Ibuprofen 400mg','Pain Relief',4.00,350,35,'tablets','Cipla',350,False),
            ('Diclofenac 50mg','Pain Relief',3.50,250,25,'tablets','Novartis',300,False),
            ('Omeprazole 20mg','Digestive',5.00,300,30,'capsules','Sun Pharma',320,False),
            ('Pantoprazole 40mg','Digestive',7.00,200,20,'tablets','Alkem',280,False),
            ('Metformin 500mg','Diabetes',3.00,500,50,'tablets','USV',365,True),
            ('Amlodipine 5mg','Heart & BP',4.50,300,30,'tablets','Cipla',400,True),
            ('Vitamin C 500mg','Vitamins & Supplements',2.00,600,50,'tablets','Limcee',500,False),
            ('Multivitamin','Vitamins & Supplements',5.00,300,30,'tablets','Revital',400,False),
            ('Cough Syrup','Respiratory',45.00,100,15,'bottles','Dabur',180,False),
            ('Betadine Solution','First Aid',55.00,80,10,'bottles','Win Medicare',500,False),
            ('ORS Powder','Digestive',10.00,500,50,'units','Electral',365,False),
            ('Dolo 650','Fever & Cold',3.50,450,50,'tablets','Micro Labs',300,False),
            ('Combiflam','Pain Relief',5.00,350,35,'tablets','Sanofi',300,False),
            ('Iron + Folic Acid','Women Health',4.00,400,40,'tablets','Mankind',365,False),
            ('Calcium + D3','Vitamins & Supplements',6.00,300,30,'tablets','Shelcal',400,False),
            ('Ashwagandha','Ayurvedic',8.00,200,20,'tablets','Dabur',500,False),
            ('Chyawanprash','Ayurvedic',250.00,30,5,'units','Dabur',365,False),
            ('Disprin','Pain Relief',2.00,300,30,'tablets','Reckitt',400,False),
            ('Allegra 120mg','Fever & Cold',15.00,150,15,'tablets','Sanofi',365,False),
            ('Volini Spray','Pain Relief',180.00,40,5,'units','Ranbaxy',500,False),
            ('Band-Aid Pack','First Aid',35.00,100,15,'units','J&J',999,False),
            ('Savlon','First Aid',65.00,50,8,'bottles','ITC',600,False),
            ('B-Complex','Vitamins & Supplements',3.50,400,40,'tablets','Becosules',365,False),
            ('Gripe Water','Baby Care',55.00,60,10,'bottles','Woodwards',365,False),
            ('Tulsi Drops','Ayurvedic',120.00,40,5,'bottles','Organic India',400,False),
        ]

        for pharm in [pharmacist, pharmacist2, pharmacist3, pharmacist4]:
            # Each pharmacy gets a random subset with slightly different prices
            selected = random.sample(all_products, min(len(all_products), random.randint(15, 25)))
            for name, cat_name, price, stock, min_s, unit, mfr, exp_days, is_rx in selected:
                cat = Category.query.filter_by(name=cat_name).first()
                if cat:
                    # Slight price variation
                    variation = random.uniform(0.85, 1.15)
                    db.session.add(Product(
                        name=name, category_id=cat.id, unit_price=round(price*variation, 2),
                        current_stock=random.randint(int(stock*0.5), stock),
                        minimum_stock=min_s, unit=unit, manufacturer=mfr,
                        expiry_date=datetime.now(timezone.utc).date()+timedelta(days=exp_days),
                        is_prescription=is_rx, added_by=pharm.id
                    ))
            db.session.commit()
            print(f"✅ Products added for {pharm.organization}")

        # Sales records
        for pharm in [pharmacist, pharmacist2, pharmacist3, pharmacist4]:
            prods = Product.query.filter_by(added_by=pharm.id).all()
            if prods:
                for i in range(30):
                    day = datetime.now(timezone.utc) - timedelta(days=i)
                    for _ in range(random.randint(2, 8)):
                        p = random.choice(prods)
                        q = random.randint(1, 10)
                        db.session.add(SalesRecord(product_id=p.id, quantity=q, total_price=p.unit_price*q,
                                                   sale_date=day, buyer_type=random.choice(['walk-in','online','monthly'])))
                db.session.commit()
        print("✅ Sales records created")

        # Customers
        for name, phone, monthly in [('Ramesh Yadav','+91 9111111111',True),('Geeta Singh','+91 9222222222',True),
                                      ('Mohammad Ali','+91 9333333333',False),('Lakshmi Pandey','+91 9444444444',False)]:
            db.session.add(Customer(name=name, phone=phone, is_monthly=monthly, added_by=pharmacist.id))
        db.session.commit()
        print("✅ Customers created")

    print("✅ Database seeding complete!")

# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(404)
def not_found(e):
    flash('Not found.', 'warning'); return redirect(url_for('index'))

@app.errorhandler(500)
def server_error(e):
    flash('Server error.', 'danger'); return redirect(url_for('index'))

# ============================================================
# APP STARTUP
# ============================================================

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        print("✅ Database tables created")
        register_templates()
        setup_template_loader()
        print("✅ Templates registered")
        seed_database()
        try:
            if firestore_db:
                for u in User.query.all(): sync_user_to_firestore(u)
                print("✅ Initial Firestore sync complete")
        except Exception as e: print(f"⚠️ Sync skipped: {e}")

    register_templates()
    setup_template_loader()

    print("\n" + "="*60)
    print("🏥 GRAMIN SMARTCARE - Ready!")
    print("="*60)
    print("🌐 Open: http://localhost:5000")
    print("-"*60)
    print("📧 Demo: pharmacist@demo.com / demo123")
    print("📧 Demo: doctor@demo.com / demo123")
    print("📧 Demo: patient@demo.com / demo123")
    print("-"*60)
    print("📍 Location: Kolhapur, Maharashtra")
    print("🔥 Firebase: Auth + Firestore")
    print("🤖 Gemini AI: gemini-2.0-flash")
    print("="*60 + "\n")

    app.run(debug=True, host='0.0.0.0', port=5000)
