import csv
import io
import os
import sqlite3
from datetime import datetime, date
from functools import wraps
from flask import (
    Flask, render_template_string, request, jsonify, 
    send_from_directory, session, redirect, url_for, Response
)
from werkzeug.security import generate_password_hash, check_password_hash

from datetime import datetime, date, timedelta

app = Flask(__name__)
# Use a static fallback secret key so workers always share the exact same key
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "kiosk_pos_enterprise_multitenant_key_2026_fixed")

app.config.update(
    SESSION_COOKIE_SECURE=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(days=30)
)

BASE_DIR = os.environ.get(
    "RENDER_DISK_PATH",
    os.path.abspath(os.path.dirname(__file__))
)
DB_FILE = "/home/kiosktrack/kiosk-track/shop.db" if os.path.exists("/home/kiosktrack/kiosk-track") else os.path.join(BASE_DIR, "shop.db")


def init_db():
    os.makedirs(os.path.dirname(os.path.abspath(DB_FILE)), exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS shops (
            shop_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            store_logo TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            username TEXT UNIQUE NOT NULL,
            phone TEXT,
            recovery_pin TEXT,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin', 'cashier')),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shop_id) REFERENCES shops(shop_id) ON DELETE CASCADE
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS items (
            item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER DEFAULT 1,
            name TEXT NOT NULL,
            unit_price REAL NOT NULL DEFAULT 0.0,
            current_stock INTEGER NOT NULL DEFAULT 0,
            reorder_level INTEGER NOT NULL DEFAULT 5,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER DEFAULT 1,
            user_id INTEGER DEFAULT 1,
            item_id INTEGER NOT NULL,
            movement_type TEXT NOT NULL CHECK(movement_type IN ('IN', 'OUT', 'ADJUSTMENT')),
            payment_method TEXT CHECK(payment_method IN ('CASH', 'MPESA', 'N/A')),
            quantity INTEGER NOT NULL,
            unit_price REAL NOT NULL DEFAULT 0.0,
            total_amount REAL NOT NULL DEFAULT 0.0,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (item_id) REFERENCES items(item_id) ON DELETE CASCADE
        );
    """)

    cursor.execute("PRAGMA table_info(shops);")
    shop_cols = [row["name"] for row in cursor.fetchall()]
    if "store_logo" not in shop_cols:
        cursor.execute("ALTER TABLE shops ADD COLUMN store_logo TEXT;")

    cursor.execute("PRAGMA table_info(users);")
    user_cols = [row["name"] for row in cursor.fetchall()]
    if "phone" not in user_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN phone TEXT;")
    if "recovery_pin" not in user_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN recovery_pin TEXT;")

    cursor.execute("SELECT COUNT(*) FROM shops;")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO shops (shop_id, name) VALUES (1, 'Kiosk Track Main');")
        cursor.execute("""
            INSERT INTO users (shop_id, username, phone, recovery_pin, password_hash, role)
            VALUES (1, 'admin', '0700000000', '1234', ?, 'admin');
        """, (generate_password_hash("admin123"),))

    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


with app.app_context():
    init_db()


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Unauthorized"}), 401
        if session.get("role") != "admin":
            return jsonify({"error": "Admin access required"}), 403
        return f(*args, **kwargs)
    return decorated_function


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <title>Kiosk Track POS</title>
    <link rel="manifest" href="/manifest.json">
    <link rel="icon" href="/static/app_icon.svg" type="image/svg+xml">
    <link rel="apple-touch-icon" href="/static/app_icon.svg">
    <meta name="theme-color" content="#10b981">
    <meta name="mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="KioskTrack">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: { extend: { fontFamily: { sans: ['"Plus Jakarta Sans"', 'sans-serif'] } } }
        }
    </script>
</head>
<body class="bg-slate-100 dark:bg-slate-950 text-slate-900 dark:text-slate-100 min-h-screen font-sans antialiased transition-colors duration-200 overflow-x-hidden">

    <nav class="sticky top-0 z-40 backdrop-blur-xl bg-white/90 dark:bg-slate-900/90 border-b border-slate-200 dark:border-slate-800 px-3 py-2.5 shadow-sm">
        <div class="max-w-3xl mx-auto flex items-center justify-between gap-2">
            <div class="flex items-center gap-2.5 min-w-0">
                <div class="w-9 h-9 rounded-xl bg-emerald-100 dark:bg-emerald-950/80 border-2 border-emerald-300 dark:border-emerald-800 flex items-center justify-center text-emerald-600 dark:text-emerald-400 font-black text-sm overflow-hidden shadow-sm shrink-0">
                    <span id="navAvatarInitials">{{ session.get('shop_name', 'K')[0]|upper }}</span>
                    <img id="navAvatarImage" src="" alt="Logo" class="w-full h-full object-cover hidden">
                </div>
                <div class="min-w-0">
                    <h1 class="text-sm font-extrabold tracking-tight text-slate-900 dark:text-white truncate leading-tight">{{ session.get('shop_name', 'Kiosk Track') }}</h1>
                    <span class="text-[10px] font-bold text-emerald-600 dark:text-emerald-400 flex items-center gap-1 truncate">
                        <span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse shrink-0"></span> @{{ session.get('username') }}
                    </span>
                </div>
            </div>

            <div class="flex items-center gap-1.5 shrink-0">
                <select id="langSelect" onchange="changeLanguage(this.value)" class="bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-200 text-[11px] font-bold px-1.5 py-1.5 rounded-xl border border-slate-200 dark:border-slate-700 focus:outline-none">
                    <option value="en">🇬🇧 EN</option>
                    <option value="sw">🇰🇪 SW</option>
                    <option value="fr">🇫🇷 FR</option>
                    <option value="es">🇪🇸 ES</option>
                    <option value="ar">🇸🇦 AR</option>
                    <option value="zh">🇨🇳 ZH</option>
                </select>

                <button onclick="toggleTheme()" class="p-2 rounded-xl bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-200 hover:scale-105 active:scale-95 transition shadow-sm">
                    <span id="themeIcon">🌙</span>
                </button>
                {% if session.get('role') == 'admin' %}
                <button onclick="openStaffModal()" title="Manage Staff" class="bg-slate-200 dark:bg-slate-800 hover:bg-slate-300 text-slate-800 dark:text-slate-200 text-[11px] font-bold px-2.5 py-1.5 rounded-xl transition shadow-sm flex items-center gap-1">
                    <span>👥</span> <span data-i18n="staff">Staff</span>
                </button>
                <button onclick="openAddItemModal()" class="bg-indigo-600 hover:bg-indigo-500 active:scale-95 text-white text-[11px] font-bold px-2.5 py-1.5 rounded-xl shadow transition flex items-center gap-1">
                    <span>+</span> <span data-i18n="item">Item</span>
                </button>
                {% endif %}
            </div>
        </div>
    </nav>

    <div id="toast" class="fixed top-4 left-1/2 -translate-x-1/2 z-50 transition-all duration-300 opacity-0 pointer-events-none transform -translate-y-2 max-w-sm w-11/12"></div>

    <main class="max-w-3xl mx-auto px-3 sm:px-4 pt-3 pb-28">

        <section id="screen-counter" class="tab-screen">
            <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-2.5 mb-3 shadow-sm flex flex-wrap items-center justify-between gap-2 text-xs">
                <div class="flex items-center gap-2">
                    <span class="text-[10px] font-bold text-slate-400 uppercase tracking-wider" data-i18n="entry_date">📅 Entry Date:</span>
                    <input type="date" id="activeSaleDate" value="{{ today_date }}" onchange="onSaleDateChange()" 
                           class="bg-slate-100 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl px-2 py-1 text-xs font-bold text-slate-900 dark:text-white">
                </div>
                <div id="dateNotice" class="text-[11px] font-semibold text-emerald-600 dark:text-emerald-400 flex items-center gap-1">
                    <span>🟢</span> <span data-i18n="live_mode">Live Mode (Deducts Stock)</span>
                </div>
            </div>

            <!-- Clickable Stat Cards -->
            <div class="grid grid-cols-3 gap-2 mb-3">
                <div onclick="openDrilldown('CASH')" class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-2.5 shadow-sm cursor-pointer hover:border-emerald-500 transition hover:scale-[1.02] active:scale-95">
                    <span class="block text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-0.5" data-i18n="cash">💵 Cash</span>
                    <div class="text-sm sm:text-base font-black text-emerald-600 dark:text-emerald-400 truncate" id="statCash">KES {{ "{:,.0f}".format(today_cash) }}</div>
                </div>
                <div onclick="openDrilldown('MPESA')" class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-2.5 shadow-sm cursor-pointer hover:border-green-500 transition hover:scale-[1.02] active:scale-95">
                    <span class="block text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-0.5" data-i18n="mpesa">📲 M-Pesa</span>
                    <div class="text-sm sm:text-base font-black text-green-600 dark:text-green-400 truncate" id="statMpesa">KES {{ "{:,.0f}".format(today_mpesa) }}</div>
                </div>
                <div onclick="openDrilldown('TOTAL')" class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-2.5 shadow-sm cursor-pointer hover:border-indigo-500 transition hover:scale-[1.02] active:scale-95">
                    <span class="block text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-0.5" data-i18n="total_sales">📊 Total Sales</span>
                    <div class="text-sm sm:text-base font-black text-slate-900 dark:text-white truncate" id="statTotal">KES {{ "{:,.0f}".format(today_cash + today_mpesa) }}</div>
                </div>
            </div>

            <div class="sticky top-[61px] z-30 mb-3">
                <div class="relative shadow-sm rounded-2xl">
                    <div class="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-slate-400">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
                    </div>
                    <input type="text" id="counterSearch" oninput="filterList('counterSearch', '.counter-card')" placeholder="Search items..." 
                           data-i18n-placeholder="search_placeholder"
                           class="w-full pl-11 pr-10 py-2.5 bg-white dark:bg-slate-900 backdrop-blur-md border border-slate-200 dark:border-slate-800 text-slate-900 dark:text-white rounded-2xl placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-emerald-500 text-sm font-medium shadow-sm">
                </div>
            </div>

            <div class="space-y-2.5" id="counterList">
                {% for item in items %}
                <div class="counter-card bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-3 hover:border-slate-300 dark:hover:border-slate-700 transition relative shadow-sm" 
                     id="item-card-{{ item['item_id'] }}" data-name="{{ item['name'] }}">
                    <div class="flex items-start justify-between gap-2 mb-2">
                        <div>
                            <h2 class="font-bold text-slate-900 dark:text-white text-sm leading-snug">{{ item['name'] }}</h2>
                            <span class="text-xs font-semibold text-emerald-600 dark:text-emerald-400">KES {{ "{:,.1f}".format(item['unit_price']) }}</span>
                        </div>
                        <div class="flex items-center gap-1.5">
                            <span id="badge-{{ item['item_id'] }}" class="px-2.5 py-0.5 rounded-full text-[11px] font-bold {% if item['current_stock'] <= 0 %}bg-rose-100 dark:bg-rose-950 text-rose-700 dark:text-rose-400 border border-rose-300 dark:border-rose-800{% elif item['current_stock'] <= item['reorder_level'] %}bg-amber-100 dark:bg-amber-950 text-amber-700 dark:text-amber-300 border border-amber-300 dark:border-amber-800{% else %}bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-200 border border-slate-200 dark:border-slate-700{% endif %}">
                                Stock: <span id="stock-val-{{ item['item_id'] }}">{{ item['current_stock'] }}</span>
                            </span>
                        </div>
                    </div>

                    <div class="flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-slate-100 dark:border-slate-800">
                        <div class="flex items-center gap-1.5">
                            <div class="flex items-center bg-slate-100 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl px-1">
                                <button onclick="adjustQty('qty-{{ item['item_id'] }}', -1)" class="w-6 h-6 text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white font-bold text-sm">-</button>
                                <input type="number" id="qty-{{ item['item_id'] }}" value="1" min="1" 
                                       class="w-8 bg-transparent text-center text-xs font-bold text-slate-900 dark:text-white focus:outline-none">
                                <button onclick="adjustQty('qty-{{ item['item_id'] }}', 1)" class="w-6 h-6 text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white font-bold text-sm">+</button>
                            </div>

                            <button onclick="makeSale({{ item['item_id'] }}, 'CASH')" 
                                    class="bg-emerald-600 hover:bg-emerald-500 text-white active:scale-95 hover:scale-105 px-2.5 py-1.5 rounded-xl text-xs font-bold shadow transition flex items-center gap-1">
                                <span>💵</span> <span data-i18n="btn_cash">Cash</span>
                            </button>
                            <button onclick="makeSale({{ item['item_id'] }}, 'MPESA')" 
                                    class="bg-green-600 hover:bg-green-500 text-white active:scale-95 hover:scale-105 px-2.5 py-1.5 rounded-xl text-xs font-bold shadow transition flex items-center gap-1">
                                <span>📲</span> <span data-i18n="btn_mpesa">M-Pesa</span>
                            </button>
                            <button onclick="openSplitModal({{ item['item_id'] }}, '{{ item['name'] }}', {{ item['unit_price'] }})" 
                                    class="bg-amber-100 dark:bg-amber-950/80 hover:bg-amber-200 dark:hover:bg-amber-900 border border-amber-300 dark:border-amber-800 text-amber-800 dark:text-amber-300 active:scale-95 hover:scale-105 px-2 py-1.5 rounded-xl text-xs font-bold transition flex items-center gap-1">
                                <span>⚡</span> <span data-i18n="btn_split">Split</span>
                            </button>
                        </div>

                        {% if session.get('role') == 'admin' %}
                        <div class="flex items-center gap-1.5">
                            <button onclick="reverseSale({{ item['item_id'] }})" 
                                    title="Undo sale"
                                    class="bg-rose-50 dark:bg-rose-950/70 hover:bg-rose-100 text-rose-700 dark:text-rose-300 border border-rose-200 dark:border-rose-800 active:scale-95 hover:scale-105 px-2 py-1.5 rounded-xl text-xs font-bold transition flex items-center gap-1">
                                <span>↩</span> <span data-i18n="btn_return">Return</span>
                            </button>
                            <input type="number" id="restock-qty-{{ item['item_id'] }}" placeholder="+Qty" min="1" 
                                   class="w-12 px-2 py-1.5 bg-slate-100 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl text-center text-xs text-slate-900 dark:text-white focus:outline-none">
                            <button onclick="makeRestock({{ item['item_id'] }})" 
                                    class="bg-slate-200 dark:bg-slate-800 hover:bg-slate-300 hover:scale-105 text-sky-700 dark:text-sky-300 active:scale-95 px-2.5 py-1.5 rounded-xl text-xs font-bold border border-slate-300 dark:border-slate-700 transition flex items-center gap-1">
                                <span>📦</span> <span data-i18n="btn_in">+ In</span>
                            </button>
                        </div>
                        {% endif %}
                    </div>
                </div>
                {% endfor %}
            </div>
        </section>

        {% if session.get('role') == 'admin' %}
        <section id="screen-reports" class="tab-screen hidden">
            <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-4 mb-4 shadow-sm space-y-4">
                <div class="flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <h2 class="text-base font-bold text-slate-900 dark:text-white flex items-center gap-1.5">
                            <span>📊</span> <span data-i18n="reports_title">Sales & Staff Shifts</span>
                        </h2>
                        <p class="text-xs text-slate-500" data-i18n="reports_subtitle">Historical performance and staff handovers</p>
                    </div>
                    <div class="flex items-center gap-1 bg-slate-100 dark:bg-slate-950 p-1 rounded-xl border border-slate-200 dark:border-slate-800 text-xs">
                        <button onclick="setReportRange('today', this)" class="report-range-btn px-2.5 py-1 rounded-lg font-bold bg-emerald-600 text-white hover:opacity-90 transition" data-i18n="today">Today</button>
                        <button onclick="setReportRange('yesterday', this)" class="report-range-btn px-2.5 py-1 rounded-lg text-slate-500 dark:text-slate-400 hover:text-white transition" data-i18n="yesterday">Yesterday</button>
                        <button onclick="setReportRange('week', this)" class="report-range-btn px-2.5 py-1 rounded-lg text-slate-500 dark:text-slate-400 hover:text-white transition" data-i18n="week">7 Days</button>
                        <button onclick="setReportRange('month', this)" class="report-range-btn px-2.5 py-1 rounded-lg text-slate-500 dark:text-slate-400 hover:text-white transition" data-i18n="month">30 Days</button>
                    </div>
                </div>

                <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    <div onclick="openDrilldown('TOTAL')" class="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-xl border border-slate-200 dark:border-slate-800 cursor-pointer hover:border-indigo-500 transition hover:scale-[1.02]">
                        <span class="text-[10px] text-slate-500 uppercase font-bold flex items-center gap-1"><span>💰</span> <span data-i18n="revenue">Revenue</span></span>
                        <div id="repTotalRev" class="text-base font-black text-slate-900 dark:text-white">KES 0</div>
                    </div>
                    <div onclick="openUnitsSoldModal()" class="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-xl border border-slate-200 dark:border-slate-800 cursor-pointer hover:border-emerald-500 transition hover:scale-[1.02]">
                        <span class="text-[10px] text-slate-500 uppercase font-bold flex items-center gap-1"><span>📦</span> <span data-i18n="units_sold">Units Sold</span></span>
                        <div id="repTotalUnits" class="text-base font-black text-emerald-600 dark:text-emerald-400">0 pcs</div>
                    </div>
                    <div onclick="openDrilldown('CASH')" class="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-xl border border-slate-200 dark:border-slate-800 cursor-pointer hover:border-emerald-500 transition hover:scale-[1.02]">
                        <span class="text-[10px] text-slate-500 uppercase font-bold flex items-center gap-1"><span>💵</span> <span data-i18n="cash">Cash</span></span>
                        <div id="repCash" class="text-base font-black text-emerald-500">KES 0</div>
                    </div>
                    <div onclick="openDrilldown('MPESA')" class="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-xl border border-slate-200 dark:border-slate-800 cursor-pointer hover:border-green-500 transition hover:scale-[1.02]">
                        <span class="text-[10px] text-slate-500 uppercase font-bold flex items-center gap-1"><span>📲</span> <span data-i18n="mpesa">M-Pesa</span></span>
                        <div id="repMpesa" class="text-base font-black text-green-500">KES 0</div>
                    </div>
                </div>

                <div onclick="openClosingStockModal()" class="bg-slate-50 dark:bg-slate-950 p-3 rounded-xl border border-slate-200 dark:border-slate-800 flex items-center justify-between cursor-pointer hover:border-sky-500 transition hover:scale-[1.01]">
                    <div class="flex items-center gap-2">
                        <span class="text-lg">📦</span>
                        <div>
                            <div class="font-bold text-xs text-slate-900 dark:text-white" data-i18n="closing_stock_card">Remaining Closing Stock</div>
                            <div class="text-[10px] text-slate-500" data-i18n="closing_stock_sub">Click to inspect remaining items & valuation</div>
                        </div>
                    </div>
                    <span class="text-xs font-bold text-sky-600 dark:text-sky-400 bg-sky-50 dark:bg-sky-950/80 px-2.5 py-1 rounded-lg border border-sky-200 dark:border-sky-800">View List →</span>
                </div>

                <div class="bg-slate-50 dark:bg-slate-950 p-3 rounded-xl border border-slate-200 dark:border-slate-800 space-y-2.5 text-xs">
                    <span class="font-bold text-slate-400 uppercase text-[10px]" data-i18n="raw_export">📥 Flexible Range CSV Export:</span>
                    <div class="grid grid-cols-2 gap-2">
                        <div>
                            <label class="block text-[10px] text-slate-400 mb-1" data-i18n="start_date">Start Date</label>
                            <input type="date" id="exportStart" class="w-full bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl px-2 py-1.5 text-xs text-slate-900 dark:text-white">
                        </div>
                        <div>
                            <label class="block text-[10px] text-slate-400 mb-1" data-i18n="end_date">End Date</label>
                            <input type="date" id="exportEnd" class="w-full bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl px-2 py-1.5 text-xs text-slate-900 dark:text-white">
                        </div>
                    </div>
                    <div class="flex items-center gap-2 pt-1">
                        <button onclick="downloadRangeCsv()" class="flex-1 bg-sky-600 hover:bg-sky-500 text-white font-bold py-2 rounded-xl transition shadow-sm hover:scale-[1.02]">Download Range CSV</button>
                        <a href="/api/reports/export-csv?range=all" class="bg-slate-200 dark:bg-slate-800 hover:bg-slate-300 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-200 font-bold px-3 py-2 rounded-xl transition text-center" data-i18n="csv_all">All-Time CSV</a>
                    </div>
                </div>

                <div class="bg-slate-50 dark:bg-slate-950 p-4 rounded-2xl border border-slate-200 dark:border-slate-800">
                    <h3 class="text-xs font-bold text-slate-700 dark:text-slate-300 uppercase tracking-wider mb-3 flex items-center gap-1.5">
                        <span>👥</span> <span data-i18n="staff_breakdown">Staff Shift Breakdown</span>
                    </h3>
                    <div class="overflow-x-auto">
                        <table class="w-full text-xs text-left">
                            <thead class="text-[10px] uppercase text-slate-400 border-b border-slate-200 dark:border-slate-800">
                                <tr>
                                    <th class="py-2" data-i18n="th_staff">Staff</th>
                                    <th class="py-2" data-i18n="th_role">Role</th>
                                    <th class="py-2" data-i18n="th_sales">Sales</th>
                                    <th class="py-2" data-i18n="th_cash">Cash</th>
                                    <th class="py-2" data-i18n="th_mpesa">M-Pesa</th>
                                    <th class="py-2 font-bold" data-i18n="th_total">Total</th>
                                </tr>
                            </thead>
                            <tbody id="staffTableBody" class="divide-y divide-slate-100 dark:divide-slate-800">
                                <tr><td colspan="6" class="py-3 text-center text-slate-400">Loading staff shift details...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>

            </div>
        </section>

        <section id="screen-audit" class="tab-screen hidden">
            <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-4 mb-4 shadow-sm">
                <div class="mb-4">
                    <h2 class="text-base font-bold text-slate-900 dark:text-white flex items-center gap-1.5">
                        <span>📋</span> <span data-i18n="stock_calibration">Physical Stock Calibration</span>
                    </h2>
                    <p class="text-xs text-slate-500" data-i18n="stock_subtitle">Setting counts here overrides your shelf total directly</p>
                </div>
                <div class="divide-y divide-slate-100 dark:divide-slate-800 max-h-[500px] overflow-y-auto pr-1">
                    {% for item in items %}
                    <div class="audit-row py-3 flex items-center justify-between gap-2">
                        <div class="flex items-center gap-3">
                            <div class="w-9 h-9 rounded-xl bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 flex items-center justify-center text-base shadow-sm shrink-0">
                                🏷️
                            </div>
                            <div>
                                <div class="font-bold text-slate-900 dark:text-white text-xs leading-snug">{{ item['name'] }}</div>
                                <span class="text-[11px] text-slate-500"><span data-i18n="current_count">Current Count</span>: <b id="audit-sys-{{ item['item_id'] }}">{{ item['current_stock'] }}</b></span>
                            </div>
                        </div>
                        <div class="flex items-center gap-1.5">
                            <input type="number" id="counted-{{ item['item_id'] }}" placeholder="Counted" 
                                   class="w-16 px-2 py-1.5 bg-slate-100 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl text-center text-xs font-bold text-slate-900 dark:text-white">
                            <button onclick="updateStockTake({{ item['item_id'] }})" 
                                    class="bg-sky-600 hover:bg-sky-500 hover:scale-105 active:scale-95 text-white font-bold text-xs px-2.5 py-1.5 rounded-xl flex items-center gap-1 transition">
                                <span>✓</span> <span data-i18n="btn_set">Set</span>
                            </button>
                        </div>
                    </div>
                    {% endfor %}
                </div>
            </div>
        </section>
        {% endif %}

        <section id="screen-profile" class="tab-screen hidden">
            <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-5 mb-6 shadow-sm space-y-6">
                
                <div class="flex items-center gap-4 pb-4 border-b border-slate-100 dark:border-slate-800">
                    <div class="relative group cursor-pointer" onclick="document.getElementById('avatarInput').click()">
                        <div class="w-16 h-16 rounded-2xl bg-emerald-100 dark:bg-emerald-950/80 border-2 border-emerald-300 dark:border-emerald-800 flex items-center justify-center text-emerald-600 dark:text-emerald-400 font-black text-2xl overflow-hidden shadow-inner">
                            <span id="avatarInitials">{{ session.get('shop_name', 'K')[0]|upper }}</span>
                            <img id="avatarImage" src="" alt="Logo" class="w-full h-full object-cover hidden">
                        </div>
                        <div class="absolute inset-0 bg-black/40 rounded-2xl flex items-center justify-center opacity-0 group-hover:opacity-100 transition text-white text-[10px] font-bold">
                            Edit
                        </div>
                    </div>
                    <div>
                        <h2 class="text-base font-black text-slate-900 dark:text-white">{{ session.get('shop_name') }}</h2>
                        <p class="text-xs text-emerald-600 dark:text-emerald-400 font-bold">@{{ session.get('username') }} • <span class="uppercase text-[10px]">{{ session.get('role') }}</span></p>
                        <button onclick="document.getElementById('avatarInput').click()" class="text-[11px] font-bold text-indigo-500 hover:underline mt-1 inline-block">Change Store Logo</button>
                    </div>
                </div>

                <input type="file" id="avatarInput" accept="image/*" class="hidden" onchange="handleAvatarUpload(event)">

                <div class="space-y-4 text-xs">
                    <div class="bg-slate-50 dark:bg-slate-950 p-4 rounded-2xl border border-slate-200 dark:border-slate-800 space-y-3">
                        <div class="font-bold text-slate-900 dark:text-white flex items-center gap-1.5 uppercase text-[10px] tracking-wider text-slate-400">
                            <span>⚙️</span> <span data-i18n="settings_header">App Settings & Preferences</span>
                        </div>
                        
                        <div class="flex items-center justify-between py-1.5 border-b border-slate-200 dark:border-slate-800">
                            <span class="font-semibold text-slate-700 dark:text-slate-300" data-i18n="theme_pref">Theme Mode</span>
                            <button onclick="toggleTheme()" class="bg-slate-200 dark:bg-slate-800 hover:bg-slate-300 dark:hover:bg-slate-700 text-slate-800 dark:text-slate-200 font-bold px-3 py-1.5 rounded-xl transition flex items-center gap-1">
                                <span id="themeToggleLabel">Toggle Light/Dark</span>
                            </button>
                        </div>

                        <div class="flex items-center justify-between py-1">
                            <span class="font-semibold text-slate-700 dark:text-slate-300" data-i18n="app_version">App Version</span>
                            <span class="font-mono font-bold text-slate-600 dark:text-slate-400">v2.8</span>
                        </div>
                    </div>

                    <button onclick="triggerNativeInstall()" class="w-full bg-gradient-to-r from-emerald-500 to-teal-500 hover:from-emerald-400 hover:to-teal-400 text-slate-950 font-extrabold py-3 px-4 rounded-xl transition flex items-center justify-center gap-2 shadow-md hover:scale-[1.01]">
                        <span>📲</span> <span data-i18n="install_app_btn">Install App on Phone</span>
                    </button>

                    <div class="bg-slate-50 dark:bg-slate-950 p-4 rounded-2xl border border-slate-200 dark:border-slate-800 space-y-3">
                        <div class="font-bold text-slate-900 dark:text-white flex items-center gap-1.5">
                            <span>🔗</span> <span data-i18n="share_store">Share Store Link</span>
                        </div>
                        <p class="text-[11px] text-slate-500 leading-relaxed" data-i18n="share_desc">Copy your store link or share instantly with customers and friends via WhatsApp.</p>
                        
                        <div class="flex items-center gap-2">
                            <input type="text" id="storeLinkInput" readonly value="https://kiosktrack.pythonanywhere.com" 
                                   class="w-full bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl px-3 py-2 text-slate-700 dark:text-slate-300 font-mono text-xs focus:outline-none">
                            <button onclick="copyStoreLink()" class="bg-slate-200 dark:bg-slate-800 hover:bg-slate-300 text-slate-800 dark:text-slate-200 font-bold px-3 py-2 rounded-xl transition shrink-0 flex items-center gap-1 hover:scale-105">
                                <span>📋</span> <span data-i18n="btn_copy">Copy</span>
                            </button>
                        </div>

                        <a href="https://api.whatsapp.com/send?text=Hey!%20Check%20out%20our%20store%20on%20Kiosk%20Track:%20https://kiosktrack.pythonanywhere.com" 
                           target="_blank" 
                           class="w-full bg-green-600 hover:bg-green-500 text-white font-bold py-2.5 px-3 rounded-xl transition flex items-center justify-center gap-1.5 shadow-sm hover:scale-[1.01]">
                            <span>💬</span> <span data-i18n="btn_whatsapp">Share via WhatsApp</span>
                        </a>
                    </div>

                    <div class="flex items-center justify-between bg-slate-50 dark:bg-slate-950 p-3 rounded-xl border border-slate-200 dark:border-slate-800">
                        <span class="text-[11px] font-bold text-slate-500" data-i18n="store_logo">Store Logo / Picture</span>
                        <button onclick="removeAvatar()" class="bg-rose-50 dark:bg-rose-950/70 hover:bg-rose-100 text-rose-600 font-bold px-3 py-1.5 rounded-lg transition border border-rose-200 dark:border-rose-800 hover:scale-105" data-i18n="btn_remove">Remove Logo</button>
                    </div>
                </div>

                <div class="pt-4 border-t border-slate-100 dark:border-slate-800 space-y-4">
                    <a href="/logout" class="w-full bg-rose-600 hover:bg-rose-500 text-white font-bold py-3 rounded-xl transition text-center shadow-lg flex items-center justify-center gap-2 hover:scale-[1.01]">
                        <span data-i18n="btn_logout">Log out</span>
                    </a>

                    <div class="text-center pt-2 space-y-0.5">
                        <p class="text-[10px] text-slate-400 font-semibold">© 2026 Kiosk Track. All rights reserved.</p>
                        <p class="text-[9px] text-emerald-600 dark:text-emerald-400 font-bold" data-i18n="footer_empower">Empowering Businesses WorldWide.</p>
                    </div>
                </div>

            </div>
        </section>

        <!-- Drilldown Modal -->
        <div id="drilldownModal" class="hidden fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
            <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-3xl w-full max-w-lg p-5 shadow-2xl space-y-4 max-h-[90vh] flex flex-col">
                <div class="flex items-center justify-between border-b border-slate-200 dark:border-slate-800 pb-3">
                    <div>
                        <h3 class="text-sm font-black text-slate-900 dark:text-white" id="drilldownTitle">Sales Details</h3>
                        <span class="text-xs text-emerald-600 font-bold" id="drilldownSubTotal">Total: KES 0</span>
                    </div>
                    <button onclick="closeDrilldownModal()" class="text-slate-400 hover:text-white text-xl font-bold">&times;</button>
                </div>
                
                <div class="flex items-center gap-2 bg-slate-50 dark:bg-slate-950 p-2.5 rounded-xl border border-slate-200 dark:border-slate-800 text-xs">
                    <span class="font-bold text-slate-400 uppercase text-[10px]">Filter Date:</span>
                    <input type="date" id="drilldownDate" onchange="fetchDrilldownData()" class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg px-2 py-1 font-bold">
                    <button onclick="resetDrilldownDate()" class="text-[10px] font-bold text-indigo-500 hover:underline ml-auto">All Range</button>
                </div>

                <div class="overflow-y-auto flex-1 pr-1 max-h-72">
                    <table class="w-full text-xs text-left">
                        <thead class="text-[10px] uppercase text-slate-400 border-b border-slate-200 dark:border-slate-800">
                            <tr>
                                <th class="py-2">Time</th>
                                <th class="py-2">Product</th>
                                <th class="py-2">Qty</th>
                                <th class="py-2">Method</th>
                                <th class="py-2 font-bold">Amount</th>
                            </tr>
                        </thead>
                        <tbody id="drilldownTableBody" class="divide-y divide-slate-100 dark:divide-slate-800">
                            <tr><td colspan="5" class="py-4 text-center text-slate-400">Loading transactions...</td></tr>
                        </tbody>
                    </table>
                </div>

                <div class="flex justify-between items-center pt-3 border-t border-slate-200 dark:border-slate-800">
                    <button onclick="downloadDrilldownCsv()" class="bg-sky-600 hover:bg-sky-500 text-white font-bold text-xs px-3.5 py-2 rounded-xl transition flex items-center gap-1">
                        <span>📥</span> Download CSV
                    </button>
                    <button onclick="closeDrilldownModal()" class="bg-slate-200 dark:bg-slate-800 hover:bg-slate-300 text-slate-700 dark:text-slate-200 font-bold text-xs px-4 py-2 rounded-xl transition">
                        Close
                    </button>
                </div>
            </div>
        </div>

        <!-- Units Sold Modal -->
        <div id="unitsSoldModal" class="hidden fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
            <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-3xl w-full max-w-md p-5 shadow-2xl space-y-4 max-h-[90vh] flex flex-col">
                <div class="flex items-center justify-between border-b border-slate-200 dark:border-slate-800 pb-3">
                    <h3 class="text-sm font-black text-slate-900 dark:text-white">📦 Units Sold Breakdown</h3>
                    <button onclick="closeUnitsSoldModal()" class="text-slate-400 hover:text-white text-xl font-bold">&times;</button>
                </div>
                <div class="overflow-y-auto flex-1 pr-1 max-h-72">
                    <table class="w-full text-xs text-left">
                        <thead class="text-[10px] uppercase text-slate-400 border-b border-slate-200 dark:border-slate-800">
                            <tr>
                                <th class="py-2">Product Name</th>
                                <th class="py-2">Units Sold</th>
                                <th class="py-2 font-bold">Total Sales</th>
                            </tr>
                        </thead>
                        <tbody id="unitsSoldTableBody" class="divide-y divide-slate-100 dark:divide-slate-800">
                            <tr><td colspan="3" class="py-4 text-center text-slate-400">Loading units sold...</td></tr>
                        </tbody>
                    </table>
                </div>
                <div class="flex justify-end pt-3 border-t border-slate-200 dark:border-slate-800">
                    <button onclick="closeUnitsSoldModal()" class="bg-slate-200 dark:bg-slate-800 hover:bg-slate-300 text-slate-700 dark:text-slate-200 font-bold text-xs px-4 py-2 rounded-xl transition">Close</button>
                </div>
            </div>
        </div>

        <!-- Closing Stock Modal -->
        <div id="closingStockModal" class="hidden fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
            <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-3xl w-full max-w-md p-5 shadow-2xl space-y-4 max-h-[90vh] flex flex-col">
                <div class="flex items-center justify-between border-b border-slate-200 dark:border-slate-800 pb-3">
                    <h3 class="text-sm font-black text-slate-900 dark:text-white">📦 Remaining Closing Stock</h3>
                    <button onclick="closeClosingStockModal()" class="text-slate-400 hover:text-white text-xl font-bold">&times;</button>
                </div>
                <div class="overflow-y-auto flex-1 pr-1 max-h-72">
                    <table class="w-full text-xs text-left">
                        <thead class="text-[10px] uppercase text-slate-400 border-b border-slate-200 dark:border-slate-800">
                            <tr>
                                <th class="py-2">Product Name</th>
                                <th class="py-2">In Stock</th>
                                <th class="py-2 font-bold">Valuation (KES)</th>
                            </tr>
                        </thead>
                        <tbody id="closingStockTableBody" class="divide-y divide-slate-100 dark:divide-slate-800">
                            <tr><td colspan="3" class="py-4 text-center text-slate-400">Loading closing stock...</td></tr>
                        </tbody>
                    </table>
                </div>
                <div class="flex justify-between items-center pt-3 border-t border-slate-200 dark:border-slate-800">
                    <button onclick="downloadClosingStockCsv()" class="bg-sky-600 hover:bg-sky-500 text-white font-bold text-xs px-3.5 py-2 rounded-xl transition">📥 Download CSV</button>
                    <button onclick="closeClosingStockModal()" class="bg-slate-200 dark:bg-slate-800 hover:bg-slate-300 text-slate-700 dark:text-slate-200 font-bold text-xs px-4 py-2 rounded-xl transition">Close</button>
                </div>
            </div>
        </div>

        <!-- Staff Modal -->
        <div id="staffModal" class="hidden fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
            <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-3xl w-full max-w-md p-6 shadow-2xl space-y-4">
                <div class="flex items-center justify-between border-b border-slate-200 dark:border-slate-800 pb-3">
                    <h3 class="text-sm font-bold text-slate-900 dark:text-white" data-i18n="manage_cashiers">Manage Cashiers</h3>
                    <button onclick="closeStaffModal()" class="text-slate-400 text-lg font-bold">&times;</button>
                </div>
                <div class="space-y-2 max-h-48 overflow-y-auto pr-1">
                    <h4 class="text-[10px] font-bold uppercase text-slate-400" data-i18n="current_team">Current Team</h4>
                    <div id="existingStaffList" class="divide-y divide-slate-100 dark:divide-slate-800 text-xs"></div>
                </div>
                <div class="pt-3 border-t border-slate-200 dark:border-slate-800 space-y-2 text-xs">
                    <h4 class="text-[10px] font-bold uppercase text-slate-400" data-i18n="create_cashier">Create New Cashier</h4>
                    <div>
                        <label class="block font-semibold mb-1" data-i18n="username">Username</label>
                        <input type="text" id="staffUsername" class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl px-3 py-2 text-slate-900 dark:text-white">
                    </div>
                    <div>
                        <label class="block font-semibold mb-1" data-i18n="password_pin">Password / PIN</label>
                        <input type="password" id="staffPassword" class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl px-3 py-2 text-slate-900 dark:text-white">
                    </div>
                </div>
                <div class="flex justify-end gap-2 pt-2 border-t border-slate-200 dark:border-slate-800">
                    <button onclick="closeStaffModal()" class="px-3 py-1.5 text-xs text-slate-500 font-bold" data-i18n="btn_close">Close</button>
                    <button onclick="submitNewStaff()" class="bg-indigo-600 text-white font-bold text-xs px-4 py-2 rounded-xl" data-i18n="btn_create">Create Cashier</button>
                </div>
            </div>
        </div>

        <!-- Add Item Modal -->
        <div id="addItemModal" class="hidden fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
            <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-3xl w-full max-w-sm p-6 shadow-2xl space-y-4">
                <div class="flex items-center justify-between border-b border-slate-200 dark:border-slate-800 pb-3">
                    <h3 class="text-sm font-bold text-slate-900 dark:text-white" data-i18n="add_product">Add New Product</h3>
                    <button onclick="closeAddItemModal()" class="text-slate-400 text-lg font-bold">&times;</button>
                </div>
                <div class="space-y-3 text-xs">
                    <div>
                        <label class="block font-semibold mb-1" data-i18n="product_name">Product Name</label>
                        <input type="text" id="newItemName" class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl px-3 py-2 text-slate-900 dark:text-white">
                    </div>
                    <div class="grid grid-cols-2 gap-2">
                        <div>
                            <label class="block font-semibold mb-1" data-i18n="selling_price">Selling Price (KES)</label>
                            <input type="number" id="newItemPrice" step="0.5" class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl px-3 py-2 text-slate-900 dark:text-white">
                        </div>
                        <div>
                            <label class="block font-semibold mb-1" data-i18n="initial_stock">Initial Stock</label>
                            <input type="number" id="newItemStock" class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl px-3 py-2 text-slate-900 dark:text-white">
                        </div>
                    </div>
                </div>
                <div class="flex justify-end gap-2 pt-2 border-t border-slate-200 dark:border-slate-800">
                    <button onclick="closeAddItemModal()" class="px-3 py-1.5 text-xs text-slate-500 font-bold" data-i18n="btn_cancel">Cancel</button>
                    <button onclick="submitNewItem()" class="bg-indigo-600 text-white font-bold text-xs px-4 py-2 rounded-xl" data-i18n="btn_save">Save</button>
                </div>
            </div>
        </div>

        <!-- Split Modal -->
        <div id="splitModal" class="hidden fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
            <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-3xl w-full max-w-sm p-6 shadow-2xl space-y-4">
                <div class="flex items-center justify-between border-b border-slate-200 dark:border-slate-800 pb-3">
                    <div>
                        <h3 class="text-sm font-bold text-slate-900 dark:text-white" id="splitItemName">Item Name</h3>
                        <span class="text-xs text-emerald-600 font-bold" id="splitTotalDisplay">Total: KES 0</span>
                    </div>
                    <button onclick="closeSplitModal()" class="text-slate-400 text-lg font-bold">&times;</button>
                </div>
                <div class="space-y-3 text-xs">
                    <div>
                        <label class="block font-semibold mb-1" data-i18n="cash_kes">Cash (KES)</label>
                        <input type="number" id="splitCashInput" oninput="autoCalculateMpesa()" class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl px-3 py-2 text-slate-900 dark:text-white">
                    </div>
                    <div>
                        <label class="block font-semibold mb-1" data-i18n="mpesa_kes">M-Pesa (KES)</label>
                        <input type="number" id="splitMpesaInput" class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl px-3 py-2 text-slate-900 dark:text-white">
                    </div>
                </div>
                <div class="flex justify-end gap-2 pt-2 border-t border-slate-200 dark:border-slate-800">
                    <button onclick="closeSplitModal()" class="px-3 py-1.5 text-xs text-slate-500 font-bold" data-i18n="btn_cancel">Cancel</button>
                    <button onclick="submitSplitSale()" class="bg-emerald-600 text-white font-bold text-xs px-4 py-2 rounded-xl" data-i18n="btn_complete">Complete Sale</button>
                </div>
            </div>
        </div>

    </main>

    <nav class="fixed bottom-0 left-0 right-0 z-40 bg-white/95 dark:bg-slate-900/95 backdrop-blur-xl border-t border-slate-200 dark:border-slate-800 pb-[env(safe-area-inset-bottom)] shadow-lg">
        <div class="max-w-md mx-auto grid {% if session.get('role') == 'admin' %}grid-cols-4{% else %}grid-cols-2{% endif %} h-16">
            <button onclick="switchTab('counter', this)" class="nav-tab flex flex-col items-center justify-center gap-1 text-emerald-600 dark:text-emerald-400">
                <div class="w-10 h-7 rounded-full flex items-center justify-center bg-emerald-100 dark:bg-emerald-950/80 border border-emerald-300 dark:border-emerald-800 shadow-sm tab-indicator">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M16 11V7a4 4 0 00-8 0v4M5 9h14l1 12H4L5 9z"/></svg>
                </div>
                <span class="text-[11px] font-bold tracking-tight" data-i18n="nav_counter">Counter</span>
            </button>
            {% if session.get('role') == 'admin' %}
            <button onclick="switchTab('reports', this)" class="nav-tab flex flex-col items-center justify-center gap-1 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 transition">
                <div class="w-10 h-7 rounded-full flex items-center justify-center bg-transparent border border-transparent tab-indicator">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>
                </div>
                <span class="text-[11px] font-bold tracking-tight" data-i18n="nav_reports">Reports</span>
            </button>
            <button onclick="switchTab('audit', this)" class="nav-tab flex flex-col items-center justify-center gap-1 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 transition">
                <div class="w-10 h-7 rounded-full flex items-center justify-center bg-transparent border border-transparent tab-indicator">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"/></svg>
                </div>
                <span class="text-[11px] font-bold tracking-tight" data-i18n="nav_stocktake">Stock Take</span>
            </button>
            {% endif %}
            <button onclick="switchTab('profile', this)" class="nav-tab flex flex-col items-center justify-center gap-1 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 transition">
                <div class="w-10 h-7 rounded-full flex items-center justify-center bg-transparent border border-transparent tab-indicator">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/></svg>
                </div>
                <span class="text-[11px] font-bold tracking-tight" data-i18n="nav_profile">Profile</span>
            </button>
        </div>
    </nav>

    <script>
        const TODAY_STR = "{{ today_date }}";
        let deferredPrompt = null;

        const translations = {
            en: { staff: "Staff", item: "Item", entry_date: "📅 Entry Date:", live_mode: "Live Mode (Deducts Stock)", cash: "💵 Cash", mpesa: "📲 M-Pesa", total_sales: "📊 Total Sales", search_placeholder: "Search items...", btn_cash: "Cash", btn_mpesa: "M-Pesa", btn_split: "Split", btn_return: "Return", btn_in: "+ In", reports_title: "Sales & Staff Shifts", reports_subtitle: "Historical performance and staff handovers", today: "Today", yesterday: "Yesterday", week: "7 Days", month: "30 Days", revenue: "Revenue", units_sold: "Units Sold", raw_export: "📥 Flexible Range CSV Export:", csv_year: "Last Year CSV", csv_all: "All-Time CSV", staff_breakdown: "Staff Shift Breakdown", th_staff: "Staff", th_role: "Role", th_sales: "Sales", th_cash: "Cash", th_mpesa: "M-Pesa", th_total: "Total", stock_calibration: "Physical Stock Calibration", stock_subtitle: "Setting counts here overrides your shelf total directly", current_count: "Current Count", btn_set: "Set", store_logo: "Store Logo / Picture", btn_upload: "Upload", btn_remove: "Remove Logo", share_store: "Share Store Link", share_desc: "Copy your store link or share instantly with customers and friends via WhatsApp.", btn_copy: "Copy", btn_whatsapp: "Share via WhatsApp", app_version: "App Version", btn_close: "Close", btn_logout: "Log out", manage_cashiers: "Manage Cashiers", current_team: "Current Team", create_cashier: "Create New Cashier", username: "Username", password_pin: "Password / PIN", btn_create: "Create Cashier", add_product: "Add New Product", product_name: "Product Name", selling_price: "Selling Price (KES)", initial_stock: "Initial Stock", btn_cancel: "Cancel", btn_save: "Save", cash_kes: "Cash (KES)", mpesa_kes: "M-Pesa (KES)", btn_complete: "Complete Sale", nav_counter: "Counter", nav_reports: "Reports", nav_stocktake: "Stock Take", nav_profile: "Profile", install_app_btn: "Install App on Phone", closing_stock_card: "Remaining Closing Stock", closing_stock_sub: "Click to inspect remaining items & valuation", start_date: "Start Date", end_date: "End Date", settings_header: "App Settings & Preferences", theme_pref: "Theme Mode", footer_empower: "Empowering Businesses WorldWide." },
            sw: { staff: "Wafanyakazi", item: "Bidhaa", entry_date: "📅 Tarehe:", live_mode: "Hali ya Moja kwa Moja (Inapunguza Stock)", cash: "💵 Pesa taslimu", mpesa: "📲 M-Pesa", total_sales: "📊 Jumla ya Mauzo", search_placeholder: "Tafuta bidhaa...", btn_cash: "Cash", btn_mpesa: "M-Pesa", btn_split: "Gawanya", btn_return: "Rudisha", btn_in: "+ Ingiza", reports_title: "Mauzo na Zamu", reports_subtitle: "Utendaji wa kihistoria na zamu za wafanyakazi", today: "Leo", yesterday: "Jana", week: "Siku 7", month: "Siku 30", revenue: "Mapato", units_sold: "Bidhaa Zilizouzwa", raw_export: "📥 Hamisha Data kwa Tarehe:", csv_year: "CSV ya Mwaka Jana", csv_all: "CSV ya Wakati Wote", staff_breakdown: "Uchanganuzi wa Zamu", th_staff: "Mfanyakazi", th_role: "Nafasi", th_sales: "Mauzo", th_cash: "Pesa", th_mpesa: "M-Pesa", th_total: "Jumla", stock_calibration: "Kurekebisha Stock", stock_subtitle: "Kuandika idadi hapa kunabadilisha moja kwa moja rafu yako", current_count: "Idadi ya Sasa", btn_set: "Weka", store_logo: "Nembo ya Duka / Picha", btn_upload: "Weka", btn_remove: "Ondoa Nembo", share_store: "Shiriki Kiungo cha Duka", share_desc: "Nakili kiungo au ushiriki papo hapo na wateja kupitia WhatsApp.", btn_copy: "Nakili", btn_whatsapp: "Shiriki kupitia WhatsApp", app_version: "Toleo la App", btn_close: "Funga", btn_logout: "Ondoka", manage_cashiers: "Simamia Watoa Huduma", current_team: "Timu ya Sasa", create_cashier: "Unda Mfanyakazi Mpya", username: "Jina la mtumiaji", password_pin: "Nenosiri / PIN", btn_create: "Unda", add_product: "Ongeza Bidhaa Mpya", product_name: "Jina la Bidhaa", selling_price: "Bei ya KUUZA (KES)", initial_stock: "Stock ya Awali", btn_cancel: "Ghairi", btn_save: "Hifadhi", cash_kes: "Pesa (KES)", mpesa_kes: "M-Pesa (KES)", btn_complete: "Maliza Mauzo", nav_counter: "Kaunta", nav_reports: "Ripoti", nav_stocktake: "Hesabu ya Stock", nav_profile: "Wasifu", install_app_btn: "Weka App kwenye Simu", closing_stock_card: "Bidhaa Zilizobaki", closing_stock_sub: "Bonyeza kuona thamani na bidhaa zilizobaki", start_date: "Tarehe ya Kuanza", end_date: "Tarehe ya Mwisho", settings_header: "Mipangilio ya App", theme_pref: "Hali ya Rangi", footer_empower: "Kuwezesha Biashara Duniani Kote." },
            fr: { staff: "Personnel", item: "Article", entry_date: "📅 Date:", live_mode: "Mode en direct", cash: "💵 Espèces", mpesa: "📲 M-Pesa", total_sales: "📊 Ventes Totales", search_placeholder: "Rechercher...", btn_cash: "Espèces", btn_mpesa: "M-Pesa", btn_split: "Diviser", btn_return: "Retour", btn_in: "+ Entrée", reports_title: "Rapports", reports_subtitle: "Performance historique", today: "Aujourd'hui", yesterday: "Hier", week: "7 Jours", month: "30 Jours", revenue: "Revenu", units_sold: "Unités vendues", raw_export: "📥 Exporter Données:", csv_year: "CSV An Dernier", csv_all: "CSV Tout", staff_breakdown: "Détail du Personnel", th_staff: "Personnel", th_role: "Rôle", th_sales: "Ventes", th_cash: "Espèces", th_mpesa: "M-Pesa", th_total: "Total", stock_calibration: "Calibration Stock", stock_subtitle: "Modifie directement le stock", current_count: "Stock Actuel", btn_set: "Définir", store_logo: "Logo du Magasin", btn_upload: "Télécharger", btn_remove: "Supprimer Logo", share_store: "Partager le lien", share_desc: "Copiez le lien ou partagez avec vos clients via WhatsApp.", btn_copy: "Copier", btn_whatsapp: "Partager via WhatsApp", app_version: "Version", btn_close: "Fermer", btn_logout: "Déconnexion", manage_cashiers: "Gérer Caissiers", current_team: "Équipe", create_cashier: "Créer Caissier", username: "Nom d'utilisateur", password_pin: "Mot de passe", btn_create: "Créer", add_product: "Ajouter Article", product_name: "Nom", selling_price: "Prix (KES)", initial_stock: "Stock Initial", btn_cancel: "Annuler", btn_save: "Enregistrer", cash_kes: "Espèces (KES)", mpesa_kes: "M-Pesa (KES)", btn_complete: "Valider", nav_counter: "Comptoir", nav_reports: "Rapports", nav_stocktake: "Inventaire", nav_profile: "Profil", install_app_btn: "Installer l'application", closing_stock_card: "Stock de Clôture", closing_stock_sub: "Inspecter le stock restant", start_date: "Date Début", end_date: "Date Fin", settings_header: "Paramètres de l'application", theme_pref: "Mode Thème", footer_empower: "Autonomiser les entreprises du monde entier." },
            es: { staff: "Personal", item: "Artículo", entry_date: "📅 Fecha:", live_mode: "Modo en Vivo", cash: "💵 Efectivo", mpesa: "📲 M-Pesa", total_sales: "📊 Ventas Totales", search_placeholder: "Buscar...", btn_cash: "Efectivo", btn_mpesa: "M-Pesa", btn_split: "Dividir", btn_return: "Devolver", btn_in: "+ Entrar", reports_title: "Reportes", reports_subtitle: "Rendimiento histórico", today: "Hoy", yesterday: "Ayer", week: "7 Días", month: "30 Días", revenue: "Ingresos", units_sold: "Unidades", raw_export: "📥 Exportar Datos:", csv_year: "CSV Año Pasado", csv_all: "CSV Todo", staff_breakdown: "Desglose del Personal", th_staff: "Personal", th_role: "Rol", th_sales: "Ventas", th_cash: "Efectivo", th_mpesa: "M-Pesa", th_total: "Total", stock_calibration: "Calibración de Stock", stock_subtitle: "Modifica el stock directamente", current_count: "Conteo Actual", btn_set: "Fijar", store_logo: "Logo de Tienda", btn_upload: "Subir", btn_remove: "Eliminar Logo", share_store: "Compartir Enlace", share_desc: "Copia el enlace o compártelo con tus clientes por WhatsApp.", btn_copy: "Copiar", btn_whatsapp: "Compartir por WhatsApp", app_version: "Versión", btn_close: "Cerrar", btn_logout: "Cerrar Sesión", manage_cashiers: "Gestionar Cajeros", current_team: "Equipo", create_cashier: "Crear Cajero", username: "Usuario", password_pin: "Contraseña", btn_create: "Crear", add_product: "Agregar Producto", product_name: "Nombre", selling_price: "Precio (KES)", initial_stock: "Stock Inicial", btn_cancel: "Cancelar", btn_save: "Guardar", cash_kes: "Efectivo (KES)", mpesa_kes: "M-Pesa (KES)", btn_complete: "Completar Venta", nav_counter: "Mostrador", nav_reports: "Reportes", nav_stocktake: "Inventario", nav_profile: "Perfil", install_app_btn: "Instalar Aplicación", closing_stock_card: "Stock Restante", closing_stock_sub: "Ver inventario actual", start_date: "Fecha Inicio", end_date: "Fecha Fin", settings_header: "Configuración de la App", theme_pref: "Modo de Tema", footer_empower: "Empoderando negocios en todo el mundo." },
            ar: { staff: "الموظفين", item: "صنف", entry_date: "📅 تاريخ:", live_mode: "الوضع المباشر", cash: "💵 نقدي", mpesa: "📲 إمبيسا", total_sales: "📊 إجمالي المبيعات", search_placeholder: "بحث عن أصناف...", btn_cash: "نقدي", btn_mpesa: "إمبيسا", btn_split: "تقسيم", btn_return: "إرجاع", btn_in: "+ إدخال", reports_title: "التقارير", reports_subtitle: "الأداء التاريخي", today: "اليوم", yesterday: "أمس", week: "7 أيام", month: "30 يوم", revenue: "الإيرادات", units_sold: "الوحدات المباعة", raw_export: "📥 تصدير البيانات:", csv_year: "CSV العام الماضي", csv_all: "CSV الكل", staff_breakdown: "تفصيل ورديات الموظفين", th_staff: "الموظف", th_role: "الدور", th_sales: "المبيعات", th_cash: "نقدي", th_mpesa: "إمبيسا", th_total: "المجموع", stock_calibration: "مراجعة المخزون", stock_subtitle: "تعديل رصيد الرف مباشرة", current_count: "العدد الحالي", btn_set: "تعيين", store_logo: "شعار المتجر", btn_upload: "رفع", btn_remove: "إزالة الشعار", share_store: "مشاركة رابط المتجر", share_desc: "انسخ الرابط أو شاركه مع العملاء والأصدقاء عبر واتساب.", btn_copy: "نسخ", btn_whatsapp: "مشاركة عبر واتساب", app_version: "إصدار التطبيق", btn_close: "إغلاق", btn_logout: "تسجيل الخروج", manage_cashiers: "إدارة الكاشير", current_team: "الفريق الحالي", create_cashier: "إنشاء كاشير", username: "اسم المستخدم", password_pin: "كلمة المرور / الرمز", btn_create: "إنشاء", add_product: "إضافة منتج", product_name: "اسم المنتج", selling_price: "سعر البيع", initial_stock: "المخزون الأولي", btn_cancel: "إلغاء", btn_save: "حفظ", cash_kes: "نقدي", mpesa_kes: "إمبيسا", btn_complete: "إتمام البيع", nav_counter: "العداد", nav_reports: "التقارير", nav_stocktake: "جرد المخزون", nav_profile: "الملف الشخصي", install_app_btn: "تثبيت التطبيق على الهاتف", closing_stock_card: "المخزون المتبقي", closing_stock_sub: "عرض تقييم المخزون", start_date: "تاريخ البدء", end_date: "تاريخ الانتهاء", settings_header: "إعدادات التطبيق", theme_pref: "وضع المظهر", footer_empower: "تمكين الشركات في جميع أنحاء العالم." },
            zh: { staff: "员工", item: "商品", entry_date: "📅 日期：", live_mode: "实时模式", cash: "💵 现金", mpesa: "📲 移动支付", total_sales: "📊 总销售额", search_placeholder: "搜索商品...", btn_cash: "现金", btn_mpesa: "移动支付", btn_split: "拆分", btn_return: "退货", btn_in: "+ 入库", reports_title: "销售与班次", reports_subtitle: "历史业绩", today: "今天", yesterday: "昨天", week: "7天", month: "30天", revenue: "收入", units_sold: "销售数量", raw_export: "📥 导出原始数据:", csv_year: "去年CSV", csv_all: "全部CSV", staff_breakdown: "员工班次明细", th_staff: "员工", th_role: "角色", th_sales: "销售", th_cash: "现金", th_mpesa: "移动支付", th_total: "总计", stock_calibration: "库存校准", stock_subtitle: "直接覆盖货架库存", current_count: "当前盘点", btn_set: "设置", store_logo: "店铺标志", btn_upload: "上传", btn_remove: "移除Logo", share_store: "分享店铺链接", share_desc: "复制您的店铺链接，或通过WhatsApp快速分享给客户和好友。", btn_copy: "复制", btn_whatsapp: "通过WhatsApp分享", app_version: "应用版本", btn_close: "关闭", btn_logout: "退出登录", manage_cashiers: "管理收银员", current_team: "当前团队", create_cashier: "新建收银员", username: "用户名", password_pin: "密码/PIN", btn_create: "创建", add_product: "添加新商品", product_name: "商品名称", selling_price: "售价", initial_stock: "初始库存", btn_cancel: "取消", btn_save: "保存", cash_kes: "现金", mpesa_kes: "移动支付", btn_complete: "完成销售", nav_counter: "收银台", nav_reports: "报表", nav_stocktake: "盘点", nav_profile: "个人资料", install_app_btn: "在手机上安装应用", closing_stock_card: "剩余库存", closing_stock_sub: "查看剩余库存及估值", start_date: "开始日期", end_date: "结束日期", settings_header: "应用设置", theme_pref: "主题模式", footer_empower: "赋能全球企业。" }
        };

        function changeLanguage(lang) {
            localStorage.setItem('kiosk_lang', lang);
            const dict = translations[lang] || translations.en;
            document.querySelectorAll('[data-i18n]').forEach(el => {
                const key = el.getAttribute('data-i18n');
                if (dict[key]) el.innerText = dict[key];
            });
            document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
                const key = el.getAttribute('data-i18n-placeholder');
                if (dict[key]) el.placeholder = dict[key];
            });
        }

        window.addEventListener('DOMContentLoaded', () => {
            const savedLang = localStorage.getItem('kiosk_lang') || 'en';
            const langEl = document.getElementById('langSelect');
            if (langEl) {
                langEl.value = savedLang;
                changeLanguage(savedLang);
            }

            const savedTheme = localStorage.getItem('kiosk_theme');
            const themeIcon = document.getElementById('themeIcon');
            if (savedTheme === 'light') {
                document.documentElement.classList.remove('dark');
                if (themeIcon) themeIcon.innerText = '☀️';
            } else {
                document.documentElement.classList.add('dark');
                if (themeIcon) themeIcon.innerText = '🌙';
            }

            setTimeout(fetchServerLogo, 100);
        });

        async function fetchServerLogo() {
            try {
                const res = await fetch('/api/store/logo');
                const d = await res.json();
                if (d.logo) {
                    applyAvatar(d.logo);
                }
            } catch (err) { console.error(err); }
        }

        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/sw.js', { scope: '/' })
                .catch(err => console.error('SW Registration Failed:', err));
        }

        window.addEventListener('beforeinstallprompt', (e) => {
            e.preventDefault();
            deferredPrompt = e;
        });

        async function triggerNativeInstall() {
            if (!deferredPrompt) {
                alert("To install, open browser menu (⋮) and tap 'Install app' or 'Add to Home screen'.");
                return;
            }
            deferredPrompt.prompt();
            const { outcome } = await deferredPrompt.userChoice;
            deferredPrompt = null;
        }

        window.addEventListener('appinstalled', () => {
            showToast("Kiosk Track installed successfully!");
        });

        function copyStoreLink() {
            const input = document.getElementById('storeLinkInput');
            input.select();
            input.setSelectionRange(0, 99999);
            navigator.clipboard.writeText(input.value);
            showToast("Store link copied to clipboard!");
        }

        function onSaleDateChange() {
            const selected = document.getElementById('activeSaleDate').value;
            const notice = document.getElementById('dateNotice');
            if (selected === TODAY_STR) {
                notice.innerHTML = "<span>🟢</span> Live Mode (Deducts Stock)";
                notice.className = "text-[11px] font-semibold text-emerald-600 dark:text-emerald-400 flex items-center gap-1";
            } else {
                notice.innerHTML = "<span>⚠️</span> Backdated Mode (Reports Only - Shelf Stock Preserved)";
                notice.className = "text-[11px] font-bold text-amber-500 flex items-center gap-1";
            }
        }

        function toggleTheme() {
            const isDark = document.documentElement.classList.toggle('dark');
            const themeIcon = document.getElementById('themeIcon');
            if (themeIcon) {
                themeIcon.innerText = isDark ? '🌙' : '☀️';
            }
            localStorage.setItem('kiosk_theme', isDark ? 'dark' : 'light');
        }

        function switchTab(name, btn) {
            document.querySelectorAll('.tab-screen').forEach(s => s.classList.add('hidden'));
            document.getElementById(`screen-${name}`)?.classList.remove('hidden');
            document.querySelectorAll('.nav-tab').forEach(t => {
                t.className = "nav-tab flex flex-col items-center justify-center gap-1 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 transition";
                const indicator = t.querySelector('.tab-indicator');
                if(indicator) indicator.className = "w-10 h-7 rounded-full flex items-center justify-center bg-transparent border border-transparent tab-indicator";
            });
            btn.className = "nav-tab flex flex-col items-center justify-center gap-1 text-emerald-600 dark:text-emerald-400";
            const activeIndicator = btn.querySelector('.tab-indicator');
            if(activeIndicator) activeIndicator.className = "w-10 h-7 rounded-full flex items-center justify-center bg-emerald-100 dark:bg-emerald-950/80 border border-emerald-300 dark:border-emerald-800 shadow-sm tab-indicator";

            if(name === 'reports') loadReports('today');
        }

        function filterList(id, cls) {
            const q = document.getElementById(id).value.toLowerCase();
            document.querySelectorAll(cls).forEach(c => {
                c.style.display = c.dataset.name.toLowerCase().includes(q) ? '' : 'none';
            });
        }

        function adjustQty(id, delta) {
            const el = document.getElementById(id);
            el.value = Math.max(1, (parseInt(el.value) || 1) + delta);
        }

        function showToast(msg, ok = true) {
            const t = document.getElementById('toast');
            t.className = `fixed top-4 left-1/2 -translate-x-1/2 z-50 p-3 rounded-2xl text-xs font-bold shadow-2xl flex items-center gap-2 border transition-all duration-300 max-w-sm w-11/12 ${ok ? 'bg-emerald-900/90 text-emerald-200 border-emerald-700' : 'bg-rose-900/90 text-rose-200 border-rose-700'}`;
            t.innerText = msg;
            t.classList.remove('opacity-0', '-translate-y-2', 'pointer-events-none');
            t.classList.add('opacity-100', 'translate-y-0');
            setTimeout(() => {
                t.classList.add('opacity-0', '-translate-y-2', 'pointer-events-none');
                t.classList.remove('opacity-100', 'translate-y-0');
            }, 2500);
        }

        async function makeSale(itemId, payment) {
            const qty = parseInt(document.getElementById(`qty-${itemId}`).value) || 1;
            const saleDate = document.getElementById('activeSaleDate').value;
            const res = await fetch('/api/sale', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ item_id: itemId, quantity: qty, payment_method: payment, sale_date: saleDate })
            });
            const d = await res.json();
            if (res.ok) {
                document.getElementById(`stock-val-${itemId}`).innerText = d.new_stock;
                const auditVal = document.getElementById(`audit-sys-${itemId}`);
                if (auditVal) auditVal.innerText = d.new_stock;
                document.getElementById('statCash').innerText = `KES ${Math.round(d.today_cash).toLocaleString()}`;
                document.getElementById('statMpesa').innerText = `KES ${Math.round(d.today_mpesa).toLocaleString()}`;
                document.getElementById('statTotal').innerText = `KES ${Math.round(d.today_cash + d.today_mpesa).toLocaleString()}`;
                showToast(`Recorded sale for ${saleDate}`);
            } else {
                showToast(d.error || 'Sale failed', false);
            }
        }

        let activeSplitId = null, activeSplitTotal = 0, activeSplitQty = 1;
        function openSplitModal(id, name, price) {
            activeSplitId = id;
            activeSplitQty = parseInt(document.getElementById(`qty-${id}`).value) || 1;
            activeSplitTotal = price * activeSplitQty;
            document.getElementById('splitItemName').innerText = `${activeSplitQty}x ${name}`;
            document.getElementById('splitTotalDisplay').innerText = `Total Due: KES ${activeSplitTotal.toLocaleString()}`;
            document.getElementById('splitCashInput').value = '';
            document.getElementById('splitMpesaInput').value = activeSplitTotal;
            document.getElementById('splitModal').classList.remove('hidden');
        }
        function closeSplitModal() { document.getElementById('splitModal').classList.add('hidden'); }
        function autoCalculateMpesa() {
            const c = parseFloat(document.getElementById('splitCashInput').value) || 0;
            document.getElementById('splitMpesaInput').value = Math.max(0, activeSplitTotal - c);
        }
        async function submitSplitSale() {
            const c = parseFloat(document.getElementById('splitCashInput').value) || 0;
            const m = parseFloat(document.getElementById('splitMpesaInput').value) || 0;
            const saleDate = document.getElementById('activeSaleDate').value;
            const res = await fetch('/api/sale/split', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ item_id: activeSplitId, quantity: activeSplitQty, cash_amount: c, mpesa_amount: m, sale_date: saleDate })
            });
            const d = await res.json();
            if(res.ok) {
                document.getElementById(`stock-val-${activeSplitId}`).innerText = d.new_stock;
                document.getElementById('statCash').innerText = `KES ${Math.round(d.today_cash).toLocaleString()}`;
                document.getElementById('statMpesa').innerText = `KES ${Math.round(d.today_mpesa).toLocaleString()}`;
                document.getElementById('statTotal').innerText = `KES ${Math.round(d.today_cash + d.today_mpesa).toLocaleString()}`;
                showToast("Split sale recorded!");
                closeSplitModal();
            } else {
                showToast(d.error || 'Error recording split', false);
            }
        }

        async function reverseSale(id) {
            const q = parseInt(document.getElementById(`qty-${id}`).value) || 1;
            const res = await fetch('/api/sale/reverse', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ item_id: id, quantity: q })
            });
            const d = await res.json();
            if(res.ok) {
                document.getElementById(`stock-val-${id}`).innerText = d.new_stock;
                const auditVal = document.getElementById(`audit-sys-${id}`);
                if (auditVal) auditVal.innerText = d.new_stock;
                showToast(`Returned: Added ${q} pcs back to shelf`);
            }
        }

        async function updateStockTake(id) {
            const val = parseInt(document.getElementById(`counted-${id}`).value);
            if (isNaN(val)) {
                showToast("Enter a valid shelf count", false);
                return;
            }
            const res = await fetch('/api/stocktake/update-count', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ item_id: id, counted_quantity: val })
            });
            const d = await res.json();
            if(res.ok) {
                document.getElementById(`stock-val-${id}`).innerText = d.new_stock;
                const auditVal = document.getElementById(`audit-sys-${id}`);
                if (auditVal) auditVal.innerText = d.new_stock;
                showToast(`Shelf count updated to ${d.new_stock}`);
            }
        }

        async function makeRestock(id) {
            const q = parseInt(document.getElementById(`restock-qty-${id}`).value) || 0;
            const res = await fetch('/api/restock', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ item_id: id, quantity: q })
            });
            const d = await res.json();
            if(res.ok) {
                document.getElementById(`stock-val-${id}`).innerText = d.new_stock;
                showToast(`Restocked ${d.new_stock} pcs`);
            }
        }

        function setReportRange(range, btn) {
            document.querySelectorAll('.report-range-btn').forEach(b => {
                b.className = 'report-range-btn px-2.5 py-1 rounded-lg text-slate-500 dark:text-slate-400 hover:text-white transition';
            });
            btn.className = 'report-range-btn px-2.5 py-1 rounded-lg font-bold bg-emerald-600 text-white';
            loadReports(range);
        }

        async function loadReports(range) {
            const res = await fetch(`/api/reports?range=${range}`);
            const d = await res.json();
            document.getElementById('repTotalRev').innerText = `KES ${Math.round(d.summary.total_revenue).toLocaleString()}`;
            document.getElementById('repTotalUnits').innerText = `${d.summary.total_units} pcs`;
            document.getElementById('repCash').innerText = `KES ${Math.round(d.summary.cash).toLocaleString()}`;
            document.getElementById('repMpesa').innerText = `KES ${Math.round(d.summary.mpesa).toLocaleString()}`;

            const tbody = document.getElementById('staffTableBody');
            if (d.staff && d.staff.length > 0) {
                tbody.innerHTML = d.staff.map(s => `
                    <tr>
                        <td class="py-2.5 font-bold text-slate-900 dark:text-white">@${s.username}</td>
                        <td class="py-2.5 text-slate-500 uppercase text-[10px] font-semibold">${s.role}</td>
                        <td class="py-2.5 font-mono">${s.tx_count}</td>
                        <td class="py-2.5 text-emerald-600 dark:text-emerald-400 font-semibold font-mono">KES ${Math.round(s.cash_amount).toLocaleString()}</td>
                        <td class="py-2.5 text-green-600 dark:text-green-400 font-semibold font-mono">KES ${Math.round(s.mpesa_amount).toLocaleString()}</td>
                        <td class="py-2.5 font-black text-slate-900 dark:text-white font-mono">KES ${Math.round(s.total_amount).toLocaleString()}</td>
                    </tr>
                `).join('');
            } else {
                tbody.innerHTML = `<tr><td colspan="6" class="py-3 text-center text-slate-400">No staff sales recorded for this period</td></tr>`;
            }
        }

        let activeDrillMode = 'TOTAL';

        function openDrilldown(mode) {
            activeDrillMode = mode;
            const titles = { 
                CASH: '💵 Cash Transactions', 
                MPESA: '📲 M-Pesa Transactions', 
                TOTAL: '📊 All Sales Transactions' 
            };
            document.getElementById('drilldownTitle').innerText = titles[mode] || 'Sales Breakdown';
            // Set to today's active date selector value by default
            document.getElementById('drilldownDate').value = document.getElementById('activeSaleDate').value || TODAY_STR;
            document.getElementById('drilldownModal').classList.remove('hidden');
            fetchDrilldownData();
        }

        function resetDrilldownDate() {
            document.getElementById('drilldownDate').value = ''; // Clears filter to fetch ALL sales
            fetchDrilldownData();
        }

        async function fetchDrilldownData() {
            const dateVal = document.getElementById('drilldownDate').value;
            let url = `/api/transactions/drilldown?mode=${activeDrillMode}`;
            if (dateVal) url += `&date=${dateVal}`;

            const res = await fetch(url);
            const d = await res.json();
            const totalVal = parseFloat(d.total_amount) || 0;
            document.getElementById('drilldownSubTotal').innerText = `Total: KES ${Math.round(totalVal).toLocaleString()}`;
            
            const tbody = document.getElementById('drilldownTableBody');
            if (d.transactions && d.transactions.length > 0) {
                tbody.innerHTML = d.transactions.map(tx => `
                    <tr>
                        <td class="py-2 font-mono text-[11px] text-slate-500">${tx.timestamp}</td>
                        <td class="py-2 font-bold">${tx.product_name}</td>
                        <td class="py-2">${tx.quantity}</td>
                        <td class="py-2"><span class="px-2 py-0.5 rounded text-[10px] font-bold ${tx.payment_method === 'CASH' ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400' : 'bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-400'}">${tx.payment_method}</span></td>
                        <td class="py-2 font-black font-mono">KES ${Math.round(parseFloat(tx.total_amount) || 0).toLocaleString()}</td>
                    </tr>
                `).join('');
            } else {
                tbody.innerHTML = `<tr><td colspan="5" class="py-4 text-center text-slate-400">No transactions found for this selection</td></tr>`;
            }
        }

        function downloadDrilldownCsv() {
            const dateVal = document.getElementById('drilldownDate').value;
            let url = `/api/transactions/drilldown-csv?mode=${activeDrillMode}`;
            if (dateVal) url += `&date=${dateVal}`;
            window.location.href = url;
        }

        async function openUnitsSoldModal() {
            document.getElementById('unitsSoldModal').classList.remove('hidden');
            const res = await fetch('/api/reports/units-sold');
            const d = await res.json();
            const tbody = document.getElementById('unitsSoldTableBody');
            if (d.units && d.units.length > 0) {
                tbody.innerHTML = d.units.map(u => `
                    <tr>
                        <td class="py-2.5 font-bold">${u.product_name}</td>
                        <td class="py-2.5 font-mono">${u.total_units} pcs</td>
                        <td class="py-2.5 font-black font-mono text-emerald-600 dark:text-emerald-400">KES ${Math.round(u.total_sales).toLocaleString()}</td>
                    </tr>
                `).join('');
            } else {
                tbody.innerHTML = `<tr><td colspan="3" class="py-4 text-center text-slate-400">No units sold recorded</td></tr>`;
            }
        }
        function closeUnitsSoldModal() { document.getElementById('unitsSoldModal').classList.add('hidden'); }

        async function openClosingStockModal() {
            document.getElementById('closingStockModal').classList.remove('hidden');
            const res = await fetch('/api/reports/closing-stock');
            const d = await res.json();
            const tbody = document.getElementById('closingStockTableBody');
            if (d.stock && d.stock.length > 0) {
                tbody.innerHTML = d.stock.map(s => `
                    <tr>
                        <td class="py-2.5 font-bold">${s.product_name}</td>
                        <td class="py-2.5 font-mono">${s.current_stock} pcs</td>
                        <td class="py-2.5 font-black font-mono text-sky-600 dark:text-sky-400">KES ${Math.round(s.valuation).toLocaleString()}</td>
                    </tr>
                `).join('');
            } else {
                tbody.innerHTML = `<tr><td colspan="3" class="py-4 text-center text-slate-400">No stock items available</td></tr>`;
            }
        }
        function closeClosingStockModal() { document.getElementById('closingStockModal').classList.add('hidden'); }
        function downloadClosingStockCsv() { window.location.href = '/api/reports/closing-stock-csv'; }

        function downloadRangeCsv() {
            const start = document.getElementById('exportStart').value;
            const end = document.getElementById('exportEnd').value;
            if(!start || !end) {
                showToast("Please select both start and end dates", false);
                return;
            }
            window.location.href = `/api/reports/export-csv?start=${start}&end=${end}`;
        }

        async function handleAvatarUpload(event) {
            const file = event.target.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = async function(e) {
                const base64String = e.target.result;
                await fetch('/api/store/logo', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ logo: base64String })
                });
                applyAvatar(base64String);
                showToast("Store logo updated & synced!");
            };
            reader.readAsDataURL(file);
        }

        function applyAvatar(src) {
            ['navAvatarImage', 'avatarImage'].forEach(id => {
                const img = document.getElementById(id);
                if(img) { img.src = src; img.classList.remove('hidden'); }
            });
            ['navAvatarInitials', 'avatarInitials'].forEach(id => {
                const init = document.getElementById(id);
                if(init) { init.classList.add('hidden'); }
            });
        }

        async function removeAvatar() {
            await fetch('/api/store/logo', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ logo: '' })
            });
            ['navAvatarImage', 'avatarImage'].forEach(id => {
                const img = document.getElementById(id);
                if(img) { img.src = ''; img.classList.add('hidden'); }
            });
            ['navAvatarInitials', 'avatarInitials'].forEach(id => {
                const init = document.getElementById(id);
                if(init) { init.classList.remove('hidden'); }
            });
            showToast("Store logo removed");
        }

        async function openStaffModal() {
            document.getElementById('staffModal').classList.remove('hidden');
            const listEl = document.getElementById('existingStaffList');
            listEl.innerHTML = `<div class="py-3 text-center text-slate-400">Loading team...</div>`;
            
            try {
                const res = await fetch('/api/staff/list');
                if (!res.ok) {
                    let msg = `Request failed (${res.status})`;
                    if (res.status === 401 || res.status === 403) {
                        msg = "Session expired — please log out and log in again.";
                    }
                    listEl.innerHTML = `<div class="py-3 text-center text-rose-500 font-bold">${msg}</div>`;
                    return;
                }
                const data = await res.json();
                const users = Array.isArray(data) ? data : (data.users || []);
                if (users.length === 0) {
                    listEl.innerHTML = `<div class="py-3 text-center text-slate-400">No cashiers found. Create one below!</div>`;
                    return;
                }
                
                listEl.innerHTML = users.map(u => `
                    <div class="py-2.5 flex items-center justify-between border-b border-slate-100 dark:border-slate-800/80">
                        <div>
                            <span class="font-bold text-slate-900 dark:text-white">@${u.username}</span>
                            <span class="text-[10px] text-slate-400 uppercase font-semibold">(${u.role})</span>
                        </div>
                        ${u.role !== 'admin' ? `
                            <div class="flex items-center gap-2">
                                <button onclick="resetStaffPassword(${u.user_id}, '${u.username}')" class="text-[11px] font-bold text-indigo-500 hover:underline">
                                    Reset
                                </button>
                                <button onclick="deleteStaff(${u.user_id}, '${u.username}')" class="text-[11px] font-bold text-rose-500 hover:text-rose-600">
                                    Delete 🗑️
                                </button>
                            </div>
                        ` : '<span class="text-[10px] text-emerald-500 font-bold">Owner</span>'}
                    </div>
                `).join('');
            } catch (err) {
                listEl.innerHTML = `<div class="py-3 text-center text-rose-500 font-bold">Could not load staff: ${err.message}</div>`;
            }
        }
        
        function closeStaffModal() { document.getElementById('staffModal').classList.add('hidden'); }

        async function resetStaffPassword(userId, username) {
            const newPass = prompt(`Enter new password / PIN for @${username}:`);
            if (!newPass) return;
            const res = await fetch('/api/staff/reset-password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: userId, new_password: newPass })
            });
            const d = await res.json();
            if (res.ok) {
                showToast(`Password updated for @${username}`);
            } else {
                showToast(d.error || 'Failed to update password', false);
            }
        }

        async function deleteStaff(userId, username) {
            if (!confirm(`Are you sure you want to remove cashier @${username}?`)) {
                return;
            }
            const res = await fetch('/api/staff/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: userId })
            });
            const d = await res.json();
            if (res.ok) {
                showToast(`Cashier @${username} removed!`);
                openStaffModal(); // refresh the list
            } else {
                showToast(d.error || 'Failed to remove cashier', false);
            }
        }
        
        async function submitNewStaff() {
            const u = document.getElementById('staffUsername').value.trim();
            const p = document.getElementById('staffPassword').value;
            const res = await fetch('/api/staff/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username: u, password: p, role: 'cashier' })
            });
            const d = await res.json();
            if (res.ok) {
                showToast(`Cashier ${u} created!`);
                openStaffModal();
                document.getElementById('staffUsername').value = '';
                document.getElementById('staffPassword').value = '';
            } else {
                showToast(d.error || 'Failed to create user', false);
            }
        }

        function openAddItemModal() { document.getElementById('addItemModal').classList.remove('hidden'); }
        function closeAddItemModal() { document.getElementById('addItemModal').classList.add('hidden'); }
        async function submitNewItem() {
            const name = document.getElementById('newItemName').value.trim();
            const price = parseFloat(document.getElementById('newItemPrice').value);
            const stock = parseInt(document.getElementById('newItemStock').value) || 0;
            const res = await fetch('/api/items/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, unit_price: price, initial_stock: stock })
            });
            if(res.ok) { location.reload(); }
            else { const d = await res.json(); showToast(d.error || 'Error adding item', false); }
        }
    </script>
</body>
</html>
"""

AUTH_TEMPLATE = """
<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login - Kiosk Track</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-100 dark:bg-slate-950 text-slate-900 dark:text-slate-100 min-h-screen flex items-center justify-center p-4">
    <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-3xl p-6 sm:p-8 max-w-sm w-full shadow-2xl space-y-4">
        <div class="text-center space-y-2">
            <img src="/static/app_icon.svg" alt="Logo" class="w-14 h-14 mx-auto rounded-2xl shadow-md">
            <div>
                <h1 class="text-xl font-black text-slate-900 dark:text-white tracking-tight">Kiosk Track</h1>
                <p class="text-xs text-slate-500 font-medium">Cloud Inventory & Point of Sale</p>
            </div>
        </div>

        {% if error %}
        <div class="bg-rose-100 dark:bg-rose-950/80 border border-rose-300 dark:border-rose-800 text-rose-700 dark:text-rose-300 text-xs p-3 rounded-xl text-center font-semibold">
            {{ error }}
        </div>
        {% endif %}
        {% if message %}
        <div class="bg-emerald-100 dark:bg-emerald-950/80 border border-emerald-300 dark:border-emerald-800 text-emerald-700 dark:text-emerald-300 text-xs p-3 rounded-xl text-center font-semibold">
            {{ message }}
        </div>
        {% endif %}

        <form method="POST" action="{{ action_url }}" class="space-y-3.5 text-xs">
            {% if mode == 'register' %}
            <div>
                <label class="block text-slate-500 dark:text-slate-400 font-bold mb-1 uppercase text-[10px]">Shop Name</label>
                <input type="text" name="shop_name" required placeholder="e.g. Westlands Mini Mart" 
                       class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl px-3 py-2.5 text-slate-900 dark:text-white focus:outline-none focus:border-emerald-500 font-semibold">
            </div>
            <div>
                <label class="block text-slate-500 dark:text-slate-400 font-bold mb-1 uppercase text-[10px]">Your Mobile Phone</label>
                <input type="tel" name="phone" required placeholder="e.g. 0712345678" 
                       class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl px-3 py-2.5 text-slate-900 dark:text-white focus:outline-none focus:border-emerald-500 font-semibold">
            </div>
            <div>
                <label class="block text-slate-500 dark:text-slate-400 font-bold mb-1 uppercase text-[10px]">Admin Username</label>
                <input type="text" name="username" required placeholder="Enter username" 
                       class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl px-3 py-2.5 text-slate-900 dark:text-white focus:outline-none focus:border-emerald-500 font-semibold">
            </div>
            <div>
                <label class="block text-slate-500 dark:text-slate-400 font-bold mb-1 uppercase text-[10px]">Password</label>
                <input type="password" name="password" required placeholder="••••••••" 
                       class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl px-3 py-2.5 text-slate-900 dark:text-white focus:outline-none focus:border-emerald-500 font-semibold">
            </div>
            <div>
                <label class="block text-slate-500 dark:text-slate-400 font-bold mb-1 uppercase text-[10px]">4-Digit Recovery PIN</label>
                <input type="password" name="recovery_pin" maxlength="4" required placeholder="4-digit PIN (e.g. 1997)" 
                       class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl px-3 py-2.5 text-slate-900 dark:text-white focus:outline-none focus:border-emerald-500 font-semibold">
            </div>

            {% elif mode == 'forgot' %}
            <div>
                <label class="block text-slate-500 dark:text-slate-400 font-bold mb-1 uppercase text-[10px]">Registered Phone Number</label>
                <input type="tel" name="phone" required placeholder="e.g. 0712345678" 
                       class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl px-3 py-2.5 text-slate-900 dark:text-white focus:outline-none focus:border-emerald-500 font-semibold">
            </div>
            <div>
                <label class="block text-slate-500 dark:text-slate-400 font-bold mb-1 uppercase text-[10px]">4-Digit Recovery PIN</label>
                <input type="password" name="recovery_pin" maxlength="4" required placeholder="••••" 
                       class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl px-3 py-2.5 text-slate-900 dark:text-white focus:outline-none focus:border-emerald-500 font-semibold">
            </div>
            <div>
                <label class="block text-slate-500 dark:text-slate-400 font-bold mb-1 uppercase text-[10px]">New Password</label>
                <input type="password" name="new_password" required placeholder="Enter new password" 
                       class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl px-3 py-2.5 text-slate-900 dark:text-white focus:outline-none focus:border-emerald-500 font-semibold">
            </div>

            {% else %}
            <div>
                <label class="block text-slate-500 dark:text-slate-400 font-bold mb-1 uppercase text-[10px]">Username or Phone</label>
                <input type="text" name="login_identifier" required placeholder="Enter username or phone" 
                       class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl px-3 py-2.5 text-slate-900 dark:text-white focus:outline-none focus:border-emerald-500 font-semibold">
            </div>
            <div>
                <label class="block text-slate-500 dark:text-slate-400 font-bold mb-1 uppercase text-[10px]">Password</label>
                <input type="password" name="password" required placeholder="••••••••" 
                       class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl px-3 py-2.5 text-slate-900 dark:text-white focus:outline-none focus:border-emerald-500 font-semibold">
            </div>
            {% endif %}

            <button type="submit" class="w-full bg-emerald-600 hover:bg-emerald-500 text-white font-black py-2.5 rounded-xl transition text-xs shadow-lg mt-2">
                {{ button_text }}
            </button>
        </form>

        <div class="mt-4 pt-3 border-t border-slate-200 dark:border-slate-800 text-center text-xs space-y-2">
            {% if mode == 'login' %}
            <div><a href="/forgot-password" class="text-slate-500 dark:text-slate-400 hover:text-emerald-500 font-bold">Forgot Password?</a></div>
            <div><span class="text-slate-400">Want to run your shop?</span> <a href="/register-shop" class="text-emerald-600 dark:text-emerald-400 font-bold hover:underline">Register New Shop</a></div>
            {% elif mode == 'register' %}
            <div><span class="text-slate-400">Already registered?</span> <a href="/login" class="text-emerald-600 dark:text-emerald-400 font-bold hover:underline">Log In</a></div>
            {% else %}
            <div><a href="/login" class="text-emerald-600 dark:text-emerald-400 font-bold hover:underline">Back to Login</a></div>
            {% endif %}
        </div>
    </div>
</body>
</html>
"""


# --- Routes ---

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identifier = request.form.get("login_identifier", "").strip()
        password = request.form.get("password", "")

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT u.user_id, u.shop_id, u.username, u.password_hash, u.role, s.name as shop_name
            FROM users u
            JOIN shops s ON u.shop_id = s.shop_id
            WHERE u.username = ? OR u.phone = ?
        """, (identifier, identifier))
        user = cursor.fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session.permanent = True  # Keeps you logged in for 30 days
            session["user_id"] = user["user_id"]
            session["shop_id"] = user["shop_id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session["shop_name"] = user["shop_name"]
            return redirect(url_for("index"))
        return render_template_string(AUTH_TEMPLATE, mode="login", action_url="/login", button_text="Sign In", error="Invalid login credentials")

    return render_template_string(AUTH_TEMPLATE, mode="login", action_url="/login", button_text="Sign In", error=None)


@app.route("/register-shop", methods=["GET", "POST"])
def register_shop():
    if request.method == "POST":
        shop_name = request.form.get("shop_name", "").strip()
        phone = request.form.get("phone", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        recovery_pin = request.form.get("recovery_pin", "").strip()

        if not shop_name or not username or not password or not phone or not recovery_pin:
            return render_template_string(AUTH_TEMPLATE, mode="register", action_url="/register-shop", button_text="Register Business", error="Please fill all fields")

        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO shops (name) VALUES (?)", (shop_name,))
            shop_id = cursor.lastrowid
            cursor.execute("""
                INSERT INTO users (shop_id, username, phone, recovery_pin, password_hash, role)
                VALUES (?, ?, ?, ?, ?, 'admin')
            """, (shop_id, username, phone, recovery_pin, generate_password_hash(password)))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            return render_template_string(AUTH_TEMPLATE, mode="register", action_url="/register-shop", button_text="Register Business", error="Username already registered")
        
        conn.close()
        return redirect(url_for("login"))

    return render_template_string(AUTH_TEMPLATE, mode="register", action_url="/register-shop", button_text="Register Business", error=None)


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        phone = request.form.get("phone", "").strip()
        pin = request.form.get("recovery_pin", "").strip()
        new_password = request.form.get("new_password", "")

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username FROM users WHERE phone = ? AND recovery_pin = ? AND role = 'admin'", (phone, pin))
        user = cursor.fetchone()

        if not user:
            conn.close()
            return render_template_string(AUTH_TEMPLATE, mode="forgot", action_url="/forgot-password", button_text="Reset Password", error="Phone number or Recovery PIN did not match")

        cursor.execute("UPDATE users SET password_hash = ? WHERE user_id = ?", (generate_password_hash(new_password), user["user_id"]))
        conn.commit()
        conn.close()
        return render_template_string(AUTH_TEMPLATE, mode="login", action_url="/login", button_text="Sign In", message="Password reset successful! You can now log in.")

    return render_template_string(AUTH_TEMPLATE, mode="forgot", action_url="/forgot-password", button_text="Reset Password", error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    shop_id = session["shop_id"]
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM items WHERE shop_id = ? AND is_active = 1 ORDER BY name ASC;", (shop_id,))
    items = cursor.fetchall()

    cursor.execute("""
        SELECT 
            COALESCE(SUM(CASE WHEN payment_method = 'CASH' THEN total_amount ELSE 0 END), 0) as cash_total,
            COALESCE(SUM(CASE WHEN payment_method = 'MPESA' THEN total_amount ELSE 0 END), 0) as mpesa_total
        FROM transactions
        WHERE shop_id = ? AND DATE(timestamp, 'localtime') = DATE('now', 'localtime');
    """, (shop_id,))
    totals = cursor.fetchone()
    conn.close()

    today_str = date.today().isoformat()

    return render_template_string(
        HTML_TEMPLATE,
        items=items,
        today_cash=totals["cash_total"],
        today_mpesa=totals["mpesa_total"],
        today_date=today_str
    )


@app.route("/api/store/logo", methods=["GET", "POST"])
@login_required
def store_logo():
    shop_id = session["shop_id"]
    conn = get_db()
    cursor = conn.cursor()
    if request.method == "POST":
        data = request.get_json() or {}
        logo_data = data.get("logo", "")
        cursor.execute("UPDATE shops SET store_logo = ? WHERE shop_id = ?", (logo_data, shop_id))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    else:
        cursor.execute("SELECT store_logo FROM shops WHERE shop_id = ?", (shop_id,))
        row = cursor.fetchone()
        conn.close()
        return jsonify({"logo": row["store_logo"] if row and row["store_logo"] else ""})


@app.route("/api/staff/list", methods=["GET"])
@admin_required
def list_staff():
    shop_id = session.get("shop_id")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_id, username, role 
        FROM users 
        WHERE shop_id = ? OR shop_id = 1 
        ORDER BY role ASC, username ASC;
    """, (shop_id,))
    users = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify({"users": users})


@app.route("/api/staff/create", methods=["POST"])
@admin_required
def create_staff():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = data.get("password")
    role = data.get("role", "cashier")

    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO users (shop_id, username, password_hash, role)
            VALUES (?, ?, ?, ?)
        """, (session["shop_id"], username, generate_password_hash(password), role))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "Username already taken"}), 400

    conn.close()
    return jsonify({"success": True})


@app.route("/api/staff/reset-password", methods=["POST"])
@admin_required
def admin_reset_staff_password():
    data = request.get_json() or {}
    user_id = int(data.get("user_id", 0))
    new_password = data.get("new_password", "")

    if not new_password:
        return jsonify({"error": "New password cannot be empty"}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET password_hash = ? WHERE user_id = ? AND role != 'admin'", 
                   (generate_password_hash(new_password), user_id))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/staff/delete", methods=["POST"])
@admin_required
def delete_staff():
    data = request.get_json() or {}
    user_id = int(data.get("user_id", 0))

    if not user_id:
        return jsonify({"error": "User ID required"}), 400

    conn = get_db()
    cursor = conn.cursor()
    # Ensure the user being deleted belongs to this shop and is NOT an admin
    cursor.execute("SELECT role FROM users WHERE user_id = ?", (user_id,))
    target = cursor.fetchone()
    
    if not target:
        conn.close()
        return jsonify({"error": "User not found"}), 404
        
    if target["role"] == "admin":
        conn.close()
        return jsonify({"error": "Cannot delete admin account"}), 403

    cursor.execute("DELETE FROM users WHERE user_id = ? AND role != 'admin'", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/sale", methods=["POST"])
@login_required
def api_sale():
    data = request.get_json() or {}
    item_id = int(data.get("item_id", 0))
    qty = int(data.get("quantity", 1))
    payment = data.get("payment_method", "CASH")
    sale_date = data.get("sale_date") or date.today().isoformat()
    shop_id = session["shop_id"]
    user_id = session["user_id"]

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT name, unit_price, current_stock FROM items WHERE item_id = ? AND shop_id = ?", (item_id, shop_id))
    item = cursor.fetchone()

    if not item:
        conn.close()
        return jsonify({"error": "Item not found"}), 404

    total = qty * item["unit_price"]
    
    cursor.execute("""
        INSERT INTO transactions (shop_id, user_id, item_id, movement_type, payment_method, quantity, unit_price, total_amount, timestamp)
        VALUES (?, ?, ?, 'OUT', ?, ?, ?, ?, datetime(?, '12:00:00'))
    """, (shop_id, user_id, item_id, payment, qty, item["unit_price"], total, sale_date))

    if sale_date == date.today().isoformat():
        cursor.execute("UPDATE items SET current_stock = current_stock - ? WHERE item_id = ?", (qty, item_id))

    cursor.execute("SELECT current_stock FROM items WHERE item_id = ?", (item_id,))
    new_stock = cursor.fetchone()["current_stock"]

    cursor.execute("""
        SELECT 
            COALESCE(SUM(CASE WHEN payment_method = 'CASH' THEN total_amount ELSE 0 END), 0) as cash_total,
            COALESCE(SUM(CASE WHEN payment_method = 'MPESA' THEN total_amount ELSE 0 END), 0) as mpesa_total
        FROM transactions
        WHERE shop_id = ? AND DATE(timestamp, 'localtime') = DATE('now', 'localtime');
    """, (shop_id,))
    totals = cursor.fetchone()
    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "new_stock": new_stock,
        "today_cash": totals["cash_total"],
        "today_mpesa": totals["mpesa_total"]
    })


@app.route("/api/sale/split", methods=["POST"])
@login_required
def api_sale_split():
    data = request.get_json() or {}
    item_id = int(data.get("item_id", 0))
    qty = int(data.get("quantity", 1))
    cash_amount = float(data.get("cash_amount", 0.0))
    mpesa_amount = float(data.get("mpesa_amount", 0.0))
    sale_date = data.get("sale_date") or date.today().isoformat()
    shop_id = session["shop_id"]
    user_id = session["user_id"]

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT name, unit_price, current_stock FROM items WHERE item_id = ? AND shop_id = ?", (item_id, shop_id))
    item = cursor.fetchone()

    if not item:
        conn.close()
        return jsonify({"error": "Item not found"}), 404

    cursor.execute("""
        INSERT INTO transactions (shop_id, user_id, item_id, movement_type, payment_method, quantity, unit_price, total_amount, timestamp)
        VALUES (?, ?, ?, 'OUT', 'CASH', ?, ?, ?, datetime(?, '12:00:00'))
    """, (shop_id, user_id, item_id, qty, item["unit_price"], cash_amount, sale_date))

    cursor.execute("""
        INSERT INTO transactions (shop_id, user_id, item_id, movement_type, payment_method, quantity, unit_price, total_amount, timestamp)
        VALUES (?, ?, ?, 'OUT', 'MPESA', 0, ?, ?, datetime(?, '12:00:00'))
    """, (shop_id, user_id, item_id, item["unit_price"], mpesa_amount, sale_date))

    if sale_date == date.today().isoformat():
        cursor.execute("UPDATE items SET current_stock = current_stock - ? WHERE item_id = ?", (qty, item_id))

    cursor.execute("SELECT current_stock FROM items WHERE item_id = ?", (item_id,))
    new_stock = cursor.fetchone()["current_stock"]

    cursor.execute("""
        SELECT 
            COALESCE(SUM(CASE WHEN payment_method = 'CASH' THEN total_amount ELSE 0 END), 0) as cash_total,
            COALESCE(SUM(CASE WHEN payment_method = 'MPESA' THEN total_amount ELSE 0 END), 0) as mpesa_total
        FROM transactions
        WHERE shop_id = ? AND DATE(timestamp, 'localtime') = DATE('now', 'localtime');
    """, (shop_id,))
    totals = cursor.fetchone()
    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "new_stock": new_stock,
        "today_cash": totals["cash_total"],
        "today_mpesa": totals["mpesa_total"]
    })


@app.route("/api/sale/reverse", methods=["POST"])
@admin_required
def api_sale_reverse():
    data = request.get_json() or {}
    item_id = int(data.get("item_id", 0))
    qty = int(data.get("quantity", 1))
    shop_id = session["shop_id"]
    user_id = session["user_id"]

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT unit_price FROM items WHERE item_id = ? AND shop_id = ?", (item_id, shop_id))
    item = cursor.fetchone()

    total = qty * item["unit_price"]
    cursor.execute("UPDATE items SET current_stock = current_stock + ? WHERE item_id = ?", (qty, item_id))

    cursor.execute("""
        INSERT INTO transactions (shop_id, user_id, item_id, movement_type, payment_method, quantity, unit_price, total_amount)
        VALUES (?, ?, ?, 'IN', 'CASH', ?, ?, ?)
    """, (shop_id, user_id, item_id, qty, item["unit_price"], -total))

    cursor.execute("SELECT current_stock FROM items WHERE item_id = ?", (item_id,))
    new_stock = cursor.fetchone()["current_stock"]
    conn.commit()
    conn.close()

    return jsonify({"success": True, "new_stock": new_stock})


@app.route("/api/stocktake/update-count", methods=["POST"])
@admin_required
def update_stock_count():
    data = request.get_json() or {}
    item_id = int(data.get("item_id", 0))
    counted_qty = int(data.get("counted_quantity", 0))
    shop_id = session["shop_id"]
    user_id = session["user_id"]

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE items SET current_stock = ? WHERE item_id = ? AND shop_id = ?", (counted_qty, item_id, shop_id))
    
    cursor.execute("""
        INSERT INTO transactions (shop_id, user_id, item_id, movement_type, payment_method, quantity, unit_price, total_amount)
        VALUES (?, ?, ?, 'ADJUSTMENT', 'N/A', ?, 0, 0)
    """, (shop_id, user_id, item_id, counted_qty))

    conn.commit()
    conn.close()
    return jsonify({"success": True, "new_stock": counted_qty})


@app.route("/api/restock", methods=["POST"])
@admin_required
def api_restock():
    data = request.get_json() or {}
    item_id = int(data.get("item_id", 0))
    qty = int(data.get("quantity", 0))
    shop_id = session["shop_id"]
    user_id = session["user_id"]

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT unit_price FROM items WHERE item_id = ? AND shop_id = ?", (item_id, shop_id))
    item = cursor.fetchone()

    cursor.execute("UPDATE items SET current_stock = current_stock + ? WHERE item_id = ?", (qty, item_id))

    cursor.execute("""
        INSERT INTO transactions (shop_id, user_id, item_id, movement_type, payment_method, quantity, unit_price, total_amount)
        VALUES (?, ?, ?, 'IN', 'N/A', ?, ?, ?)
    """, (shop_id, user_id, item_id, qty, item["unit_price"], qty * item["unit_price"]))

    cursor.execute("SELECT current_stock FROM items WHERE item_id = ?", (item_id,))
    new_stock = cursor.fetchone()["current_stock"]
    conn.commit()
    conn.close()

    return jsonify({"success": True, "new_stock": new_stock})


@app.route("/api/items/add", methods=["POST"])
@admin_required
def add_new_item():
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    price = float(data.get("unit_price", 0))
    stock = int(data.get("initial_stock", 0))
    shop_id = session["shop_id"]
    user_id = session["user_id"]

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO items (shop_id, name, unit_price, current_stock, is_active)
        VALUES (?, ?, ?, ?, 1)
    """, (shop_id, name, price, stock))
    item_id = cursor.lastrowid

    if stock > 0:
        cursor.execute("""
            INSERT INTO transactions (shop_id, user_id, item_id, movement_type, payment_method, quantity, unit_price, total_amount)
            VALUES (?, ?, ?, 'IN', 'N/A', ?, ?, ?)
        """, (shop_id, user_id, item_id, stock, price, stock * price))

    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/reports", methods=["GET"])
