# ============================================================
# RURAL PHARMACY STOCK PREDICTOR - SINGLE FILE APPLICATION
# ============================================================
# Open: http://localhost:5000
# ============================================================

import os
import json
import random
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, render_template_string, redirect, url_for,
    flash, request, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import func, desc

# ============================================================
# APP CONFIGURATION
# ============================================================
app = Flask(__name__)
app.config['SECRET_KEY'] = 'rural-pharmacy-secret-key-2024'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///pharmacy.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'warning'

# ============================================================
# DATABASE MODELS
# ============================================================

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    full_name = db.Column(db.String(150))
    phone = db.Column(db.String(20))
    organization = db.Column(db.String(150))
    address = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
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
        days_left = (self.expiry_date - datetime.utcnow().date()).days
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
        return (self.expiry_date - datetime.utcnow().date()).days

    @property
    def predicted_stock_days(self):
        recent = SalesRecord.query.filter_by(product_id=self.id).filter(
            SalesRecord.sale_date >= datetime.utcnow() - timedelta(days=30)
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
    sale_date = db.Column(db.DateTime, default=datetime.utcnow)
    buyer_type = db.Column(db.String(50))


class StockPrediction(db.Model):
    __tablename__ = 'stock_predictions'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    predicted_demand = db.Column(db.Integer)
    predicted_date = db.Column(db.Date)
    confidence = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    product = db.relationship('Product', backref='predictions')


class Order(db.Model):
    __tablename__ = 'orders'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(30), default='pending')
    order_date = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='orders')
    product = db.relationship('Product', backref='orders')


class Customer(db.Model):
    __tablename__ = 'customers'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    is_monthly = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    added_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    bills = db.relationship('Bill', backref='customer', lazy=True)
    monthly_medicines = db.relationship('MonthlyMedicine', backref='customer', lazy=True)


class Bill(db.Model):
    __tablename__ = 'bills'
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=False)
    bill_date = db.Column(db.DateTime, default=datetime.utcnow)
    total_amount = db.Column(db.Float, default=0)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    items = db.relationship('BillItem', backref='bill', lazy=True)
    pharmacist = db.relationship('User', backref='bills_created')


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
    added_at = db.Column(db.DateTime, default=datetime.utcnow)
    product = db.relationship('Product', backref='monthly_subscriptions')


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ============================================================
# ACCESS CONTROL DECORATOR
# ============================================================