@admin_required
def get_reports():
    shop_id = session["shop_id"]
    range_type = request.args.get("range", "today")

    if range_type == "today":
        date_filter = "DATE(t.timestamp, 'localtime') = DATE('now', 'localtime')"
    elif range_type == "yesterday":
        date_filter = "DATE(t.timestamp, 'localtime') = DATE('now', 'localtime', '-1 day')"
    elif range_type == "week":
        date_filter = "DATE(t.timestamp, 'localtime') >= DATE('now', 'localtime', '-7 days')"
    elif range_type == "month":
        date_filter = "DATE(t.timestamp, 'localtime') >= DATE('now', 'localtime', '-30 days')"
    else:
        date_filter = "1=1"

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(f"""
        SELECT 
            COALESCE(SUM(CASE WHEN movement_type = 'OUT' THEN quantity ELSE 0 END), 0) AS total_units_sold,
            COALESCE(SUM(CASE WHEN movement_type = 'OUT' THEN total_amount ELSE 0 END), 0) AS total_sales_val,
            COALESCE(SUM(CASE WHEN payment_method = 'CASH' AND movement_type = 'OUT' THEN total_amount ELSE 0 END), 0) AS cash_val,
            COALESCE(SUM(CASE WHEN payment_method = 'MPESA' AND movement_type = 'OUT' THEN total_amount ELSE 0 END), 0) AS mpesa_val
        FROM transactions t
        WHERE shop_id = ? AND {date_filter};
    """, (shop_id,))
    totals = cursor.fetchone()

    cursor.execute(f"""
        SELECT 
            u.username,
            u.role,
            COUNT(t.transaction_id) AS tx_count,
            COALESCE(SUM(CASE WHEN t.payment_method = 'CASH' THEN t.total_amount ELSE 0 END), 0) AS cash_amount,
            COALESCE(SUM(CASE WHEN t.payment_method = 'MPESA' THEN t.total_amount ELSE 0 END), 0) AS mpesa_amount,
            COALESCE(SUM(t.total_amount), 0) AS total_amount
        FROM transactions t
        JOIN users u ON t.user_id = u.user_id
        WHERE t.shop_id = ? AND t.movement_type = 'OUT' AND {date_filter}
        GROUP BY u.user_id
        ORDER BY total_amount DESC;
    """, (shop_id,))
    staff_summary = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return jsonify({
        "summary": {
            "total_revenue": totals["total_sales_val"],
            "total_units": totals["total_units_sold"],
            "cash": totals["cash_val"],
            "mpesa": totals["mpesa_val"]
        },
        "staff": staff_summary
    })


@app.route("/api/transactions/drilldown", methods=["GET"])
@admin_required
def drilldown_transactions():
    shop_id = session.get("shop_id")
    mode = request.args.get("mode", "TOTAL")
    date_val = request.args.get("date")

    pay_filter = ""
    if mode == "CASH":
        pay_filter = "AND t.payment_method = 'CASH'"
    elif mode == "MPESA":
        pay_filter = "AND t.payment_method = 'MPESA'"

    date_filter = ""
    params = [shop_id]
    if date_val and date_val.strip():
        date_filter = "AND DATE(t.timestamp, 'localtime') = ?"
        params.append(date_val.strip())
    # If date_val is empty, we show all recent transactions for the shop instead of locking to UTC now

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT 
            t.transaction_id,
            t.timestamp,
            i.name AS product_name,
            t.quantity,
            t.payment_method,
            t.total_amount
        FROM transactions t
        JOIN items i ON t.item_id = i.item_id
        WHERE (t.shop_id = ? OR t.shop_id = 1) AND t.movement_type = 'OUT' {pay_filter} {date_filter}
        ORDER BY t.timestamp DESC;
    """, tuple(params))
    rows = [dict(r) for r in cursor.fetchall()]

    cursor.execute(f"""
        SELECT COALESCE(SUM(t.total_amount), 0) as total
        FROM transactions t
        WHERE (t.shop_id = ? OR t.shop_id = 1) AND t.movement_type = 'OUT' {pay_filter} {date_filter};
    """, tuple(params))
    total_val = cursor.fetchone()["total"]
    conn.close()

    return jsonify({"transactions": rows, "total_amount": total_val})


@app.route("/api/transactions/drilldown-csv", methods=["GET"])
@admin_required
def drilldown_csv():
    shop_id = session.get("shop_id")
    mode = request.args.get("mode", "TOTAL")
    date_val = request.args.get("date")

    pay_filter = ""
    if mode == "CASH":
        pay_filter = "AND t.payment_method = 'CASH'"
    elif mode == "MPESA":
        pay_filter = "AND t.payment_method = 'MPESA'"

    date_filter = ""
    params = [shop_id]
    if date_val and date_val.strip():
        date_filter = "AND DATE(t.timestamp, 'localtime') = ?"
        params.append(date_val.strip())

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT 
            t.transaction_id,
            t.timestamp,
            i.name AS product_name,
            t.quantity,
            t.payment_method,
            t.total_amount,
            u.username AS handled_by
        FROM transactions t
        JOIN items i ON t.item_id = i.item_id
        JOIN users u ON t.user_id = u.user_id
        WHERE (t.shop_id = ? OR t.shop_id = 1) AND t.movement_type = 'OUT' {pay_filter} {date_filter}
        ORDER BY t.timestamp DESC;
    """, tuple(params))
    rows = cursor.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Transaction ID", "Timestamp", "Product Name", "Quantity", "Payment Method", "Total Amount (KES)", "Staff Member"])
    for row in rows:
        writer.writerow([row["transaction_id"], row["timestamp"], row["product_name"], row["quantity"], row["payment_method"], row["total_amount"], row["handled_by"]])
    output.seek(0)
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=sales_breakdown_{mode.lower()}.csv"})