def pharmacist_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.role != 'pharmacist':
            flash('Access denied. This feature is only available for pharmacists.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

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

.category-pill { display: flex; align-items: center; gap: 0.6rem; background: var(--card-bg); padding: 0.8rem 1.5rem; border-radius: 50px; box-shadow: var(--shadow); white-space: nowrap; transition: var(--transition); cursor: pointer; font-weight: 500; }
.category-pill:hover { transform: translateY(-3px); box-shadow: var(--shadow-hover); }
.cat-icon { font-size: 1.3rem; }
.category-card-grid { background: var(--card-bg); border-radius: var(--radius); padding: 1.5rem; text-align: center; box-shadow: var(--shadow); transition: var(--transition); border: 2px solid transparent; }
.category-card-grid:hover { transform: translateY(-5px); border-color: var(--primary); box-shadow: var(--shadow-hover); }
.cat-grid-icon { font-size: 2rem; display: block; margin-bottom: 0.5rem; }

.cta-card { background: linear-gradient(135deg, var(--primary), var(--secondary)); border-radius: var(--radius); padding: 4rem 2rem; color: white; }
.cta-card h2 { font-weight: 700; font-size: 2rem; margin-bottom: 0.5rem; }
.cta-card p { opacity: 0.9; font-size: 1.1rem; margin-bottom: 2rem; }

.auth-container { min-height: calc(100vh - 76px); display: flex; align-items: center; padding: 2rem 0; background: linear-gradient(135deg, #f5f7fa, #c3cfe2); }
.auth-card { background: var(--card-bg); border-radius: var(--radius); padding: 2.5rem; box-shadow: var(--shadow-hover); border: 1px solid rgba(0,0,0,0.05); }
.auth-icon { width: 70px; height: 70px; border-radius: 50%; background: linear-gradient(135deg, var(--primary), var(--secondary)); display: flex; align-items: center; justify-content: center; margin: 0 auto 1rem; color: white; font-size: 1.5rem; }
.auth-header h2 { font-weight: 700; color: var(--dark); }
.auth-header p { color: #888; }
.role-selector { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
.role-option input[type="radio"] { display: none; }
.role-card { text-align: center; padding: 1.2rem; border: 2px solid #e0e0e0; border-radius: var(--radius-sm); cursor: pointer; transition: var(--transition); }
.role-card i { font-size: 1.8rem; color: #999; display: block; margin-bottom: 0.5rem; transition: var(--transition); }
.role-card span { display: block; font-weight: 600; color: #555; }
.role-card small { color: #999; font-size: 0.75rem; }
.role-option input:checked + .role-card { border-color: var(--primary); background: rgba(102,126,234,0.05); box-shadow: 0 0 0 3px rgba(102,126,234,0.15); }
.role-option input:checked + .role-card i { color: var(--primary); }
.demo-credentials { background: rgba(102,126,234,0.05); border-radius: 8px; padding: 0.8rem; }
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

.chart-card { background: var(--card-bg); border-radius: var(--radius); box-shadow: var(--shadow); overflow: hidden; border: 1px solid rgba(0,0,0,0.05); height: 100%; }
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
.prediction-badge { background: rgba(102,126,234,0.08); color: var(--primary); padding: 0.4rem 0.8rem; border-radius: 8px; font-size: 0.75rem; font-weight: 500; text-align: center; }
.product-actions { margin-top: auto; }

.prediction-row-critical { border-left: 4px solid #e74c3c; }
.prediction-row-warning { border-left: 4px solid #f39c12; }
.prediction-row-safe { border-left: 4px solid #27ae60; }
.urgency-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; margin-right: 6px; }
.urgency-critical { background: #e74c3c; }
.urgency-warning { background: #f39c12; }
.urgency-safe { background: #27ae60; }
.confidence-bar { display: flex; align-items: center; gap: 8px; }

.period-tabs { display: flex; gap: 0.5rem; flex-wrap: wrap; }
.period-tab { padding: 0.6rem 1.5rem; border-radius: 50px; text-decoration: none; font-weight: 600; font-size: 0.9rem; background: var(--card-bg); color: #666; box-shadow: var(--shadow); transition: var(--transition); }
.period-tab:hover { color: var(--primary); transform: translateY(-2px); }
.period-tab.active { background: linear-gradient(135deg, var(--primary), var(--secondary)); color: white; }
.rank-medal { width: 30px; height: 30px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; color: white; font-weight: 700; font-size: 0.8rem; }
.rank-1 { background: linear-gradient(135deg, #FFD700, #FFA500); }
.rank-2 { background: linear-gradient(135deg, #C0C0C0, #A0A0A0); }
.rank-3 { background: linear-gradient(135deg, #CD7F32, #A0522D); }
.rank-number { font-weight: 600; color: #666; }

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
.min-vh-75 { min-height: 75vh; }

.expiry-badge { font-size: 0.7rem; padding: 0.25rem 0.6rem; border-radius: 50px; font-weight: 600; }
.expiry-expired { background: #fdecea; color: #d32f2f; }
.expiry-expiring_soon { background: #fff3e0; color: #e65100; }
.expiry-expiring_3months { background: #fff8e1; color: #f9a825; }
.expiry-valid { background: #e8f5e9; color: #2e7d32; }
.expiry-unknown { background: #f5f5f5; color: #999; }

.bill-card { background: var(--card-bg); border-radius: var(--radius); padding: 1.5rem; box-shadow: var(--shadow); border: 1px solid rgba(0,0,0,0.05); margin-bottom: 1rem; }
.bill-item-row { display: flex; align-items: center; gap: 0.5rem; padding: 0.5rem 0; border-bottom: 1px solid rgba(0,0,0,0.05); }
.customer-card { background: var(--card-bg); border-radius: var(--radius); padding: 1.5rem; box-shadow: var(--shadow); transition: var(--transition); border: 1px solid rgba(0,0,0,0.05); }
.customer-card:hover { transform: translateY(-3px); box-shadow: var(--shadow-hover); }
.monthly-badge { background: linear-gradient(135deg, var(--primary), var(--secondary)); color: white; padding: 0.25rem 0.8rem; border-radius: 50px; font-size: 0.75rem; font-weight: 600; }

.readonly-banner { background: linear-gradient(135deg, rgba(102,126,234,0.1), rgba(118,75,162,0.1)); border: 1px solid rgba(102,126,234,0.2); border-radius: var(--radius-sm); padding: 0.8rem 1.2rem; margin-bottom: 1rem; color: var(--primary); font-weight: 500; }

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
    <title>{{ title|default('Gramin SmartCare - Stock Predictor') }}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>""" + CSS + """</style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark fixed-top">
        <div class="container-fluid">
            <a class="navbar-brand" href="/">
                <i class="fas fa-clinic-medical me-2"></i>
                <span class="brand-text">Gramin SmartCare</span>
                <small class="brand-sub">Stock Predictor</small>
            </a>
            <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav">
                <span class="navbar-toggler-icon"></span>
            </button>
            <div class="collapse navbar-collapse" id="navbarNav">
                <ul class="navbar-nav me-auto">
                    <li class="nav-item"><a class="nav-link" href="/"><i class="fas fa-home me-1"></i>Home</a></li>
                    {% if current_user.is_authenticated %}
                    <li class="nav-item"><a class="nav-link" href="/dashboard"><i class="fas fa-tachometer-alt me-1"></i>Dashboard</a></li>
                    <li class="nav-item"><a class="nav-link" href="/inventory"><i class="fas fa-boxes me-1"></i>Inventory</a></li>
                    <li class="nav-item"><a class="nav-link" href="/analytics"><i class="fas fa-chart-bar me-1"></i>Analytics</a></li>
                    <li class="nav-item"><a class="nav-link" href="/predictions"><i class="fas fa-brain me-1"></i>Predictions</a></li>
                    <li class="nav-item"><a class="nav-link" href="/top_sellers"><i class="fas fa-fire me-1"></i>Top Sellers</a></li>
                    {% if current_user.role == 'pharmacist' %}
                    <li class="nav-item"><a class="nav-link" href="/billing"><i class="fas fa-file-invoice-dollar me-1"></i>Billing</a></li>
                    <li class="nav-item"><a class="nav-link" href="/customers"><i class="fas fa-users me-1"></i>Customers</a></li>
                    {% endif %}
                    {% endif %}
                </ul>
                <ul class="navbar-nav">
                    {% if current_user.is_authenticated %}
                    <li class="nav-item dropdown">
                        <a class="nav-link dropdown-toggle user-menu" href="#" data-bs-toggle="dropdown">
                            <div class="user-avatar">{{ current_user.full_name[0] if current_user.full_name else 'U' }}</div>
                            {{ current_user.full_name or current_user.username }}
                            <span class="badge bg-{{ 'info' if current_user.role == 'pharmacist' else 'success' }} ms-1">{{ current_user.role|title }}</span>
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
                <div class="col-md-4"><h5><i class="fas fa-clinic-medical me-2"></i>RuralPharma</h5><p>AI-powered stock prediction for rural pharmacies and hospitals.</p></div>
                <div class="col-md-4"><h5>Quick Links</h5><ul class="list-unstyled"><li><a href="/">Home</a></li><li><a href="/login">Login</a></li><li><a href="/register">Register</a></li></ul></div>
                <div class="col-md-4"><h5>Contact</h5><p><i class="fas fa-phone me-2"></i>+91 1800-XXX-XXXX</p><p><i class="fas fa-envelope me-2"></i>support@ruralpharma.in</p></div>
            </div>
            <hr><p class="text-center mb-0">&copy; 2026 Pharmacy Stock Predictor. Built for Rural India.</p>
        </div>
    </footer>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        if(typeof Chart!=='undefined'){Chart.defaults.font.family="'Poppins',sans-serif";Chart.defaults.font.size=12;Chart.defaults.color='#666';Chart.defaults.plugins.tooltip.backgroundColor='rgba(26,26,46,0.9)';Chart.defaults.plugins.tooltip.padding=12;Chart.defaults.plugins.tooltip.cornerRadius=10;}
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
    <div class="hero-overlay"></div>
    <div class="container position-relative">
        <div class="row align-items-center min-vh-75">
            <div class="col-lg-7">
                <div class="hero-content">
                    <span class="hero-badge"><i class="fas fa-heartbeat me-2"></i>Smart Healthcare</span>
                    <h1 class="hero-title">Gramin<br><span class="gradient-text">SmartCare</span></h1>
                    <p class="hero-subtitle">AI-powered inventory management for rural pharmacies and hospitals. Never run out of essential medicines and supplies.</p>
                    <div class="hero-buttons">
                        {% if not current_user.is_authenticated %}
                        <a href="/register" class="btn btn-hero-primary btn-lg"><i class="fas fa-rocket me-2"></i>Get Started</a>
                        <a href="/login" class="btn-hero-outline btn-lg"><i class="fas fa-sign-in-alt me-2"></i>Login</a>
                        {% else %}
                        <a href="/dashboard" class="btn btn-hero-primary btn-lg"><i class="fas fa-tachometer-alt me-2"></i>Go to Dashboard</a>
                        {% endif %}
                    </div>
                </div>
            </div>
            <div class="col-lg-5">
                <div class="hero-stats-grid">
                    <div class="hero-stat-card"><div class="stat-icon bg-gradient-blue"><i class="fas fa-pills"></i></div><div class="stat-number">{{ total_products }}</div><div class="stat-label">Products Tracked</div></div>
                    <div class="hero-stat-card"><div class="stat-icon bg-gradient-green"><i class="fas fa-layer-group"></i></div><div class="stat-number">{{ total_categories }}</div><div class="stat-label">Categories</div></div>
                    <div class="hero-stat-card"><div class="stat-icon bg-gradient-orange"><i class="fas fa-exclamation-triangle"></i></div><div class="stat-number">{{ low_stock }}</div><div class="stat-label">Low Stock Alerts</div></div>
                    <div class="hero-stat-card"><div class="stat-icon bg-gradient-purple"><i class="fas fa-rupee-sign"></i></div><div class="stat-number">₹{{ "%.0f"|format(today_sales) }}</div><div class="stat-label">Today's Sales</div></div>
                </div>
            </div>
        </div>
    </div>
</section>
<section class="features-section py-5">
    <div class="container">
        <div class="section-header text-center mb-5">
            <span class="section-badge">Features</span>
            <h2 class="section-title">Everything Your Rural Pharmacy Needs</h2>
            <p class="section-desc">Comprehensive solution covering medicines, surgical supplies, diagnostic equipment, and more</p>
        </div>
        <div class="row g-4">
            {% for icon, title, desc in [('fa-brain','AI Stock Predictions','ML algorithms predict future demand based on sales, seasons, and health trends.'),('fa-chart-line','Deep Analytics','Interactive charts for sales trends, category analysis, buyer patterns.'),('fa-fire-alt','Top Sellers Tracking','Identify highest-selling items. Ensure critical stock is maintained.'),('fa-bell','Smart Alerts','Auto notifications for low stock, expiring medicines, reorder needs.'),('fa-file-invoice-dollar','Billing & Customers','Complete billing system with customer database and monthly medicine tracking.'),('fa-hospital','Complete Hospital Supply','Track medicines, surgical, PPE, diagnostics, IV fluids, first aid.')] %}
            <div class="col-lg-4 col-md-6">
                <div class="feature-card"><div class="feature-icon"><i class="fas {{ icon }}"></i></div><h4>{{ title }}</h4><p>{{ desc }}</p></div>
            </div>
            {% endfor %}
        </div>
    </div>
</section>
<section class="categories-section py-5">
    <div class="container">
        <div class="section-header text-center mb-5"><span class="section-badge">Categories</span><h2 class="section-title">Comprehensive Supply Management</h2></div>
        <div class="categories-scroll">
            <div class="row g-3 flex-nowrap overflow-auto pb-3">
                {% for icon, name in [('💊','Tablets'),('🧴','Syrups'),('💉','Injections'),('🔪','Surgical'),('🩹','Bandages'),('🔬','Diagnostics'),('🧤','PPE'),('🧪','IV Fluids'),('🏥','First Aid'),('👶','Baby Care'),('🌿','Ayurvedic'),('⚕️','Devices')] %}
                <div class="col-auto"><div class="category-pill"><span class="cat-icon">{{ icon }}</span><span>{{ name }}</span></div></div>
                {% endfor %}
            </div>
        </div>
    </div>
</section>
<section class="cta-section py-5">
    <div class="container"><div class="cta-card text-center"><h2>Ready to Transform Your Pharmacy?</h2><p>Join rural pharmacies using smart stock prediction</p>
        <div class="d-flex justify-content-center gap-3 flex-wrap">
            <a href="/register?role=pharmacist" class="btn btn-light btn-lg"><i class="fas fa-store me-2"></i>Register as Pharmacist</a>
            <a href="/register?role=doctor" class="btn btn-outline-light btn-lg"><i class="fas fa-user-md me-2"></i>Register as Doctor</a>
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
            <form method="POST">
                <div class="mb-3"><label class="form-label"><i class="fas fa-user me-2"></i>Username</label><input type="text" name="username" class="form-control form-control-lg" placeholder="Enter username" required></div>
                <div class="mb-3"><label class="form-label"><i class="fas fa-lock me-2"></i>Password</label>
                    <div class="input-group"><input type="password" name="password" id="lp" class="form-control form-control-lg" placeholder="Enter password" required>
                    <button class="btn btn-outline-secondary" type="button" onclick="let i=document.getElementById('lp');i.type=i.type==='password'?'text':'password';"><i class="fas fa-eye"></i></button></div></div>
                <div class="mb-4"><label class="form-label"><i class="fas fa-user-tag me-2"></i>Login As</label>
                    <div class="role-selector">
                        <label class="role-option"><input type="radio" name="role" value="pharmacist" required><div class="role-card"><i class="fas fa-pills"></i><span>Pharmacist</span><small>Full access & billing</small></div></label>
                        <label class="role-option"><input type="radio" name="role" value="doctor" required><div class="role-card"><i class="fas fa-user-md"></i><span>Doctor</span><small>View stock (read-only)</small></div></label>
                    </div></div>
                <button type="submit" class="btn btn-primary btn-lg w-100 mb-3"><i class="fas fa-sign-in-alt me-2"></i>Sign In</button>
            </form>
            <div class="auth-footer text-center">
                <p>Don't have an account? <a href="/register">Register here</a></p>
                <div class="demo-credentials mt-3"><small class="text-muted d-block mb-2"><strong>Demo Credentials:</strong></small>
                <small class="text-muted">Pharmacist: pharmacist1 / pharmacist123</small><br><small class="text-muted">Doctor: doctor1 / doctor123</small></div>
            </div>
        </div>
    </div></div></div>
</div>
{% endblock %}
"""

REGISTER_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="auth-container register-container">
    <div class="container"><div class="row justify-content-center"><div class="col-lg-7 col-md-9">
        <div class="auth-card">
            <div class="auth-header text-center"><div class="auth-icon"><i class="fas fa-user-plus"></i></div><h2>Create Account</h2><p>Join RuralPharma Stock Predictor</p></div>
            <form method="POST">
                <div class="mb-4"><label class="form-label"><i class="fas fa-user-tag me-2"></i>Register As</label>
                    <div class="role-selector">
                        <label class="role-option"><input type="radio" name="role" value="pharmacist" {{ 'checked' if request.args.get('role')=='pharmacist' }} required><div class="role-card"><i class="fas fa-pills"></i><span>Pharmacist</span><small>Full access & billing</small></div></label>
                        <label class="role-option"><input type="radio" name="role" value="doctor" {{ 'checked' if request.args.get('role')=='doctor' }} required><div class="role-card"><i class="fas fa-user-md"></i><span>Doctor</span><small>View stock (read-only)</small></div></label>
                    </div></div>
                <div class="row g-3">
                    <div class="col-md-6"><label class="form-label">Full Name</label><input type="text" name="full_name" class="form-control" required></div>
                    <div class="col-md-6"><label class="form-label">Username</label><input type="text" name="username" class="form-control" required></div>
                    <div class="col-md-6"><label class="form-label">Email</label><input type="email" name="email" class="form-control" required></div>
                    <div class="col-md-6"><label class="form-label">Phone</label><input type="tel" name="phone" class="form-control"></div>
                    <div class="col-12"><label class="form-label">Organization / Hospital</label><input type="text" name="organization" class="form-control"></div>
                    <div class="col-12"><label class="form-label">Address</label><textarea name="address" class="form-control" rows="2"></textarea></div>
                    <div class="col-md-6"><label class="form-label">Password</label><input type="password" name="password" class="form-control" required minlength="6"></div>
                    <div class="col-md-6"><label class="form-label">Confirm Password</label><input type="password" name="confirm_password" class="form-control" required minlength="6"></div>
                </div>
                <button type="submit" class="btn btn-primary btn-lg w-100 mt-4 mb-3"><i class="fas fa-user-plus me-2"></i>Create Account</button>
            </form>
            <div class="auth-footer text-center"><p>Already have an account? <a href="/login">Sign in here</a></p></div>
        </div>
    </div></div></div>
</div>
{% endblock %}
"""

DASHBOARD_PHARMACIST_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container-fluid px-4 py-4">
    <div class="page-header mb-4"><div><h2><i class="fas fa-tachometer-alt me-2"></i>Pharmacist Dashboard</h2><p class="text-muted">Welcome, {{ current_user.full_name }}!</p></div>
        <div class="d-flex gap-2">
            <a href="/add_product" class="btn btn-primary"><i class="fas fa-plus me-2"></i>Add Product</a>
            <a href="/billing" class="btn btn-success"><i class="fas fa-file-invoice-dollar me-2"></i>New Bill</a>
        </div></div>
    <div class="row g-4 mb-4">
        <div class="col-xl-3 col-md-6"><div class="stat-card-modern bg-gradient-blue"><div class="stat-card-body"><div class="stat-card-icon"><i class="fas fa-boxes"></i></div><div class="stat-card-info"><h3>{{ stats.total_products }}</h3><p>Total Products</p></div></div><div class="stat-card-footer"><a href="/inventory">View Inventory <i class="fas fa-arrow-right"></i></a></div></div></div>
        <div class="col-xl-3 col-md-6"><div class="stat-card-modern bg-gradient-orange"><div class="stat-card-body"><div class="stat-card-icon"><i class="fas fa-exclamation-triangle"></i></div><div class="stat-card-info"><h3>{{ stats.low_stock_count }}</h3><p>Low Stock Items</p></div></div><div class="stat-card-footer"><a href="/inventory?stock_filter=low">View Low Stock <i class="fas fa-arrow-right"></i></a></div></div></div>
        <div class="col-xl-3 col-md-6"><div class="stat-card-modern bg-gradient-red"><div class="stat-card-body"><div class="stat-card-icon"><i class="fas fa-calendar-times"></i></div><div class="stat-card-info"><h3>{{ stats.expiring_soon }}</h3><p>Expiring Soon</p></div></div><div class="stat-card-footer"><a href="/inventory?expiry_filter=expiring_soon">View Expiring <i class="fas fa-arrow-right"></i></a></div></div></div>
        <div class="col-xl-3 col-md-6"><div class="stat-card-modern bg-gradient-green"><div class="stat-card-body"><div class="stat-card-icon"><i class="fas fa-rupee-sign"></i></div><div class="stat-card-info"><h3>₹{{ "%.0f"|format(stats.total_revenue_today) }}</h3><p>Today's Revenue</p></div></div><div class="stat-card-footer"><a href="/analytics">View Analytics <i class="fas fa-arrow-right"></i></a></div></div></div>
    </div>
    <div class="row g-4 mb-4">
        <div class="col-xl-8"><div class="chart-card"><div class="chart-header"><h5><i class="fas fa-chart-line me-2"></i>Sales Trend (7 Days)</h5></div><div class="chart-body"><canvas id="salesChart" height="300"></canvas></div></div></div>
        <div class="col-xl-4"><div class="chart-card"><div class="chart-header"><h5><i class="fas fa-chart-pie me-2"></i>Stock by Category</h5></div><div class="chart-body"><canvas id="categoryChart" height="300"></canvas></div></div></div>
    </div>
    <div class="row g-4 mb-4">
        <div class="col-xl-6"><div class="chart-card"><div class="chart-header"><h5><i class="fas fa-chart-bar me-2"></i>Monthly Revenue</h5></div><div class="chart-body"><canvas id="monthlyChart" height="250"></canvas></div></div></div>
        <div class="col-xl-6"><div class="chart-card"><div class="chart-header d-flex justify-content-between align-items-center"><h5><i class="fas fa-exclamation-circle me-2 text-warning"></i>Low Stock Alerts</h5><span class="badge bg-warning">{{ stats.low_stock_count }} items</span></div>
            <div class="chart-body p-0"><div class="table-responsive" style="max-height:350px;overflow-y:auto;"><table class="table table-hover mb-0"><thead class="sticky-top bg-white"><tr><th>Product</th><th>Stock</th><th>Min</th><th>Expiry</th><th>Status</th></tr></thead><tbody>
                {% for item in stats.low_stock_items[:15] %}<tr><td><strong>{{ item.name }}</strong></td><td>{{ item.current_stock }} {{ item.unit }}</td><td>{{ item.minimum_stock }}</td><td>{% if item.expiry_date %}<span class="expiry-badge expiry-{{ item.expiry_status }}">{{ item.expiry_date.strftime('%b %Y') }}</span>{% else %}N/A{% endif %}</td><td>{% if item.current_stock <= 0 %}<span class="badge bg-danger">Out</span>{% else %}<span class="badge bg-warning">Critical</span>{% endif %}</td></tr>{% endfor %}
            </tbody></table></div></div></div></div>
    </div>
</div>
{% endblock %}
{% block scripts %}
<script>
document.addEventListener('DOMContentLoaded', function() {
    new Chart(document.getElementById('salesChart'), {type:'line',data:{labels:{{ stats.labels_7days|safe }},datasets:[{label:'Revenue (₹)',data:{{ stats.sales_7days|safe }},borderColor:'#667eea',backgroundColor:'rgba(102,126,234,0.1)',fill:true,tension:0.4,borderWidth:3,pointRadius:5,pointBackgroundColor:'#667eea'}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{y:{beginAtZero:true,ticks:{callback:v=>'₹'+v.toLocaleString()}}}}});
    new Chart(document.getElementById('categoryChart'), {type:'doughnut',data:{labels:{{ stats.cat_labels|safe }},datasets:[{data:{{ stats.cat_stock|safe }},backgroundColor:['#667eea','#764ba2','#f093fb','#4facfe','#43e97b','#fa709a','#fee140','#a8edea','#ff9a9e','#fad0c4','#fbc2eb','#a18cd1']}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'bottom',labels:{boxWidth:12,font:{size:10}}}}}});
    new Chart(document.getElementById('monthlyChart'), {type:'bar',data:{labels:{{ stats.monthly_labels|safe }},datasets:[{label:'Revenue',data:{{ stats.monthly_revenue|safe }},backgroundColor:'rgba(102,126,234,0.8)',borderRadius:8}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{y:{beginAtZero:true,ticks:{callback:v=>'₹'+(v/1000).toFixed(0)+'K'}}}}});
});
</script>
{% endblock %}
"""

DASHBOARD_DOCTOR_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container-fluid px-4 py-4">
    <div class="page-header mb-4"><div><h2><i class="fas fa-user-md me-2"></i>Doctor Dashboard</h2><p class="text-muted">Welcome, Dr. {{ current_user.full_name }}!</p></div></div>

    <div class="readonly-banner">
        <i class="fas fa-eye me-2"></i><strong>Read-Only Access</strong> — You can view stock levels, analytics, and predictions to help with your prescriptions. Only pharmacists can modify inventory and create bills.
    </div>

    <div class="row g-4 mb-4">
        <div class="col-md-3"><div class="stat-card-modern bg-gradient-blue"><div class="stat-card-body"><div class="stat-card-icon"><i class="fas fa-pills"></i></div><div class="stat-card-info"><h3>{{ stats.total_products }}</h3><p>Available Products</p></div></div></div></div>
        <div class="col-md-3"><div class="stat-card-modern bg-gradient-green"><div class="stat-card-body"><div class="stat-card-icon"><i class="fas fa-check-circle"></i></div><div class="stat-card-info"><h3>{{ stats.in_stock_count }}</h3><p>In Stock</p></div></div></div></div>
        <div class="col-md-3"><div class="stat-card-modern bg-gradient-orange"><div class="stat-card-body"><div class="stat-card-icon"><i class="fas fa-exclamation-triangle"></i></div><div class="stat-card-info"><h3>{{ stats.low_stock|length }}</h3><p>Low Stock Warnings</p></div></div></div></div>
        <div class="col-md-3"><div class="stat-card-modern bg-gradient-red"><div class="stat-card-body"><div class="stat-card-icon"><i class="fas fa-times-circle"></i></div><div class="stat-card-info"><h3>{{ stats.out_of_stock_count }}</h3><p>Out of Stock</p></div></div></div></div>
    </div>

    <h4 class="mb-3"><i class="fas fa-th-large me-2"></i>Browse by Category</h4>
    <div class="row g-3 mb-4">
        {% for cat in stats.categories %}
        <div class="col-lg-3 col-md-4 col-6"><a href="/inventory?category={{ cat.id }}" class="text-decoration-none"><div class="category-card-grid"><span class="cat-grid-icon">{{ cat.icon }}</span><h6>{{ cat.name }}</h6><small class="text-muted">{{ cat.products|length }} items</small></div></a></div>
        {% endfor %}
    </div>

    <div class="row g-4">
        <div class="col-md-6"><div class="chart-card"><div class="chart-header"><h5><i class="fas fa-bolt me-2 text-warning"></i>Quick Actions</h5></div><div class="chart-body"><div class="d-grid gap-2">
            <a href="/inventory" class="btn btn-outline-primary btn-lg"><i class="fas fa-search me-2"></i>Check Stock Availability</a>
            <a href="/top_sellers" class="btn btn-outline-success btn-lg"><i class="fas fa-fire me-2"></i>View Top Sellers</a>
            <a href="/predictions" class="btn btn-outline-info btn-lg"><i class="fas fa-brain me-2"></i>Stock Predictions</a>
            <a href="/analytics" class="btn btn-outline-warning btn-lg"><i class="fas fa-chart-bar me-2"></i>View Analytics</a>
        </div></div></div></div>
        <div class="col-md-6"><div class="chart-card"><div class="chart-header"><h5><i class="fas fa-exclamation-triangle me-2 text-danger"></i>Low Stock Medicines</h5></div>
            <div class="chart-body p-0"><div class="table-responsive" style="max-height:350px;overflow-y:auto;"><table class="table table-hover mb-0"><thead class="sticky-top bg-white"><tr><th>Medicine</th><th>Stock</th><th>Status</th></tr></thead><tbody>
            {% for p in stats.low_stock[:15] %}
            <tr><td><strong>{{ p.name }}</strong><br><small class="text-muted">{{ p.category.icon }} {{ p.category.name }}</small></td>
                <td>{{ p.current_stock }} {{ p.unit }}</td>
                <td>{% if p.current_stock <= 0 %}<span class="badge bg-danger">Out</span>{% else %}<span class="badge bg-warning">Low</span>{% endif %}</td></tr>
            {% else %}<tr><td colspan="3" class="text-center text-muted py-4">All medicines adequately stocked</td></tr>{% endfor %}
            </tbody></table></div></div>
        </div></div>
    </div>
</div>
{% endblock %}
"""

INVENTORY_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container-fluid px-4 py-4">
    <div class="page-header mb-4"><div><h2><i class="fas fa-boxes me-2"></i>Inventory</h2><p class="text-muted">{{ products|length }} products</p></div>
        {% if current_user.role == 'pharmacist' %}<a href="/add_product" class="btn btn-primary"><i class="fas fa-plus me-2"></i>Add Product</a>{% endif %}</div>

    {% if current_user.role == 'doctor' %}
    <div class="readonly-banner">
        <i class="fas fa-eye me-2"></i><strong>Read-Only View</strong> — Check medicine availability and stock levels before writing prescriptions.
    </div>
    {% endif %}

    <div class="filter-bar mb-4"><form method="GET" class="row g-3 align-items-end">
        <div class="col-md-2"><label class="form-label">Search</label><div class="input-group"><span class="input-group-text"><i class="fas fa-search"></i></span><input type="text" name="search" class="form-control" placeholder="Search..." value="{{ search }}"></div></div>
        <div class="col-md-2"><label class="form-label">Category</label><select name="category" class="form-select"><option value="">All</option>{% for c in categories %}<option value="{{ c.id }}" {{ 'selected' if selected_category == c.id }}>{{ c.icon }} {{ c.name }}</option>{% endfor %}</select></div>
        <div class="col-md-2"><label class="form-label">Stock</label><select name="stock_filter" class="form-select"><option value="">All</option><option value="low" {{ 'selected' if stock_filter=='low' }}>🟡 Low</option><option value="out" {{ 'selected' if stock_filter=='out' }}>🔴 Out</option><option value="adequate" {{ 'selected' if stock_filter=='adequate' }}>🟢 Adequate</option></select></div>
        <div class="col-md-3"><label class="form-label">Expiry</label><select name="expiry_filter" class="form-select"><option value="">All</option><option value="expired" {{ 'selected' if expiry_filter=='expired' }}>🔴 Expired</option><option value="expiring_soon" {{ 'selected' if expiry_filter=='expiring_soon' }}>🟠 Expiring (30d)</option><option value="expiring_3months" {{ 'selected' if expiry_filter=='expiring_3months' }}>🟡 Expiring (90d)</option><option value="valid" {{ 'selected' if expiry_filter=='valid' }}>🟢 Valid</option></select></div>
        <div class="col-md-3"><button type="submit" class="btn btn-primary w-100"><i class="fas fa-filter me-2"></i>Filter</button></div>
    </form></div>
    <div class="row g-3">
        {% for p in products %}
        <div class="col-xl-3 col-lg-4 col-md-6">
            <div class="product-card">
                <div class="product-header"><span class="product-category">{{ p.category.icon }} {{ p.category.name }}</span>
                    <span class="stock-badge stock-{{ p.stock_status }}">{% if p.stock_status=='out_of_stock' %}Out{% elif p.stock_status=='critical' %}Critical{% elif p.stock_status=='low' %}Low{% else %}In Stock{% endif %}</span></div>
                <h5 class="product-name">{{ p.name }}</h5>
                <div class="product-details">
                    <div class="detail-row"><span><i class="fas fa-rupee-sign me-1"></i>Price:</span><strong>₹{{ "%.2f"|format(p.unit_price) }}/{{ p.unit }}</strong></div>
                    <div class="detail-row"><span><i class="fas fa-cubes me-1"></i>Stock:</span><strong class="text-{{ 'danger' if p.stock_status in ['out_of_stock','critical'] else 'warning' if p.stock_status=='low' else 'success' }}">{{ p.current_stock }} {{ p.unit }}</strong></div>
                    <div class="detail-row"><span><i class="fas fa-industry me-1"></i>Mfr:</span><span>{{ p.manufacturer or 'N/A' }}</span></div>
                    <div class="detail-row"><span><i class="fas fa-calendar me-1"></i>Expiry:</span>
                        {% if p.expiry_date %}
                        <span class="expiry-badge expiry-{{ p.expiry_status }}">
                            {{ p.expiry_date.strftime('%d %b %Y') }}
                            {% if p.expiry_status == 'expired' %} (Expired)
                            {% elif p.expiry_status == 'expiring_soon' %} ({{ p.days_until_expiry }}d left)
                            {% elif p.expiry_status == 'expiring_3months' %} ({{ p.days_until_expiry }}d left)
                            {% endif %}
                        </span>
                        {% else %}N/A{% endif %}
                    </div>
                    {% if p.is_prescription %}<span class="badge bg-info mt-2"><i class="fas fa-prescription me-1"></i>Rx Required</span>{% endif %}
                </div>
                <div class="stock-bar mt-3">{% set pct = (p.current_stock/p.maximum_stock*100) if p.maximum_stock > 0 else 0 %}<div class="progress" style="height:8px;"><div class="progress-bar bg-{{ 'danger' if pct<15 else 'warning' if pct<40 else 'success' }}" style="width:{{ pct }}%"></div></div><small class="text-muted">{{ "%.0f"|format(pct) }}% capacity</small></div>
                <div class="prediction-badge mt-2"><i class="fas fa-brain me-1"></i>~{{ p.predicted_stock_days }} days remaining</div>
                {% if current_user.role == 'pharmacist' %}
                <div class="product-actions mt-3">
                    <form method="POST" action="/update_stock/{{ p.id }}" class="d-flex gap-2"><input type="number" name="new_stock" class="form-control form-control-sm" value="{{ p.current_stock }}" min="0"><button type="submit" class="btn btn-sm btn-outline-primary"><i class="fas fa-sync"></i></button></form>
                </div>
                {% endif %}
            </div>
        </div>
        {% endfor %}
    </div>
    {% if not products %}<div class="empty-state text-center py-5"><i class="fas fa-search fa-3x text-muted mb-3"></i><h4>No products found</h4><p class="text-muted">Adjust filters</p></div>{% endif %}
</div>
{% endblock %}
"""

ANALYTICS_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container-fluid px-4 py-4">
    <div class="page-header mb-4"><div><h2><i class="fas fa-chart-bar me-2"></i>Deep Analytics</h2><p class="text-muted">Comprehensive insights</p></div></div>

    {% if current_user.role == 'doctor' %}
    <div class="readonly-banner">
        <i class="fas fa-eye me-2"></i><strong>Read-Only View</strong> — View sales trends and stock analytics to understand medicine demand patterns.
    </div>
    {% endif %}

    <div class="row g-4 mb-4"><div class="col-12"><div class="chart-card"><div class="chart-header"><h5><i class="fas fa-chart-area me-2"></i>30-Day Sales Trend</h5></div><div class="chart-body" style="height:350px;"><canvas id="salesTrendChart"></canvas></div></div></div></div>
    <div class="row g-4 mb-4">
        <div class="col-xl-6"><div class="chart-card"><div class="chart-header"><h5><i class="fas fa-chart-pie me-2"></i>Sales by Category (30d)</h5></div><div class="chart-body" style="height:350px;"><canvas id="categorySalesChart"></canvas></div></div></div>
        <div class="col-xl-6"><div class="chart-card"><div class="chart-header"><h5><i class="fas fa-users me-2"></i>Sales by Buyer Type</h5></div><div class="chart-body" style="height:350px;"><canvas id="buyerChart"></canvas></div></div></div>
    </div>
    <div class="row g-4 mb-4">
        <div class="col-xl-4"><div class="chart-card"><div class="chart-header"><h5><i class="fas fa-heartbeat me-2"></i>Stock Health</h5></div><div class="chart-body" style="height:300px;"><canvas id="stockHealthChart"></canvas></div></div></div>
        <div class="col-xl-8"><div class="chart-card"><div class="chart-header"><h5><i class="fas fa-chart-bar me-2"></i>Category Comparison</h5></div><div class="chart-body" style="height:300px;"><canvas id="categoryCompareChart"></canvas></div></div></div>
    </div>
</div>
{% endblock %}
{% block scripts %}
<script>
document.addEventListener('DOMContentLoaded', function() {
    const colors=['#667eea','#764ba2','#f093fb','#4facfe','#43e97b','#fa709a','#fee140','#a8edea','#ff9a9e','#fad0c4','#fbc2eb','#a18cd1'];
    new Chart(document.getElementById('salesTrendChart'),{type:'line',data:{labels:{{ sales_labels|safe }},datasets:[{label:'Revenue (₹)',data:{{ sales_trend|safe }},borderColor:'#667eea',backgroundColor:'rgba(102,126,234,0.15)',fill:true,tension:0.4,borderWidth:3,pointRadius:2,pointHoverRadius:6}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{y:{beginAtZero:true,ticks:{callback:v=>'₹'+(v/1000).toFixed(1)+'K'}},x:{ticks:{maxTicksLimit:15}}},interaction:{intersect:false,mode:'index'}}});
    new Chart(document.getElementById('categorySalesChart'),{type:'pie',data:{labels:{{ cat_sales_labels|safe }},datasets:[{data:{{ cat_sales_values|safe }},backgroundColor:colors}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'right',labels:{font:{size:11},boxWidth:12}}}}});
    new Chart(document.getElementById('buyerChart'),{type:'doughnut',data:{labels:{{ buyer_labels|safe }},datasets:[{data:{{ buyer_values|safe }},backgroundColor:['#667eea','#43e97b','#fa709a','#fee140'],borderWidth:3,borderColor:'#fff'}]},options:{responsive:true,maintainAspectRatio:false,cutout:'60%',plugins:{legend:{position:'bottom'}}}});
    new Chart(document.getElementById('stockHealthChart'),{type:'doughnut',data:{labels:['Adequate','Low','Critical','Out of Stock'],datasets:[{data:{{ stock_health|safe }},backgroundColor:['#43e97b','#fee140','#fa709a','#e74c3c'],borderWidth:3,borderColor:'#fff'}]},options:{responsive:true,maintainAspectRatio:false,cutout:'55%',plugins:{legend:{position:'bottom'}}}});
    new Chart(document.getElementById('categoryCompareChart'),{type:'bar',data:{labels:{{ cat_sales_labels|safe }},datasets:[{label:'Sales (₹)',data:{{ cat_sales_values|safe }},backgroundColor:colors,borderRadius:6}]},options:{responsive:true,maintainAspectRatio:false,indexAxis:'y',plugins:{legend:{display:false}},scales:{x:{beginAtZero:true,ticks:{callback:v=>'₹'+(v/1000).toFixed(0)+'K'}}}}});
});
</script>
{% endblock %}
"""

PREDICTIONS_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container-fluid px-4 py-4">
    <div class="page-header mb-4"><div><h2><i class="fas fa-brain me-2"></i>AI Stock Predictions</h2><p class="text-muted">Demand forecasting & reorder recommendations</p></div></div>

    {% if current_user.role == 'doctor' %}
    <div class="readonly-banner">
        <i class="fas fa-eye me-2"></i><strong>Read-Only View</strong> — Check predicted stock levels to plan prescriptions accordingly.
    </div>
    {% endif %}

    <div class="row g-4 mb-4">
        <div class="col-md-4"><div class="stat-card-modern bg-gradient-red"><div class="stat-card-body"><div class="stat-card-icon"><i class="fas fa-exclamation-circle"></i></div><div class="stat-card-info"><h3>{{ predictions|selectattr('urgency','equalto','critical')|list|length }}</h3><p>Critical (≤7 days)</p></div></div></div></div>
        <div class="col-md-4"><div class="stat-card-modern bg-gradient-orange"><div class="stat-card-body"><div class="stat-card-icon"><i class="fas fa-exclamation-triangle"></i></div><div class="stat-card-info"><h3>{{ predictions|selectattr('urgency','equalto','warning')|list|length }}</h3><p>Warning (≤14 days)</p></div></div></div></div>
        <div class="col-md-4"><div class="stat-card-modern bg-gradient-green"><div class="stat-card-body"><div class="stat-card-icon"><i class="fas fa-check-circle"></i></div><div class="stat-card-info"><h3>{{ predictions|selectattr('urgency','equalto','safe')|list|length }}</h3><p>Safe (>14 days)</p></div></div></div></div>
    </div>
    <div class="chart-card"><div class="chart-header"><h5><i class="fas fa-table me-2"></i>Detailed Predictions</h5></div><div class="chart-body p-0"><div class="table-responsive"><table class="table table-hover prediction-table mb-0"><thead><tr><th>Product</th><th>Stock</th><th>Avg Daily</th><th>Days Left</th><th>Reorder Qty</th><th>Confidence</th><th>Urgency</th><th>Forecast</th></tr></thead><tbody>
        {% for pred in predictions[:30] %}
        <tr class="prediction-row-{{ pred.urgency }}">
            <td><strong>{{ pred.product.name }}</strong><br><small class="text-muted">{{ pred.product.category.icon }} {{ pred.product.category.name }}</small></td>
            <td><span class="fw-bold text-{{ 'danger' if pred.product.stock_status in ['out_of_stock','critical'] else 'warning' if pred.product.stock_status=='low' else 'success' }}">{{ pred.product.current_stock }} {{ pred.product.unit }}</span></td>
            <td>{{ pred.avg_daily_sales }}</td>
            <td><span class="badge bg-{{ 'danger' if pred.days_until_empty<=7 else 'warning' if pred.days_until_empty<=14 else 'success' }} fs-6">{{ pred.days_until_empty if pred.days_until_empty<999 else '∞' }}d</span></td>
            <td>{% if pred.reorder_qty>0 %}<span class="text-danger fw-bold">{{ pred.reorder_qty }}</span>{% else %}<span class="text-success">OK</span>{% endif %}</td>
            <td><div class="confidence-bar"><div class="progress" style="height:6px;width:80px;"><div class="progress-bar bg-info" style="width:{{ pred.confidence }}%"></div></div><small>{{ pred.confidence }}%</small></div></td>
            <td><span class="urgency-dot urgency-{{ pred.urgency }}"></span>{{ pred.urgency|title }}</td>
            <td style="width:200px;"><canvas class="forecast-mini" data-values="{{ pred.pred_values }}" data-labels="{{ pred.pred_dates }}" height="50" width="180"></canvas></td>
        </tr>
        {% endfor %}
    </tbody></table></div></div></div>
</div>
{% endblock %}
{% block scripts %}
<script>
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.forecast-mini').forEach(c=>{
        const v=JSON.parse(c.dataset.values.replace(/'/g,'"')),l=JSON.parse(c.dataset.labels.replace(/'/g,'"'));
        new Chart(c,{type:'line',data:{labels:l,datasets:[{data:v,borderColor:'#667eea',backgroundColor:'rgba(102,126,234,0.1)',fill:true,tension:0.4,borderWidth:2,pointRadius:0}]},options:{responsive:false,plugins:{legend:{display:false}},scales:{x:{display:false},y:{display:false}},animation:false}});
    });
});
</script>
{% endblock %}
"""

TOP_SELLERS_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container-fluid px-4 py-4">
    <div class="page-header mb-4"><div><h2><i class="fas fa-fire me-2 text-danger"></i>{{ title }}</h2><p class="text-muted">Highest selling items & essential daily stock</p></div></div>

    {% if current_user.role == 'doctor' %}
    <div class="readonly-banner">
        <i class="fas fa-eye me-2"></i><strong>Read-Only View</strong> — View top selling and high-demand medicines for prescription planning.
    </div>
    {% endif %}

    <div class="period-tabs mb-4">
        <a href="/top_sellers?period=daily" class="period-tab {{ 'active' if period=='daily' }}"><i class="fas fa-calendar-day me-1"></i>Daily</a>
        <a href="/top_sellers?period=weekly" class="period-tab {{ 'active' if period=='weekly' }}"><i class="fas fa-calendar-week me-1"></i>Weekly</a>
        <a href="/top_sellers?period=monthly" class="period-tab {{ 'active' if period=='monthly' }}"><i class="fas fa-calendar-alt me-1"></i>Monthly</a>
    </div>
    <div class="row g-4 mb-4">
        <div class="col-xl-7"><div class="chart-card"><div class="chart-header"><h5><i class="fas fa-trophy me-2 text-warning"></i>Top Selling Products</h5></div><div class="chart-body" style="height:400px;"><canvas id="topSellersChart"></canvas></div></div></div>
        <div class="col-xl-5"><div class="chart-card"><div class="chart-header"><h5><i class="fas fa-rupee-sign me-2 text-success"></i>Revenue by Product</h5></div><div class="chart-body" style="height:400px;"><canvas id="revenueChart"></canvas></div></div></div>
    </div>
    <div class="chart-card mb-4"><div class="chart-header"><h5><i class="fas fa-list-ol me-2"></i>Top Sellers Ranking</h5></div><div class="chart-body p-0"><div class="table-responsive"><table class="table table-hover mb-0"><thead><tr><th>Rank</th><th>Product</th><th>Qty Sold</th><th>Revenue</th><th>Stock</th><th>Status</th></tr></thead><tbody>
        {% for item in top_sellers %}
        <tr><td>{% if loop.index<=3 %}<span class="rank-medal rank-{{ loop.index }}">{{ loop.index }}</span>{% else %}<span class="rank-number">{{ loop.index }}</span>{% endif %}</td>
            <td><strong>{{ item[0] }}</strong></td><td class="fw-bold text-primary">{{ item[2] }}</td><td>₹{{ "%.2f"|format(item[3]) }}</td><td>{{ item[4] }}</td>
            <td>{% if item[4]<=0 %}<span class="badge bg-danger">Out</span>{% elif item[4]<=item[5] %}<span class="badge bg-warning">Low</span>{% else %}<span class="badge bg-success">OK</span>{% endif %}</td></tr>
        {% endfor %}
    </tbody></table></div></div></div>
    <div class="chart-card"><div class="chart-header d-flex justify-content-between align-items-center"><h5><i class="fas fa-star me-2 text-warning"></i>Essential Daily Stock Items</h5><span class="badge bg-primary fs-6">Must Maintain Daily</span></div><div class="chart-body p-0"><div class="table-responsive"><table class="table table-hover mb-0"><thead><tr><th>Product</th><th>Avg Daily Demand</th><th>Current Stock</th><th>Days Left</th><th>30d Sold</th><th>Status</th></tr></thead><tbody>
        {% for item in essential_items %}
        <tr class="{{ 'table-danger' if item.needs_reorder }}">
            <td><strong>{{ item.product.name }}</strong><br><small class="text-muted">{{ item.product.category.icon }} {{ item.product.category.name }}</small></td>
            <td><strong>{{ item.avg_daily_demand }}</strong>/day</td><td>{{ item.product.current_stock }} {{ item.product.unit }}</td>
            <td><span class="badge bg-{{ 'danger' if item.days_stock_left<=7 else 'warning' if item.days_stock_left<=14 else 'success' }} fs-6">{{ item.days_stock_left }}d</span></td>
            <td>{{ item.total_sold_30d }}</td>
            <td>{% if item.needs_reorder %}<span class="badge bg-danger"><i class="fas fa-exclamation-triangle me-1"></i>REORDER</span>{% else %}<span class="badge bg-success"><i class="fas fa-check me-1"></i>OK</span>{% endif %}</td></tr>
        {% endfor %}
    </tbody></table></div></div></div>
</div>
{% endblock %}
{% block scripts %}
<script>
document.addEventListener('DOMContentLoaded', function() {
    const colors=['#667eea','#764ba2','#f093fb','#4facfe','#43e97b','#fa709a','#fee140','#a8edea','#ff9a9e','#fad0c4','#fbc2eb','#a18cd1','#96e6a1','#d4fc79','#84fab0'];
    new Chart(document.getElementById('topSellersChart'),{type:'bar',data:{labels:{{ top_names|safe }},datasets:[{label:'Qty Sold',data:{{ top_quantities|safe }},backgroundColor:colors,borderRadius:8}]},options:{responsive:true,maintainAspectRatio:false,indexAxis:'y',plugins:{legend:{display:false}},scales:{x:{beginAtZero:true}}}});
    new Chart(document.getElementById('revenueChart'),{type:'doughnut',data:{labels:{{ top_names|safe }},datasets:[{data:{{ top_revenues|safe }},backgroundColor:colors,borderWidth:2,borderColor:'#fff'}]},options:{responsive:true,maintainAspectRatio:false,cutout:'50%',plugins:{legend:{position:'bottom',labels:{font:{size:10},boxWidth:10}}}}});
});
</script>
{% endblock %}
"""

ADD_PRODUCT_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container py-4"><div class="row justify-content-center"><div class="col-lg-8">
    <div class="chart-card"><div class="chart-header"><h5><i class="fas fa-plus-circle me-2"></i>Add New Product</h5></div><div class="chart-body">
        <form method="POST"><div class="row g-3">
            <div class="col-md-8"><label class="form-label">Product Name</label><input type="text" name="name" class="form-control" required></div>
            <div class="col-md-4"><label class="form-label">Category</label><select name="category_id" class="form-select" required>{% for c in categories %}<option value="{{ c.id }}">{{ c.icon }} {{ c.name }}</option>{% endfor %}</select></div>
            <div class="col-12"><label class="form-label">Description</label><textarea name="description" class="form-control" rows="2"></textarea></div>
            <div class="col-md-3"><label class="form-label">Unit Price (₹)</label><input type="number" name="unit_price" class="form-control" step="0.01" min="0" required></div>
            <div class="col-md-3"><label class="form-label">Current Stock</label><input type="number" name="current_stock" class="form-control" min="0" required></div>
            <div class="col-md-3"><label class="form-label">Min Stock</label><input type="number" name="minimum_stock" class="form-control" min="0" value="10" required></div>
            <div class="col-md-3"><label class="form-label">Max Stock</label><input type="number" name="maximum_stock" class="form-control" min="0" value="500" required></div>
            <div class="col-md-3"><label class="form-label">Unit</label><select name="unit" class="form-select"><option>strips</option><option>bottles</option><option>vials</option><option>packets</option><option>pieces</option><option>pairs</option><option>rolls</option><option>boxes</option><option>tubes</option><option>units</option><option>kits</option><option>ampoules</option><option>jars</option><option>packs</option><option>cans</option></select></div>
            <div class="col-md-4"><label class="form-label">Manufacturer</label><input type="text" name="manufacturer" class="form-control"></div>
            <div class="col-md-3"><label class="form-label">Expiry Date</label><input type="date" name="expiry_date" class="form-control"></div>
            <div class="col-md-2 d-flex align-items-end"><div class="form-check"><input type="checkbox" name="is_prescription" class="form-check-input" id="rx"><label class="form-check-label" for="rx">Rx Required</label></div></div>
        </div>
        <div class="mt-4 d-flex gap-2"><button type="submit" class="btn btn-primary btn-lg"><i class="fas fa-save me-2"></i>Save</button><a href="/inventory" class="btn btn-outline-secondary btn-lg">Cancel</a></div>
        </form>
    </div></div>
</div></div></div>
{% endblock %}
"""

PROFILE_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container py-4"><div class="row justify-content-center"><div class="col-lg-6">
    <div class="chart-card">
        <div class="chart-header text-center"><div class="profile-avatar-lg">{{ current_user.full_name[0] if current_user.full_name else 'U' }}</div><h4 class="mt-3">{{ current_user.full_name }}</h4><span class="badge bg-{{ 'info' if current_user.role=='pharmacist' else 'success' }} fs-6">{{ current_user.role|title }}</span>
            {% if current_user.role == 'doctor' %}<br><small class="text-muted mt-1 d-inline-block"><i class="fas fa-eye me-1"></i>Read-only access</small>{% endif %}
        </div>
        <div class="chart-body"><form method="POST">
            <div class="mb-3"><label class="form-label">Full Name</label><input type="text" name="full_name" class="form-control" value="{{ current_user.full_name or '' }}"></div>
            <div class="mb-3"><label class="form-label">Email</label><input type="email" class="form-control" value="{{ current_user.email }}" disabled></div>
            <div class="mb-3"><label class="form-label">Phone</label><input type="tel" name="phone" class="form-control" value="{{ current_user.phone or '' }}"></div>
            <div class="mb-3"><label class="form-label">Organization / Hospital</label><input type="text" name="organization" class="form-control" value="{{ current_user.organization or '' }}"></div>
            <div class="mb-3"><label class="form-label">Address</label><textarea name="address" class="form-control" rows="2">{{ current_user.address or '' }}</textarea></div>
            <button type="submit" class="btn btn-primary w-100"><i class="fas fa-save me-2"></i>Update Profile</button>
        </form></div>
    </div>
</div></div></div>
{% endblock %}
"""

BILLING_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container-fluid px-4 py-4">
    <div class="page-header mb-4">
        <div><h2><i class="fas fa-file-invoice-dollar me-2"></i>Billing</h2><p class="text-muted">Create bills and sell medicines</p></div>
        <div class="d-flex gap-2">
            <a href="/customers" class="btn btn-outline-primary"><i class="fas fa-users me-2"></i>Customers</a>
            <a href="/bill_history" class="btn btn-outline-secondary"><i class="fas fa-history me-2"></i>Bill History</a>
        </div>
    </div>

    <div class="row g-4">
        <div class="col-xl-8">
            <div class="chart-card">
                <div class="chart-header"><h5><i class="fas fa-receipt me-2"></i>New Bill</h5></div>
                <div class="chart-body">
                    <form method="POST" action="/create_bill" id="billForm">
                        <div class="row g-3 mb-4">
                            <div class="col-md-5">
                                <label class="form-label"><i class="fas fa-user me-2"></i>Customer Name</label>
                                <input type="text" name="customer_name" id="customerName" class="form-control" placeholder="Enter customer name" required list="customerList">
                                <datalist id="customerList">
                                    {% for c in customers %}<option value="{{ c.name }}" data-phone="{{ c.phone }}" data-id="{{ c.id }}">{{ c.name }} - {{ c.phone }}</option>{% endfor %}
                                </datalist>
                            </div>
                            <div class="col-md-4">
                                <label class="form-label"><i class="fas fa-phone me-2"></i>Phone Number</label>
                                <input type="tel" name="customer_phone" id="customerPhone" class="form-control" placeholder="Enter phone number" required>
                            </div>
                            <div class="col-md-3 d-flex align-items-end">
                                <button type="button" class="btn btn-outline-info w-100" onclick="loadMonthlyCustomer()"><i class="fas fa-redo me-2"></i>Load Monthly</button>
                            </div>
                        </div>

                        <div id="billItems">
                            <div class="bill-item-row row g-2 align-items-end mb-2" data-row="0">
                                <div class="col-md-5">
                                    <label class="form-label">Medicine</label>
                                    <select name="product_id_0" class="form-select product-select" onchange="updatePrice(this, 0)" required>
                                        <option value="">Select Medicine</option>
                                        {% for p in products %}
                                        <option value="{{ p.id }}" data-price="{{ p.unit_price }}" data-stock="{{ p.current_stock }}" data-unit="{{ p.unit }}">{{ p.name }} (Stock: {{ p.current_stock }} {{ p.unit }}) - ₹{{ "%.2f"|format(p.unit_price) }}</option>
                                        {% endfor %}
                                    </select>
                                </div>
                                <div class="col-md-2">
                                    <label class="form-label">Qty</label>
                                    <input type="number" name="quantity_0" class="form-control qty-input" min="1" value="1" onchange="updateRowTotal(0)" oninput="updateRowTotal(0)">
                                </div>
                                <div class="col-md-2">
                                    <label class="form-label">Price (₹)</label>
                                    <input type="text" class="form-control price-display" id="price_0" readonly>
                                </div>
                                <div class="col-md-2">
                                    <label class="form-label">Total (₹)</label>
                                    <input type="text" class="form-control total-display" id="total_0" readonly>
                                </div>
                                <div class="col-md-1 d-flex align-items-end">
                                    <button type="button" class="btn btn-outline-danger btn-sm" onclick="removeRow(this)" style="display:none;"><i class="fas fa-trash"></i></button>
                                </div>
                            </div>
                        </div>

                        <div class="d-flex justify-content-between align-items-center mt-3 mb-4">
                            <button type="button" class="btn btn-outline-primary" onclick="addRow()"><i class="fas fa-plus me-2"></i>Add Medicine</button>
                            <div class="text-end">
                                <h4 class="mb-0">Grand Total: ₹<span id="grandTotal">0.00</span></h4>
                            </div>
                        </div>

                        <input type="hidden" name="item_count" id="itemCount" value="1">
                        <button type="submit" class="btn btn-success btn-lg w-100"><i class="fas fa-check-circle me-2"></i>Generate Bill</button>
                    </form>
                </div>
            </div>
        </div>

        <div class="col-xl-4">
            <div class="chart-card mb-4">
                <div class="chart-header"><h5><i class="fas fa-sync me-2 text-primary"></i>Monthly Customers</h5></div>
                <div class="chart-body p-0">
                    <div class="table-responsive" style="max-height:400px;overflow-y:auto;">
                        <table class="table table-hover mb-0">
                            <thead class="sticky-top bg-white"><tr><th>Customer</th><th>Phone</th><th>Medicines</th><th>Action</th></tr></thead>
                            <tbody>
                                {% for c in monthly_customers %}
                                <tr>
                                    <td><strong>{{ c.name }}</strong></td>
                                    <td>{{ c.phone }}</td>
                                    <td><span class="badge bg-primary">{{ c.monthly_medicines|length }}</span></td>
                                    <td><a href="/fill_monthly/{{ c.id }}" class="btn btn-sm btn-success"><i class="fas fa-prescription me-1"></i>Fill</a></td>
                                </tr>
                                {% else %}
                                <tr><td colspan="4" class="text-center text-muted py-3">No monthly customers</td></tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <div class="chart-card">
                <div class="chart-header"><h5><i class="fas fa-history me-2"></i>Recent Bills</h5></div>
                <div class="chart-body p-0">
                    <div class="table-responsive" style="max-height:300px;overflow-y:auto;">
                        <table class="table table-hover mb-0">
                            <thead class="sticky-top bg-white"><tr><th>#</th><th>Customer</th><th>Amount</th><th>Date</th></tr></thead>
                            <tbody>
                                {% for b in recent_bills %}
                                <tr>
                                    <td><a href="/view_bill/{{ b.id }}">#{{ b.id }}</a></td>
                                    <td>{{ b.customer.name }}</td>
                                    <td>₹{{ "%.2f"|format(b.total_amount) }}</td>
                                    <td>{{ b.bill_date.strftime('%d %b %H:%M') }}</td>
                                </tr>
                                {% else %}
                                <tr><td colspan="4" class="text-center text-muted py-3">No bills yet</td></tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>
{% endblock %}
{% block scripts %}
<script>
let rowCount = 1;
const products = {{ products_json|safe }};

document.getElementById('customerName').addEventListener('input', function() {
    const options = document.querySelectorAll('#customerList option');
    for (let opt of options) {
        if (opt.value === this.value) {
            document.getElementById('customerPhone').value = opt.dataset.phone;
            break;
        }
    }
});

function loadMonthlyCustomer() {
    const name = document.getElementById('customerName').value;
    const phone = document.getElementById('customerPhone').value;
    if (!name && !phone) { alert('Enter customer name or phone first'); return; }
    fetch('/api/monthly_medicines?name=' + encodeURIComponent(name) + '&phone=' + encodeURIComponent(phone))
        .then(r => r.json())
        .then(data => {
            if (data.error) { alert(data.error); return; }
            document.getElementById('billItems').innerHTML = '';
            rowCount = 0;
            data.medicines.forEach(med => {
                addRow();
                const row = rowCount - 1;
                const select = document.querySelector(`[name="product_id_${row}"]`);
                if (select) {
                    select.value = med.product_id;
                    updatePrice(select, row);
                    document.querySelector(`[name="quantity_${row}"]`).value = med.quantity;
                    updateRowTotal(row);
                }
            });
            if (data.medicines.length === 0) { alert('No monthly medicines found for this customer'); addRow(); }
        });
}

function updatePrice(select, row) {
    const opt = select.options[select.selectedIndex];
    if (opt && opt.value) {
        document.getElementById('price_' + row).value = parseFloat(opt.dataset.price).toFixed(2);
        updateRowTotal(row);
    }
}

function updateRowTotal(row) {
    const price = parseFloat(document.getElementById('price_' + row).value) || 0;
    const qty = parseInt(document.querySelector(`[name="quantity_${row}"]`).value) || 0;
    document.getElementById('total_' + row).value = (price * qty).toFixed(2);
    updateGrandTotal();
}

function updateGrandTotal() {
    let total = 0;
    document.querySelectorAll('.total-display').forEach(el => { total += parseFloat(el.value) || 0; });
    document.getElementById('grandTotal').textContent = total.toFixed(2);
}

function addRow() {
    const row = rowCount;
    const html = `<div class="bill-item-row row g-2 align-items-end mb-2" data-row="${row}">
        <div class="col-md-5"><select name="product_id_${row}" class="form-select product-select" onchange="updatePrice(this, ${row})" required>
            <option value="">Select Medicine</option>
            ${products.map(p => `<option value="${p.id}" data-price="${p.unit_price}" data-stock="${p.current_stock}" data-unit="${p.unit}">${p.name} (Stock: ${p.current_stock} ${p.unit}) - ₹${p.unit_price.toFixed(2)}</option>`).join('')}
        </select></div>
        <div class="col-md-2"><input type="number" name="quantity_${row}" class="form-control qty-input" min="1" value="1" onchange="updateRowTotal(${row})" oninput="updateRowTotal(${row})"></div>
        <div class="col-md-2"><input type="text" class="form-control price-display" id="price_${row}" readonly></div>
        <div class="col-md-2"><input type="text" class="form-control total-display" id="total_${row}" readonly></div>
        <div class="col-md-1 d-flex align-items-end"><button type="button" class="btn btn-outline-danger btn-sm" onclick="removeRow(this)"><i class="fas fa-trash"></i></button></div>
    </div>`;
    document.getElementById('billItems').insertAdjacentHTML('beforeend', html);
    rowCount++;
    document.getElementById('itemCount').value = rowCount;
    updateRemoveButtons();
}

function removeRow(btn) {
    btn.closest('.bill-item-row').remove();
    updateGrandTotal();
    updateRemoveButtons();
}

function updateRemoveButtons() {
    const rows = document.querySelectorAll('.bill-item-row');
    rows.forEach(r => {
        const btn = r.querySelector('.btn-outline-danger');
        if (btn) btn.style.display = rows.length > 1 ? 'block' : 'none';
    });
}
</script>
{% endblock %}
"""

CUSTOMERS_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container-fluid px-4 py-4">
    <div class="page-header mb-4">
        <div><h2><i class="fas fa-users me-2"></i>Customer Database</h2><p class="text-muted">{{ customers|length }} customers</p></div>
        <button class="btn btn-primary" data-bs-toggle="modal" data-bs-target="#addCustomerModal"><i class="fas fa-plus me-2"></i>Add Customer</button>
    </div>

    <div class="filter-bar mb-4">
        <form method="GET" class="row g-3 align-items-end">
            <div class="col-md-4"><label class="form-label">Search</label><div class="input-group"><span class="input-group-text"><i class="fas fa-search"></i></span><input type="text" name="search" class="form-control" placeholder="Name or phone..." value="{{ search }}"></div></div>
            <div class="col-md-3"><label class="form-label">Type</label><select name="filter_type" class="form-select"><option value="">All</option><option value="monthly" {{ 'selected' if filter_type=='monthly' }}>Monthly Customers</option><option value="regular" {{ 'selected' if filter_type=='regular' }}>Regular</option></select></div>
            <div class="col-md-2"><button type="submit" class="btn btn-primary w-100"><i class="fas fa-filter me-2"></i>Filter</button></div>
        </form>
    </div>

    <div class="row g-3">
        {% for c in customers %}
        <div class="col-xl-4 col-md-6">
            <div class="customer-card">
                <div class="d-flex justify-content-between align-items-start mb-3">
                    <div>
                        <h5 class="mb-1"><i class="fas fa-user me-2 text-primary"></i>{{ c.name }}</h5>
                        <p class="text-muted mb-0"><i class="fas fa-phone me-1"></i>{{ c.phone }}</p>
                    </div>
                    {% if c.is_monthly %}<span class="monthly-badge"><i class="fas fa-sync me-1"></i>Monthly</span>{% endif %}
                </div>
                <div class="mb-3">
                    <small class="text-muted">Total Bills: <strong>{{ c.bills|length }}</strong></small><br>
                    <small class="text-muted">Joined: {{ c.created_at.strftime('%d %b %Y') }}</small>
                </div>
                {% if c.is_monthly and c.monthly_medicines %}
                <div class="mb-3">
                    <small class="fw-bold text-primary"><i class="fas fa-pills me-1"></i>Monthly Medicines:</small>
                    <ul class="list-unstyled mt-1 mb-0">
                        {% for mm in c.monthly_medicines %}
                        <li class="small"><i class="fas fa-check-circle text-success me-1"></i>{{ mm.product.name }} × {{ mm.quantity }}</li>
                        {% endfor %}
                    </ul>
                </div>
                {% endif %}
                <div class="d-flex gap-2">
                    <a href="/customer_detail/{{ c.id }}" class="btn btn-sm btn-outline-primary flex-fill"><i class="fas fa-eye me-1"></i>View</a>
                    {% if c.is_monthly %}
                    <a href="/fill_monthly/{{ c.id }}" class="btn btn-sm btn-success flex-fill"><i class="fas fa-prescription me-1"></i>Fill Monthly</a>
                    {% endif %}
                    <a href="/manage_monthly/{{ c.id }}" class="btn btn-sm btn-outline-info"><i class="fas fa-cog"></i></a>
                </div>
            </div>
        </div>
        {% endfor %}
    </div>
    {% if not customers %}<div class="empty-state text-center py-5"><i class="fas fa-users fa-3x text-muted mb-3"></i><h4>No customers found</h4></div>{% endif %}
</div>

<!-- Add Customer Modal -->
<div class="modal fade" id="addCustomerModal" tabindex="-1">
    <div class="modal-dialog"><div class="modal-content">
        <div class="modal-header"><h5 class="modal-title"><i class="fas fa-user-plus me-2"></i>Add Customer</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
        <form method="POST" action="/add_customer">
            <div class="modal-body">
                <div class="mb-3"><label class="form-label">Customer Name</label><input type="text" name="name" class="form-control" required></div>
                <div class="mb-3"><label class="form-label">Phone Number</label><input type="tel" name="phone" class="form-control" required></div>
                <div class="mb-3"><div class="form-check"><input type="checkbox" name="is_monthly" class="form-check-input" id="isMonthly"><label class="form-check-label" for="isMonthly">Monthly Customer (needs regular medicines)</label></div></div>
            </div>
            <div class="modal-footer"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button><button type="submit" class="btn btn-primary"><i class="fas fa-save me-2"></i>Save</button></div>
        </form>
    </div></div>
</div>
{% endblock %}
"""

CUSTOMER_DETAIL_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container-fluid px-4 py-4">
    <div class="page-header mb-4">
        <div>
            <h2><i class="fas fa-user me-2"></i>{{ customer.name }}</h2>
            <p class="text-muted"><i class="fas fa-phone me-1"></i>{{ customer.phone }}
                {% if customer.is_monthly %}<span class="monthly-badge ms-2"><i class="fas fa-sync me-1"></i>Monthly Customer</span>{% endif %}
            </p>
        </div>
        <div class="d-flex gap-2">
            {% if customer.is_monthly %}<a href="/fill_monthly/{{ customer.id }}" class="btn btn-success"><i class="fas fa-prescription me-2"></i>Fill Monthly</a>{% endif %}
            <a href="/manage_monthly/{{ customer.id }}" class="btn btn-outline-info"><i class="fas fa-cog me-2"></i>Manage Monthly</a>
            <a href="/customers" class="btn btn-outline-secondary"><i class="fas fa-arrow-left me-2"></i>Back</a>
        </div>
    </div>

    {% if customer.is_monthly and customer.monthly_medicines %}
    <div class="chart-card mb-4">
        <div class="chart-header"><h5><i class="fas fa-pills me-2 text-primary"></i>Monthly Medicines</h5></div>
        <div class="chart-body p-0">
            <div class="table-responsive"><table class="table table-hover mb-0"><thead><tr><th>Medicine</th><th>Quantity</th><th>Unit Price</th><th>Monthly Cost</th></tr></thead><tbody>
                {% set ns = namespace(monthly_total=0) %}
                {% for mm in customer.monthly_medicines %}
                {% set ns.monthly_total = ns.monthly_total + (mm.product.unit_price * mm.quantity) %}
                <tr><td><strong>{{ mm.product.name }}</strong><br><small class="text-muted">{{ mm.product.category.icon }} {{ mm.product.category.name }}</small></td>
                    <td>{{ mm.quantity }} {{ mm.product.unit }}</td><td>₹{{ "%.2f"|format(mm.product.unit_price) }}</td><td>₹{{ "%.2f"|format(mm.product.unit_price * mm.quantity) }}</td></tr>
                {% endfor %}
                <tr class="table-primary"><td colspan="3" class="text-end fw-bold">Monthly Total:</td><td class="fw-bold">₹{{ "%.2f"|format(ns.monthly_total) }}</td></tr>
            </tbody></table></div>
        </div>
    </div>
    {% endif %}

    <div class="chart-card">
        <div class="chart-header"><h5><i class="fas fa-history me-2"></i>Bill History</h5></div>
        <div class="chart-body p-0">
            <div class="table-responsive"><table class="table table-hover mb-0"><thead><tr><th>Bill #</th><th>Date</th><th>Items</th><th>Total</th><th>Action</th></tr></thead><tbody>
                {% for b in customer.bills|sort(attribute='bill_date', reverse=True) %}
                <tr><td>#{{ b.id }}</td><td>{{ b.bill_date.strftime('%d %b %Y %H:%M') }}</td><td>{{ b.items|length }} items</td><td>₹{{ "%.2f"|format(b.total_amount) }}</td>
                    <td><a href="/view_bill/{{ b.id }}" class="btn btn-sm btn-outline-primary"><i class="fas fa-eye"></i></a></td></tr>
                {% else %}<tr><td colspan="5" class="text-center text-muted py-4">No bills yet</td></tr>{% endfor %}
            </tbody></table></div>
        </div>
    </div>
</div>
{% endblock %}
"""

MANAGE_MONTHLY_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container py-4"><div class="row justify-content-center"><div class="col-lg-8">
    <div class="chart-card">
        <div class="chart-header">
            <h5><i class="fas fa-cog me-2"></i>Manage Monthly Medicines - {{ customer.name }}</h5>
        </div>
        <div class="chart-body">
            <div class="mb-3">
                <div class="form-check form-switch">
                    <form method="POST" action="/toggle_monthly/{{ customer.id }}">
                        <input class="form-check-input" type="checkbox" id="monthlyToggle" {{ 'checked' if customer.is_monthly }} onchange="this.form.submit()">
                        <label class="form-check-label" for="monthlyToggle"><strong>Monthly Customer</strong></label>
                    </form>
                </div>
            </div>

            {% if customer.monthly_medicines %}
            <h6 class="mb-3"><i class="fas fa-list me-2"></i>Current Monthly Medicines:</h6>
            <div class="table-responsive mb-4">
                <table class="table table-hover"><thead><tr><th>Medicine</th><th>Quantity</th><th>Action</th></tr></thead><tbody>
                    {% for mm in customer.monthly_medicines %}
                    <tr><td>{{ mm.product.name }}</td><td>{{ mm.quantity }} {{ mm.product.unit }}</td>
                        <td><form method="POST" action="/remove_monthly_medicine/{{ mm.id }}" class="d-inline"><button type="submit" class="btn btn-sm btn-outline-danger"><i class="fas fa-trash"></i></button></form></td></tr>
                    {% endfor %}
                </tbody></table>
            </div>
            {% endif %}

            <h6 class="mb-3"><i class="fas fa-plus me-2"></i>Add Monthly Medicine:</h6>
            <form method="POST" action="/add_monthly_medicine/{{ customer.id }}">
                <div class="row g-3">
                    <div class="col-md-6">
                        <select name="product_id" class="form-select" required>
                            <option value="">Select Medicine</option>
                            {% for p in products %}<option value="{{ p.id }}">{{ p.name }} - ₹{{ "%.2f"|format(p.unit_price) }}</option>{% endfor %}
                        </select>
                    </div>
                    <div class="col-md-3"><input type="number" name="quantity" class="form-control" placeholder="Qty" min="1" value="1" required></div>
                    <div class="col-md-3"><button type="submit" class="btn btn-primary w-100"><i class="fas fa-plus me-2"></i>Add</button></div>
                </div>
            </form>

            <div class="mt-4"><a href="/customers" class="btn btn-outline-secondary"><i class="fas fa-arrow-left me-2"></i>Back to Customers</a></div>
        </div>
    </div>
</div></div></div>
{% endblock %}
"""

VIEW_BILL_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container py-4"><div class="row justify-content-center"><div class="col-lg-8">
    <div class="chart-card">
        <div class="chart-header d-flex justify-content-between align-items-center">
            <h5><i class="fas fa-receipt me-2"></i>Bill #{{ bill.id }}</h5>
            <button class="btn btn-outline-primary btn-sm" onclick="window.print()"><i class="fas fa-print me-2"></i>Print</button>
        </div>
        <div class="chart-body">
            <div class="row mb-4">
                <div class="col-md-6">
                    <h6 class="text-muted">Customer</h6>
                    <p class="mb-1"><strong>{{ bill.customer.name }}</strong></p>
                    <p class="mb-0"><i class="fas fa-phone me-1"></i>{{ bill.customer.phone }}</p>
                </div>
                <div class="col-md-6 text-md-end">
                    <h6 class="text-muted">Bill Details</h6>
                    <p class="mb-1">Date: {{ bill.bill_date.strftime('%d %b %Y %H:%M') }}</p>
                    <p class="mb-0">Billed by: {{ bill.pharmacist.full_name }}</p>
                </div>
            </div>
            <div class="table-responsive">
                <table class="table"><thead><tr><th>#</th><th>Medicine</th><th>Qty</th><th>Unit Price</th><th>Total</th></tr></thead><tbody>
                    {% for item in bill.items %}
                    <tr><td>{{ loop.index }}</td><td>{{ item.product.name }}</td><td>{{ item.quantity }}</td><td>₹{{ "%.2f"|format(item.unit_price) }}</td><td>₹{{ "%.2f"|format(item.total_price) }}</td></tr>
                    {% endfor %}
                </tbody>
                <tfoot><tr class="table-primary"><td colspan="4" class="text-end fw-bold">Grand Total:</td><td class="fw-bold fs-5">₹{{ "%.2f"|format(bill.total_amount) }}</td></tr></tfoot>
                </table>
            </div>
            <div class="mt-3 d-flex gap-2">
                <a href="/billing" class="btn btn-outline-primary"><i class="fas fa-plus me-2"></i>New Bill</a>
                <a href="/customer_detail/{{ bill.customer.id }}" class="btn btn-outline-secondary"><i class="fas fa-user me-2"></i>Customer Profile</a>
            </div>
        </div>
    </div>
</div></div></div>
{% endblock %}
"""

BILL_HISTORY_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container-fluid px-4 py-4">
    <div class="page-header mb-4">
        <div><h2><i class="fas fa-history me-2"></i>Bill History</h2><p class="text-muted">All bills created by you</p></div>
        <a href="/billing" class="btn btn-primary"><i class="fas fa-plus me-2"></i>New Bill</a>
    </div>
    <div class="chart-card">
        <div class="chart-body p-0">
            <div class="table-responsive"><table class="table table-hover mb-0"><thead><tr><th>Bill #</th><th>Customer</th><th>Phone</th><th>Items</th><th>Total</th><th>Date</th><th>Action</th></tr></thead><tbody>
                {% for b in bills %}
                <tr><td>#{{ b.id }}</td><td><strong>{{ b.customer.name }}</strong></td><td>{{ b.customer.phone }}</td><td>{{ b.items|length }}</td><td>₹{{ "%.2f"|format(b.total_amount) }}</td><td>{{ b.bill_date.strftime('%d %b %Y %H:%M') }}</td>
                    <td><a href="/view_bill/{{ b.id }}" class="btn btn-sm btn-outline-primary"><i class="fas fa-eye"></i></a></td></tr>
                {% else %}<tr><td colspan="7" class="text-center text-muted py-4">No bills found</td></tr>{% endfor %}
            </tbody></table></div>
        </div>
    </div>
</div>
{% endblock %}
"""

FILL_MONTHLY_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="container py-4"><div class="row justify-content-center"><div class="col-lg-8">
    <div class="chart-card">
        <div class="chart-header"><h5><i class="fas fa-prescription me-2"></i>Fill Monthly Medicines - {{ customer.name }}</h5></div>
        <div class="chart-body">
            <div class="mb-4">
                <p><i class="fas fa-phone me-2"></i>{{ customer.phone }}</p>
            </div>

            {% if customer.monthly_medicines %}
            <form method="POST" action="/process_monthly/{{ customer.id }}">
                <div class="table-responsive mb-4">
                    <table class="table"><thead><tr><th><input type="checkbox" id="selectAll" checked onchange="toggleAll(this)"></th><th>Medicine</th><th>Qty</th><th>Price</th><th>Total</th><th>Stock</th></tr></thead><tbody>
                        {% set ns = namespace(grand_total=0) %}
                        {% for mm in customer.monthly_medicines %}
                        {% set item_total = mm.product.unit_price * mm.quantity %}
                        {% set ns.grand_total = ns.grand_total + item_total %}
                        <tr>
                            <td><input type="checkbox" name="selected_{{ mm.id }}" value="1" checked class="item-check"></td>
                            <td><strong>{{ mm.product.name }}</strong></td>
                            <td><input type="number" name="qty_{{ mm.id }}" value="{{ mm.quantity }}" min="1" class="form-control form-control-sm" style="width:80px;"></td>
                            <td>₹{{ "%.2f"|format(mm.product.unit_price) }}</td>
                            <td>₹{{ "%.2f"|format(item_total) }}</td>
                            <td><span class="badge bg-{{ 'success' if mm.product.current_stock >= mm.quantity else 'danger' }}">{{ mm.product.current_stock }} {{ mm.product.unit }}</span></td>
                        </tr>
                        {% endfor %}
                        <tr class="table-primary"><td colspan="4" class="text-end fw-bold">Estimated Total:</td><td colspan="2" class="fw-bold fs-5">₹{{ "%.2f"|format(ns.grand_total) }}</td></tr>
                    </tbody></table>
                </div>
                <button type="submit" class="btn btn-success btn-lg w-100"><i class="fas fa-check-circle me-2"></i>Generate Monthly Bill</button>
            </form>
            {% else %}
            <div class="text-center py-4">
                <i class="fas fa-pills fa-3x text-muted mb-3"></i>
                <h5>No monthly medicines configured</h5>
                <a href="/manage_monthly/{{ customer.id }}" class="btn btn-primary mt-2"><i class="fas fa-plus me-2"></i>Add Medicines</a>
            </div>
            {% endif %}

            <div class="mt-3"><a href="/customers" class="btn btn-outline-secondary"><i class="fas fa-arrow-left me-2"></i>Back</a></div>
        </div>
    </div>
</div></div></div>
{% endblock %}
{% block scripts %}
<script>
function toggleAll(master) {
    document.querySelectorAll('.item-check').forEach(cb => cb.checked = master.checked);
}
</script>
{% endblock %}
"""

# ============================================================
# TEMPLATE REGISTRY
# ============================================================
TEMPLATES = {
    'base.html': BASE_HTML,
    'index.html': INDEX_HTML,
    'login.html': LOGIN_HTML,
    'register.html': REGISTER_HTML,
    'dashboard_pharmacist.html': DASHBOARD_PHARMACIST_HTML,
    'dashboard_doctor.html': DASHBOARD_DOCTOR_HTML,
    'inventory.html': INVENTORY_HTML,
    'analytics.html': ANALYTICS_HTML,
    'predictions.html': PREDICTIONS_HTML,
    'top_sellers.html': TOP_SELLERS_HTML,
    'add_product.html': ADD_PRODUCT_HTML,
    'profile.html': PROFILE_HTML,
    'billing.html': BILLING_HTML,
    'customers.html': CUSTOMERS_HTML,
    'customer_detail.html': CUSTOMER_DETAIL_HTML,
    'manage_monthly.html': MANAGE_MONTHLY_HTML,
    'view_bill.html': VIEW_BILL_HTML,
    'bill_history.html': BILL_HISTORY_HTML,
    'fill_monthly.html': FILL_MONTHLY_HTML,
}

# Override Jinja2 loader to use in-memory templates
import jinja2
class DictLoader(jinja2.BaseLoader):
    def __init__(self, mapping):
        self.mapping = mapping
    def get_source(self, environment, template):
        if template in self.mapping:
            source = self.mapping[template]
            return source, template, lambda: True
        raise jinja2.TemplateNotFound(template)

app.jinja_loader = DictLoader(TEMPLATES)

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def get_pharmacist_stats():
    total_products = Product.query.count()
    low_stock_items = Product.query.filter(Product.current_stock <= Product.minimum_stock).all()
    out_of_stock = Product.query.filter(Product.current_stock <= 0).count()
    expiring_soon = Product.query.filter(
        Product.expiry_date != None,
        Product.expiry_date <= datetime.utcnow().date() + timedelta(days=30)
    ).count()
    sales_7days, labels_7days = [], []
    for i in range(6, -1, -1):
        date = datetime.utcnow().date() - timedelta(days=i)
        total = db.session.query(func.sum(SalesRecord.total_price)).filter(func.date(SalesRecord.sale_date) == date).scalar() or 0
        sales_7days.append(float(total))
        labels_7days.append(date.strftime('%d %b'))
    categories = Category.query.all()
    cat_labels = [c.name.split(' - ')[0] if ' - ' in c.name else c.name for c in categories]
    cat_stock = [int(db.session.query(func.sum(Product.current_stock)).filter(Product.category_id == c.id).scalar() or 0) for c in categories]
    monthly_revenue, monthly_labels = [], []
    for i in range(5, -1, -1):
        ms = (datetime.utcnow().replace(day=1) - timedelta(days=30*i)).replace(day=1)
        me = (datetime.utcnow().replace(day=1) - timedelta(days=30*(i-1))).replace(day=1) if i > 0 else datetime.utcnow()
        t = db.session.query(func.sum(SalesRecord.total_price)).filter(SalesRecord.sale_date >= ms, SalesRecord.sale_date < me).scalar() or 0
        monthly_revenue.append(float(t))
        monthly_labels.append(ms.strftime('%b %Y'))
    return {
        'total_products': total_products, 'low_stock_items': low_stock_items,
        'low_stock_count': len(low_stock_items), 'out_of_stock': out_of_stock,
        'expiring_soon': expiring_soon,
        'sales_7days': json.dumps(sales_7days), 'labels_7days': json.dumps(labels_7days),
        'cat_labels': json.dumps(cat_labels), 'cat_stock': json.dumps(cat_stock),
        'monthly_revenue': json.dumps(monthly_revenue), 'monthly_labels': json.dumps(monthly_labels),
        'total_revenue_today': sales_7days[-1] if sales_7days else 0
    }

def get_doctor_stats():
    all_products = Product.query.all()
    low_stock = [p for p in all_products if p.current_stock <= p.minimum_stock]
    in_stock = [p for p in all_products if p.current_stock > 0]
    out_of_stock = [p for p in all_products if p.current_stock <= 0]
    return {
        'categories': Category.query.all(),
        'low_stock': low_stock,
        'in_stock_count': len(in_stock),
        'out_of_stock_count': len(out_of_stock),
        'total_products': len(all_products)
    }

# ============================================================
# ROUTES
# ============================================================

@app.route('/')
def index():
    return render_template_string(TEMPLATES['index.html'],
        total_products=Product.query.count(), total_categories=Category.query.count(),
        low_stock=Product.query.filter(Product.current_stock <= Product.minimum_stock).count(),
        today_sales=db.session.query(func.sum(SalesRecord.total_price)).filter(func.date(SalesRecord.sale_date) == datetime.utcnow().date()).scalar() or 0)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username'), role=request.form.get('role')).first()
        if user and user.check_password(request.form.get('password')):
            login_user(user)
            flash(f'Welcome back, {user.full_name}!', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid credentials.', 'danger')
    return render_template_string(TEMPLATES['login.html'])

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        pw = request.form.get('password')
        if pw != request.form.get('confirm_password'):
            flash('Passwords do not match!', 'danger')
            return redirect(url_for('register'))
        if User.query.filter_by(username=request.form.get('username')).first():
            flash('Username exists!', 'danger')
            return redirect(url_for('register'))
        if User.query.filter_by(email=request.form.get('email')).first():
            flash('Email registered!', 'danger')
            return redirect(url_for('register'))
        u = User(username=request.form['username'], email=request.form['email'], role=request.form['role'],
                 full_name=request.form.get('full_name'), phone=request.form.get('phone'),
                 organization=request.form.get('organization'), address=request.form.get('address'))
        u.set_password(pw)
        db.session.add(u)
        db.session.commit()
        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('login'))
    return render_template_string(TEMPLATES['register.html'])

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out.', 'info')
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.role == 'pharmacist':
        return render_template_string(TEMPLATES['dashboard_pharmacist.html'], stats=get_pharmacist_stats())
    return render_template_string(TEMPLATES['dashboard_doctor.html'], stats=get_doctor_stats())

@app.route('/inventory')
@login_required
def inventory():
    q = Product.query
    cat_id = request.args.get('category', type=int)
    search = request.args.get('search', '')
    sf = request.args.get('stock_filter', '')
    ef = request.args.get('expiry_filter', '')
    if cat_id: q = q.filter_by(category_id=cat_id)
    if search: q = q.filter(Product.name.ilike(f'%{search}%'))
    if sf == 'low': q = q.filter(Product.current_stock <= Product.minimum_stock)
    elif sf == 'out': q = q.filter(Product.current_stock <= 0)
    elif sf == 'adequate': q = q.filter(Product.current_stock > Product.minimum_stock * 2)
    if ef == 'expired':
        q = q.filter(Product.expiry_date != None, Product.expiry_date <= datetime.utcnow().date())
    elif ef == 'expiring_soon':
        q = q.filter(Product.expiry_date != None, Product.expiry_date <= datetime.utcnow().date() + timedelta(days=30), Product.expiry_date > datetime.utcnow().date())
    elif ef == 'expiring_3months':
        q = q.filter(Product.expiry_date != None, Product.expiry_date <= datetime.utcnow().date() + timedelta(days=90), Product.expiry_date > datetime.utcnow().date() + timedelta(days=30))
    elif ef == 'valid':
        q = q.filter(db.or_(Product.expiry_date == None, Product.expiry_date > datetime.utcnow().date() + timedelta(days=90)))
    return render_template_string(TEMPLATES['inventory.html'], products=q.order_by(Product.name).all(),
        categories=Category.query.all(), selected_category=cat_id, search=search, stock_filter=sf, expiry_filter=ef)

@app.route('/add_product', methods=['GET', 'POST'])
@login_required
@pharmacist_required
def add_product():
    if request.method == 'POST':
        p = Product(name=request.form['name'], category_id=int(request.form['category_id']),
            description=request.form.get('description'), unit_price=float(request.form['unit_price']),
            current_stock=int(request.form['current_stock']), minimum_stock=int(request.form['minimum_stock']),
            maximum_stock=int(request.form['maximum_stock']), unit=request.form.get('unit', 'units'),
            manufacturer=request.form.get('manufacturer'), is_prescription=bool(request.form.get('is_prescription')),
            added_by=current_user.id)
        exp = request.form.get('expiry_date')
        if exp: p.expiry_date = datetime.strptime(exp, '%Y-%m-%d').date()
        db.session.add(p)
        db.session.commit()
        flash('Product added!', 'success')
        return redirect(url_for('inventory'))
    return render_template_string(TEMPLATES['add_product.html'], categories=Category.query.all())

@app.route('/update_stock/<int:pid>', methods=['POST'])
@login_required
@pharmacist_required
def update_stock(pid):
    p = Product.query.get_or_404(pid)
    ns = request.form.get('new_stock', type=int)
    if ns is not None:
        p.current_stock = ns
        db.session.commit()
        flash(f'Stock updated for {p.name}', 'success')
    return redirect(url_for('inventory'))

@app.route('/analytics')
@login_required
def analytics():
    sales_trend, sales_labels = [], []
    for i in range(29, -1, -1):
        d = datetime.utcnow().date() - timedelta(days=i)
        t = db.session.query(func.sum(SalesRecord.total_price)).filter(func.date(SalesRecord.sale_date) == d).scalar() or 0
        sales_trend.append(float(t))
        sales_labels.append(d.strftime('%d %b'))
    categories = Category.query.all()
    csl, csv = [], []
    for c in categories:
        pids = [p.id for p in Product.query.filter_by(category_id=c.id).all()]
        if pids:
            t = db.session.query(func.sum(SalesRecord.total_price)).filter(SalesRecord.product_id.in_(pids), SalesRecord.sale_date >= datetime.utcnow() - timedelta(days=30)).scalar() or 0
            if t > 0:
                csl.append(c.name.split(' - ')[0] if ' - ' in c.name else c.name)
                csv.append(float(t))
    bt = db.session.query(SalesRecord.buyer_type, func.sum(SalesRecord.total_price)).filter(SalesRecord.sale_date >= datetime.utcnow() - timedelta(days=30)).group_by(SalesRecord.buyer_type).all()
    bl = [b[0].title() for b in bt]
    bv = [float(b[1]) for b in bt]
    sh = [Product.query.filter(Product.current_stock > Product.minimum_stock * 2).count(),
          Product.query.filter(Product.current_stock <= Product.minimum_stock * 2, Product.current_stock > Product.minimum_stock).count(),
          Product.query.filter(Product.current_stock <= Product.minimum_stock, Product.current_stock > 0).count(),
          Product.query.filter(Product.current_stock <= 0).count()]
    return render_template_string(TEMPLATES['analytics.html'], sales_trend=json.dumps(sales_trend),
        sales_labels=json.dumps(sales_labels), cat_sales_labels=json.dumps(csl), cat_sales_values=json.dumps(csv),
        buyer_labels=json.dumps(bl), buyer_values=json.dumps(bv), stock_health=json.dumps(sh))

@app.route('/predictions')
@login_required
def predictions():
    products = Product.query.all()
    prediction_data = []
    for p in products:
        rs = db.session.query(func.sum(SalesRecord.quantity)).filter(SalesRecord.product_id == p.id, SalesRecord.sale_date >= datetime.utcnow() - timedelta(days=30)).scalar() or 0
        avg = rs / 30
        dte = int(p.current_stock / avg) if avg > 0 else 999
        rq = max(0, int(avg * 30) - p.current_stock)
        preds = StockPrediction.query.filter_by(product_id=p.id).order_by(StockPrediction.predicted_date).limit(14).all()
        pv = [pr.predicted_demand for pr in preds]
        pd = [pr.predicted_date.strftime('%d %b') for pr in preds]
        ac = sum(pr.confidence for pr in preds) / len(preds) if preds else 0
        prediction_data.append({'product': p, 'avg_daily_sales': round(avg, 1), 'days_until_empty': dte,
            'reorder_qty': rq, 'pred_values': pv, 'pred_dates': pd, 'confidence': round(ac * 100, 1),
            'urgency': 'critical' if dte <= 7 else ('warning' if dte <= 14 else 'safe')})
    prediction_data.sort(key=lambda x: x['days_until_empty'])
    return render_template_string(TEMPLATES['predictions.html'], predictions=prediction_data)

@app.route('/top_sellers')
@login_required
def top_sellers():
    period = request.args.get('period', 'daily')
    titles = {'daily': "Today's Top Sellers", 'weekly': "This Week's Top Sellers", 'monthly': "This Month's Top Sellers"}
    title = titles.get(period, titles['daily'])
    if period == 'daily':
        df = datetime.utcnow().date()
        filt = func.date(SalesRecord.sale_date) == df
    elif period == 'weekly':
        filt = func.date(SalesRecord.sale_date) >= datetime.utcnow().date() - timedelta(days=7)
    else:
        filt = func.date(SalesRecord.sale_date) >= datetime.utcnow().date() - timedelta(days=30)
    tq = db.session.query(Product.name, Product.id, func.sum(SalesRecord.quantity).label('tq'),
        func.sum(SalesRecord.total_price).label('tr'), Product.current_stock, Product.minimum_stock
    ).join(SalesRecord).filter(filt).group_by(Product.id).order_by(desc('tq')).limit(15).all()
    ei = db.session.query(Product, func.sum(SalesRecord.quantity).label('ts'), func.avg(SalesRecord.quantity).label('ad')
    ).join(SalesRecord).filter(SalesRecord.sale_date >= datetime.utcnow() - timedelta(days=30)
    ).group_by(Product.id).having(func.avg(SalesRecord.quantity) > 3).order_by(desc('ad')).all()
    ed = []
    for item in ei:
        p, ad = item[0], float(item[2])
        dl = int(p.current_stock / ad) if ad > 0 else 999
        ed.append({'product': p, 'avg_daily_demand': round(ad, 1), 'days_stock_left': dl,
                   'needs_reorder': dl <= 7, 'total_sold_30d': int(item[1])})
    ed.sort(key=lambda x: x['days_stock_left'])
    tn = [t[0][:20] for t in tq]
    tqt = [int(t[2]) for t in tq]
    trv = [float(t[3]) for t in tq]
    return render_template_string(TEMPLATES['top_sellers.html'], title=title, period=period, top_sellers=tq,
        essential_items=ed[:20], top_names=json.dumps(tn), top_quantities=json.dumps(tqt), top_revenues=json.dumps(trv))

@app.route('/place_order/<int:pid>', methods=['POST'])
@login_required
@pharmacist_required
def place_order(pid):
    p = Product.query.get_or_404(pid)
    qty = request.form.get('quantity', type=int, default=1)
    if qty > p.current_stock:
        flash(f'Insufficient stock! Only {p.current_stock} available.', 'danger')
        return redirect(url_for('inventory'))
    db.session.add(Order(user_id=current_user.id, product_id=pid, quantity=qty, status='confirmed'))
    db.session.add(SalesRecord(product_id=pid, quantity=qty, total_price=qty * p.unit_price,
                               buyer_type='hospital' if current_user.organization else 'walk-in'))
    p.current_stock -= qty
    db.session.commit()
    flash(f'Order placed: {qty} {p.unit} of {p.name}!', 'success')
    return redirect(url_for('inventory'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        current_user.full_name = request.form.get('full_name')
        current_user.phone = request.form.get('phone')
        current_user.organization = request.form.get('organization')
        current_user.address = request.form.get('address')
        db.session.commit()
        flash('Profile updated!', 'success')
    return render_template_string(TEMPLATES['profile.html'])

# ============================================================
# BILLING ROUTES (Pharmacist Only)
# ============================================================

@app.route('/billing')
@login_required
@pharmacist_required
def billing():
    products = Product.query.filter(Product.current_stock > 0).order_by(Product.name).all()
    customers = Customer.query.filter_by(added_by=current_user.id).order_by(Customer.name).all()
    monthly_customers = Customer.query.filter_by(added_by=current_user.id, is_monthly=True).all()
    recent_bills = Bill.query.filter_by(created_by=current_user.id).order_by(Bill.bill_date.desc()).limit(10).all()
    products_json = json.dumps([{'id': p.id, 'name': p.name, 'unit_price': p.unit_price,
                                  'current_stock': p.current_stock, 'unit': p.unit} for p in products])
    return render_template_string(TEMPLATES['billing.html'], products=products, customers=customers,
        monthly_customers=monthly_customers, recent_bills=recent_bills, products_json=products_json)

@app.route('/create_bill', methods=['POST'])
@login_required
@pharmacist_required
def create_bill():
    customer_name = request.form.get('customer_name', '').strip()
    customer_phone = request.form.get('customer_phone', '').strip()

    if not customer_name or not customer_phone:
        flash('Customer name and phone are required.', 'danger')
        return redirect(url_for('billing'))

    customer = Customer.query.filter_by(phone=customer_phone, added_by=current_user.id).first()
    if not customer:
        customer = Customer(name=customer_name, phone=customer_phone, added_by=current_user.id)
        db.session.add(customer)
        db.session.flush()
    else:
        customer.name = customer_name

    bill = Bill(customer_id=customer.id, created_by=current_user.id)
    db.session.add(bill)
    db.session.flush()

    item_count = int(request.form.get('item_count', 1))
    total_amount = 0

    for i in range(item_count):
        product_id = request.form.get(f'product_id_{i}', type=int)
        quantity = request.form.get(f'quantity_{i}', type=int, default=0)

        if not product_id or quantity <= 0:
            continue

        product = Product.query.get(product_id)
        if not product:
            continue

        if quantity > product.current_stock:
            flash(f'Insufficient stock for {product.name}! Only {product.current_stock} available.', 'warning')
            quantity = product.current_stock

        if quantity <= 0:
            continue

        item_total = quantity * product.unit_price
        bill_item = BillItem(bill_id=bill.id, product_id=product_id, quantity=quantity,
                             unit_price=product.unit_price, total_price=item_total)
        db.session.add(bill_item)

        db.session.add(SalesRecord(product_id=product_id, quantity=quantity, total_price=item_total,
                                   buyer_type='billing'))

        product.current_stock -= quantity
        total_amount += item_total

    bill.total_amount = total_amount
    db.session.commit()

    flash(f'Bill #{bill.id} created successfully! Total: ₹{total_amount:.2f}', 'success')
    return redirect(url_for('view_bill', bill_id=bill.id))

@app.route('/view_bill/<int:bill_id>')
@login_required
@pharmacist_required
def view_bill(bill_id):
    bill = Bill.query.get_or_404(bill_id)
    if bill.created_by != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('billing'))
    return render_template_string(TEMPLATES['view_bill.html'], bill=bill)

@app.route('/bill_history')
@login_required
@pharmacist_required
def bill_history():
    bills = Bill.query.filter_by(created_by=current_user.id).order_by(Bill.bill_date.desc()).all()
    return render_template_string(TEMPLATES['bill_history.html'], bills=bills)

# ============================================================
# CUSTOMER ROUTES (Pharmacist Only)
# ============================================================

@app.route('/customers')
@login_required
@pharmacist_required
def customers():
    q = Customer.query.filter_by(added_by=current_user.id)
    search = request.args.get('search', '')
    filter_type = request.args.get('filter_type', '')
    if search:
        q = q.filter(db.or_(Customer.name.ilike(f'%{search}%'), Customer.phone.ilike(f'%{search}%')))
    if filter_type == 'monthly':
        q = q.filter_by(is_monthly=True)
    elif filter_type == 'regular':
        q = q.filter_by(is_monthly=False)
    return render_template_string(TEMPLATES['customers.html'], customers=q.order_by(Customer.name).all(),
                                  search=search, filter_type=filter_type)

@app.route('/add_customer', methods=['POST'])
@login_required
@pharmacist_required
def add_customer():
    name = request.form.get('name', '').strip()
    phone = request.form.get('phone', '').strip()
    is_monthly = bool(request.form.get('is_monthly'))

    if not name or not phone:
        flash('Name and phone are required.', 'danger')
        return redirect(url_for('customers'))

    existing = Customer.query.filter_by(phone=phone, added_by=current_user.id).first()
    if existing:
        flash('Customer with this phone number already exists.', 'warning')
        return redirect(url_for('customers'))

    customer = Customer(name=name, phone=phone, is_monthly=is_monthly, added_by=current_user.id)
    db.session.add(customer)
    db.session.commit()
    flash(f'Customer {name} added!', 'success')
    return redirect(url_for('customers'))

@app.route('/customer_detail/<int:cid>')
@login_required
@pharmacist_required
def customer_detail(cid):
    customer = Customer.query.get_or_404(cid)
    if customer.added_by != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('customers'))
    return render_template_string(TEMPLATES['customer_detail.html'], customer=customer)

@app.route('/manage_monthly/<int:cid>')
@login_required
@pharmacist_required
def manage_monthly(cid):
    customer = Customer.query.get_or_404(cid)
    if customer.added_by != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('customers'))
    products = Product.query.order_by(Product.name).all()
    return render_template_string(TEMPLATES['manage_monthly.html'], customer=customer, products=products)

@app.route('/toggle_monthly/<int:cid>', methods=['POST'])
@login_required
@pharmacist_required
def toggle_monthly(cid):
    customer = Customer.query.get_or_404(cid)
    if customer.added_by != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('customers'))
    customer.is_monthly = not customer.is_monthly
    db.session.commit()
    flash(f'Monthly status {"enabled" if customer.is_monthly else "disabled"} for {customer.name}', 'success')
    return redirect(url_for('manage_monthly', cid=cid))

@app.route('/add_monthly_medicine/<int:cid>', methods=['POST'])
@login_required
@pharmacist_required
def add_monthly_medicine(cid):
    customer = Customer.query.get_or_404(cid)
    if customer.added_by != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('customers'))

    product_id = request.form.get('product_id', type=int)
    quantity = request.form.get('quantity', type=int, default=1)

    if not product_id:
        flash('Select a medicine.', 'danger')
        return redirect(url_for('manage_monthly', cid=cid))

    existing = MonthlyMedicine.query.filter_by(customer_id=cid, product_id=product_id).first()
    if existing:
        existing.quantity = quantity
        flash('Monthly medicine quantity updated.', 'success')
    else:
        mm = MonthlyMedicine(customer_id=cid, product_id=product_id, quantity=quantity)
        db.session.add(mm)
        flash('Monthly medicine added.', 'success')

    customer.is_monthly = True
    db.session.commit()
    return redirect(url_for('manage_monthly', cid=cid))

@app.route('/remove_monthly_medicine/<int:mm_id>', methods=['POST'])
@login_required
@pharmacist_required
def remove_monthly_medicine(mm_id):
    mm = MonthlyMedicine.query.get_or_404(mm_id)
    customer = Customer.query.get(mm.customer_id)
    if customer.added_by != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('customers'))
    cid = mm.customer_id
    db.session.delete(mm)
    db.session.commit()
    flash('Monthly medicine removed.', 'success')
    return redirect(url_for('manage_monthly', cid=cid))

@app.route('/fill_monthly/<int:cid>')
@login_required
@pharmacist_required
def fill_monthly(cid):
    customer = Customer.query.get_or_404(cid)
    if customer.added_by != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('customers'))
    return render_template_string(TEMPLATES['fill_monthly.html'], customer=customer)

@app.route('/process_monthly/<int:cid>', methods=['POST'])
@login_required
@pharmacist_required
def process_monthly(cid):
    customer = Customer.query.get_or_404(cid)
    if customer.added_by != current_user.id:
        flash('Unauthorized.', 'danger')
        return redirect(url_for('customers'))

    bill = Bill(customer_id=customer.id, created_by=current_user.id)
    db.session.add(bill)
    db.session.flush()

    total_amount = 0
    for mm in customer.monthly_medicines:
        selected = request.form.get(f'selected_{mm.id}')
        if not selected:
            continue

        qty = request.form.get(f'qty_{mm.id}', type=int, default=mm.quantity)
        product = mm.product

        if qty > product.current_stock:
            flash(f'Insufficient stock for {product.name}! Only {product.current_stock} available.', 'warning')
            qty = product.current_stock

        if qty <= 0:
            continue

        item_total = qty * product.unit_price
        bill_item = BillItem(bill_id=bill.id, product_id=product.id, quantity=qty,
                             unit_price=product.unit_price, total_price=item_total)
        db.session.add(bill_item)

        db.session.add(SalesRecord(product_id=product.id, quantity=qty, total_price=item_total,
                                   buyer_type='monthly'))

        product.current_stock -= qty
        total_amount += item_total

    bill.total_amount = total_amount
    db.session.commit()

    flash(f'Monthly bill #{bill.id} created for {customer.name}! Total: ₹{total_amount:.2f}', 'success')
    return redirect(url_for('view_bill', bill_id=bill.id))

@app.route('/api/monthly_medicines')
@login_required
@pharmacist_required
def api_monthly_medicines():
    name = request.args.get('name', '').strip()
    phone = request.args.get('phone', '').strip()

    customer = None
    if phone:
        customer = Customer.query.filter_by(phone=phone, added_by=current_user.id).first()
    if not customer and name:
        customer = Customer.query.filter_by(name=name, added_by=current_user.id).first()

    if not customer:
        return jsonify({'error': 'Customer not found'})
    if not customer.is_monthly:
        return jsonify({'error': 'Customer is not a monthly customer'})

    medicines = [{'product_id': mm.product_id, 'product_name': mm.product.name,
                  'quantity': mm.quantity, 'unit_price': mm.product.unit_price}
                 for mm in customer.monthly_medicines]
    return jsonify({'customer_name': customer.name, 'phone': customer.phone, 'medicines': medicines})

@app.route('/api/stock_summary')
@login_required
def api_stock_summary():
    data = []
    for c in Category.query.all():
        prods = Product.query.filter_by(category_id=c.id).all()
        data.append({'category': c.name, 'total_items': len(prods),
                     'total_stock': sum(p.current_stock for p in prods),
                     'total_value': round(sum(p.current_stock * p.unit_price for p in prods), 2)})
    return jsonify(data)

# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_database():
    db.create_all()
    if User.query.first():
        return
    print("🏥 Initializing database with sample data...")

    categories = [
        Category(name='Medicines - Tablets', description='Oral tablets and capsules', icon='💊'),
        Category(name='Medicines - Syrups', description='Liquid oral medicines', icon='🧴'),
        Category(name='Medicines - Injections', description='Injectable medicines', icon='💉'),
        Category(name='Surgical Supplies', description='Surgical instruments', icon='🔪'),
        Category(name='Bandages & Dressings', description='Wound care', icon='🩹'),
        Category(name='Diagnostic Equipment', description='Testing tools', icon='🔬'),
        Category(name='PPE & Safety', description='Protective equipment', icon='🧤'),
        Category(name='IV Fluids & Nutrition', description='IV solutions', icon='🧪'),
        Category(name='First Aid', description='Emergency supplies', icon='🏥'),
        Category(name='Baby & Mother Care', description='Maternal/infant care', icon='👶'),
        Category(name='Ayurvedic & Herbal', description='Traditional medicines', icon='🌿'),
        Category(name='Medical Devices', description='Reusable devices', icon='⚕️'),
    ]
    db.session.add_all(categories)
    db.session.commit()

    pharmacist = User(username='pharmacist1', email='pharmacist@pharmacy.com', role='pharmacist',
                    full_name='Dr. Rajesh Kumar', phone='9876543210',
                    organization='Rural Health Pharma Ltd.', address='Village Rampur, Varanasi, UP')
    pharmacist.set_password('pharmacist123')
    doctor = User(username='doctor1', email='doctor@pharmacy.com', role='doctor',
                    full_name='Dr. Sunita Devi', phone='9876543211',
                    organization='Rampur PHC', address='PHC Rampur, Varanasi, UP')
    doctor.set_password('doctor123')
    db.session.add_all([pharmacist, doctor])
    db.session.commit()

    products_data = [
        ('Paracetamol 500mg (Strip)', 1, 17.0, 450, 50, 1000, 'strips', 'Cipla'),
        ('Amoxicillin 250mg (Strip)', 1, 45.0, 200, 30, 500, 'strips', 'Sun Pharma'),
        ('Metformin 500mg (Strip)', 1, 28.0, 300, 40, 800, 'strips', 'Dr. Reddy'),
        ('Amlodipine 5mg (Strip)', 1, 35.0, 150, 25, 400, 'strips', 'Lupin'),
        ('Cetirizine 10mg (Strip)', 1, 22.0, 350, 35, 700, 'strips', 'Mankind'),
        ('Azithromycin 500mg (Strip)', 1, 95.0, 80, 20, 300, 'strips', 'Cipla'),
        ('Omeprazole 20mg (Strip)', 1, 42.0, 250, 30, 600, 'strips', 'Torrent'),
        ('Aspirin 75mg (Strip)', 1, 12.0, 500, 50, 1200, 'strips', 'Bayer'),
        ('Dolo 650mg (Strip)', 1, 30.0, 400, 50, 900, 'strips', 'Micro Labs'),
        ('Pantoprazole 40mg (Strip)', 1, 65.0, 180, 25, 500, 'strips', 'Alkem'),
        ('Cough Syrup Benadryl 100ml', 2, 95.0, 60, 15, 200, 'bottles', 'Johnson'),
        ('ORS Electral (Box/30)', 2, 180.0, 150, 30, 500, 'boxes', 'FDC'),
        ('Digene Antacid Syrup 200ml', 2, 120.0, 80, 20, 250, 'bottles', 'Abbott'),
        ('Dexorange Iron Syrup 200ml', 2, 145.0, 40, 15, 150, 'bottles', 'Franco-Indian'),
        ('Tetanus Toxoid Injection', 3, 45.0, 100, 20, 300, 'vials', 'Serum Institute'),
        ('Insulin Human 40IU', 3, 135.0, 30, 10, 100, 'vials', 'Novo Nordisk'),
        ('Anti-Snake Venom', 3, 950.0, 15, 5, 50, 'vials', 'VINS Bioproducts'),
        ('Rabies Vaccine', 3, 380.0, 25, 8, 80, 'vials', 'GSK'),
        ('Ceftriaxone 1g Inj', 3, 65.0, 80, 15, 250, 'vials', 'Alkem'),
        ('Surgical Gloves Sterile', 4, 18.0, 200, 50, 500, 'pairs', 'Safex'),
        ('Suture Material Silk', 4, 55.0, 80, 20, 200, 'packets', 'Ethicon'),
        ('Surgical Masks 3-Ply (Box/50)', 4, 150.0, 100, 20, 400, 'boxes', '3M India'),
        ('Disposable Syringes 5ml (Box/100)', 4, 280.0, 60, 15, 200, 'boxes', 'Hindustan Syringes'),
        ('Cotton Roll 500g', 5, 185.0, 100, 20, 300, 'rolls', 'Jaycot'),
        ('Band-Aid (Box/100)', 5, 220.0, 50, 15, 150, 'boxes', 'Johnson'),
        ('Crepe Bandage 6"', 5, 45.0, 120, 25, 350, 'pieces', 'Flamingo'),
        ('Sterile Gauze Pads (Pack/25)', 5, 95.0, 80, 20, 250, 'packs', 'Johnson'),
        ('Glucometer Strips (Box/50)', 6, 850.0, 40, 10, 100, 'boxes', 'Roche'),
        ('Pregnancy Test Kit', 6, 55.0, 80, 15, 200, 'kits', 'Mankind'),
        ('BP Monitor Omron', 6, 1850.0, 5, 2, 15, 'units', 'Omron'),
        ('Digital Thermometer', 6, 175.0, 20, 5, 50, 'units', 'Dr. Morepen'),
        ('N95 Mask (Box/20)', 7, 480.0, 50, 10, 200, 'boxes', '3M India'),
        ('Face Shield', 7, 95.0, 50, 10, 150, 'pieces', 'Safex'),
        ('Dettol Sanitizer 500ml', 7, 185.0, 60, 15, 200, 'bottles', 'Reckitt'),
        ('Normal Saline 500ml', 8, 42.0, 100, 20, 300, 'bottles', 'Baxter'),
        ('Dextrose 5% 500ml', 8, 48.0, 80, 15, 250, 'bottles', 'Fresenius'),
        ('Ringer Lactate 500ml', 8, 45.0, 90, 20, 280, 'bottles', 'B.Braun'),
        ('IV Infusion Set', 8, 28.0, 200, 40, 500, 'pieces', 'Romsons'),
        ('Dettol Antiseptic 250ml', 9, 115.0, 70, 15, 200, 'bottles', 'Reckitt'),
        ('Burnol Cream 20g', 9, 72.0, 40, 10, 120, 'tubes', 'Dr. Morepen'),
        ('Ciprofloxacin Eye Drops', 9, 45.0, 90, 20, 250, 'bottles', 'Cipla'),
        ('Pampers Diapers (Pack/20)', 10, 350.0, 30, 10, 80, 'packs', 'P&G'),
        ('Folic Acid 5mg (Strip)', 10, 15.0, 200, 30, 500, 'strips', 'Abbott'),
        ('Shelcal 500mg Calcium', 10, 52.0, 180, 25, 400, 'strips', 'Torrent'),
        ('Dabur Chyawanprash 500g', 11, 245.0, 30, 8, 80, 'jars', 'Dabur'),
        ('Tulsi Drops 30ml', 11, 195.0, 45, 10, 120, 'bottles', 'Organic India'),
        ('Ashwagandha Tabs (60s)', 11, 220.0, 35, 8, 100, 'bottles', 'Himalaya'),
        ('Pulse Oximeter', 12, 1200.0, 10, 3, 25, 'units', 'BPL'),
        ('Nebulizer', 12, 2500.0, 5, 2, 15, 'units', 'Omron'),
        ('Wheelchair Basic', 12, 5500.0, 3, 1, 8, 'units', 'Karma'),
    ]

    for pd in products_data:
        p = Product(name=pd[0], category_id=pd[1], unit_price=pd[2], current_stock=pd[3],
                    minimum_stock=pd[4], maximum_stock=pd[5], unit=pd[6], manufacturer=pd[7],
                    expiry_date=datetime.now().date() + timedelta(days=random.randint(90, 730)),
                    is_prescription=random.choice([True, False]), added_by=pharmacist.id)
        db.session.add(p)
    db.session.commit()

    # Add sample customers
    sample_customers = [
        Customer(name='Ramesh Gupta', phone='9871234567', is_monthly=True, added_by=pharmacist.id),
        Customer(name='Sita Sharma', phone='9872345678', is_monthly=True, added_by=pharmacist.id),
        Customer(name='Mohan Lal', phone='9873456789', is_monthly=False, added_by=pharmacist.id),
        Customer(name='Priya Verma', phone='9874567890', is_monthly=True, added_by=pharmacist.id),
        Customer(name='Anil Kumar', phone='9875678901', is_monthly=False, added_by=pharmacist.id),
    ]
    db.session.add_all(sample_customers)
    db.session.commit()

    # Add monthly medicines for monthly customers
    products_list = Product.query.all()
    for c in Customer.query.filter_by(is_monthly=True).all():
        selected = random.sample(products_list, random.randint(2, 5))
        for p in selected:
            db.session.add(MonthlyMedicine(customer_id=c.id, product_id=p.id, quantity=random.randint(1, 5)))
    db.session.commit()

    print("📊 Generating 90 days of sales data...")
    products = Product.query.all()
    for day_off in range(90, 0, -1):
        sd = datetime.utcnow() - timedelta(days=day_off)
        for p in products:
            if random.random() < 0.7:
                sf = 1.4 if sd.month in [6, 7, 8] else 1.2 if sd.month in [11, 12, 1] else 1.0
                bq = max(1, int(p.maximum_stock * 0.02))
                qty = max(1, int(random.gauss(bq, bq * 0.4) * sf))
                db.session.add(SalesRecord(product_id=p.id, quantity=qty, total_price=qty * p.unit_price,
                    sale_date=sd, buyer_type=random.choice(['walk-in', 'hospital', 'phc', 'wholesale'])))
    db.session.commit()

    print("🧠 Generating predictions...")
    for p in products:
        rs = SalesRecord.query.filter_by(product_id=p.id).filter(SalesRecord.sale_date >= datetime.utcnow() - timedelta(days=30)).all()
        ad = sum(s.quantity for s in rs) / 30 if rs else 5
        for d in range(1, 31):
            db.session.add(StockPrediction(product_id=p.id, predicted_demand=max(1, int(random.gauss(ad, ad * 0.2))),
                predicted_date=datetime.utcnow().date() + timedelta(days=d), confidence=round(random.uniform(0.7, 0.95), 2)))
    db.session.commit()
    print("✅ Database initialized successfully!")

# ============================================================
# RUN APPLICATION
# ============================================================

if __name__ == '__main__':
    with app.app_context():
        init_database()
    print("\n" + "="*60)
    print("🏥 RURAL PHARMACY STOCK PREDICTOR")
    print("="*60)
    print("🌐 Open: http://localhost:5000")
    print("👤 Pharmacist Login: pharmacist1 / pharmacist123")
    print("👤 Doctor Login: doctor1 / doctor123 (Read-Only)")
    print("="*60 + "\n")
    app.run(debug=True, port=5000)