@app.route("/api/reports/units-sold", methods=["GET"])
@admin_required
def units_sold_report():
    shop_id = session["shop_id"]
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            i.name AS product_name,
            SUM(t.quantity) AS total_units,
            SUM(t.total_amount) AS total_sales
        FROM transactions t
        JOIN items i ON t.item_id = i.item_id
        WHERE t.shop_id = ? AND t.movement_type = 'OUT'
        GROUP BY i.item_id
        ORDER BY total_units DESC;
    """, (shop_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify({"units": rows})


@app.route("/api/reports/closing-stock", methods=["GET"])
@admin_required
def closing_stock_report():
    shop_id = session["shop_id"]
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            name AS product_name,
            current_stock,
            (current_stock * unit_price) AS valuation
        FROM items
        WHERE shop_id = ? AND is_active = 1
        ORDER BY name ASC;
    """, (shop_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify({"stock": rows})


@app.route("/api/reports/closing-stock-csv", methods=["GET"])
@admin_required
def closing_stock_csv():
    shop_id = session["shop_id"]
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            name AS product_name,
            current_stock,
            unit_price,
            (current_stock * unit_price) AS valuation
        FROM items
        WHERE shop_id = ? AND is_active = 1
        ORDER BY name ASC;
    """, (shop_id,))
    rows = cursor.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Product Name", "In Stock", "Unit Price (KES)", "Valuation (KES)"])
    for row in rows:
        writer.writerow([row["product_name"], row["current_stock"], row["unit_price"], row["valuation"]])
    output.seek(0)
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=closing_stock_valuation.csv"})


@app.route("/api/reports/export-csv", methods=["GET"])
@admin_required
def export_raw_csv():
    shop_id = session["shop_id"]
    start_date = request.args.get("start")
    end_date = request.args.get("end")
    range_type = request.args.get("range")

    date_filter = "1=1"
    params = [shop_id]
    if start_date and end_date:
        date_filter = "DATE(t.timestamp, 'localtime') BETWEEN ? AND ?"
        params.extend([start_date, end_date])
    elif range_type == "last_year":
        date_filter = "strftime('%Y', t.timestamp) = strftime('%Y', 'now', '-1 year')"

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT 
            t.transaction_id,
            t.timestamp,
            i.name AS product_name,
            t.movement_type,
            t.payment_method,
            t.quantity,
            t.unit_price,
            t.total_amount,
            u.username AS handled_by
        FROM transactions t
        JOIN items i ON t.item_id = i.item_id
        JOIN users u ON t.user_id = u.user_id
        WHERE t.shop_id = ? AND {date_filter}
        ORDER BY t.timestamp DESC;
    """, tuple(params))
    rows = cursor.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Transaction ID", "Timestamp", "Product Name", 
        "Movement Type", "Payment Method", "Quantity", 
        "Unit Price (KES)", "Total Amount (KES)", "Staff Member"
    ])
    for row in rows:
        writer.writerow([
            row["transaction_id"], row["timestamp"], row["product_name"],
            row["movement_type"], row["payment_method"], row["quantity"],
            row["unit_price"], row["total_amount"], row["handled_by"]
        ])
    output.seek(0)
    
    filename = f"sales_report_{start_date}_to_{end_date}.csv" if start_date else "sales_report.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.route("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json", mimetype="application/json")


@app.route("/sw.js")
def service_worker():
    return send_from_directory("static", "sw.js", mimetype="application/javascript")


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))