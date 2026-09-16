import csv
import io
import os
import sqlite3
from datetime import datetime, date
from functools import wraps
from flask import (
    Flask, render_template_string, request, jsonify, 
    send_from_directory, session, redirect, url_for
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "kiosk_pos_enterprise_multitenant_key_2026")

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

    # 1. Shops Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS shops (
            shop_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 2. Users Table (with phone and recovery_pin for self-serve reset)
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

    # 3. Items Table (with direct current_stock column)
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

    # 4. Transactions Table
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

    # --- Live Migration Check for Existing Database ---
    cursor.execute("PRAGMA table_info(users);")
    user_cols = [row["name"] for row in cursor.fetchall()]
    if "phone" not in user_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN phone TEXT;")
    if "recovery_pin" not in user_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN recovery_pin TEXT;")

    cursor.execute("PRAGMA table_info(items);")
    item_cols = [row["name"] for row in cursor.fetchall()]
    if "shop_id" not in item_cols:
        cursor.execute("ALTER TABLE items ADD COLUMN shop_id INTEGER DEFAULT 1;")
    if "current_stock" not in item_cols:
        cursor.execute("ALTER TABLE items ADD COLUMN current_stock INTEGER NOT NULL DEFAULT 0;")
        # Backfill initial stock from past transaction sums if upgrading
        cursor.execute("""
            UPDATE items 
            SET current_stock = COALESCE((
                SELECT SUM(
                    CASE 
                        WHEN movement_type = 'IN' THEN quantity
                        WHEN movement_type = 'OUT' THEN -quantity
                        WHEN movement_type = 'ADJUSTMENT' THEN quantity
                        ELSE 0 
                    END
                ) FROM transactions WHERE transactions.item_id = items.item_id
            ), 0);
        """)
    if "created_at" not in item_cols:
        cursor.execute("ALTER TABLE items ADD COLUMN created_at DATETIME;")
        cursor.execute("UPDATE items SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL;")

    cursor.execute("PRAGMA table_info(transactions);")
    tx_cols = [row["name"] for row in cursor.fetchall()]
    if "shop_id" not in tx_cols:
        cursor.execute("ALTER TABLE transactions ADD COLUMN shop_id INTEGER DEFAULT 1;")
    if "user_id" not in tx_cols:
        cursor.execute("ALTER TABLE transactions ADD COLUMN user_id INTEGER DEFAULT 1;")

    # Seed initial shop and master admin if empty
    cursor.execute("SELECT COUNT(*) FROM shops;")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO shops (shop_id, name) VALUES (1, 'Kiosk Track Main');")
        cursor.execute("""
            INSERT INTO users (shop_id, username, phone, recovery_pin, password_hash, role)
            VALUES (1, 'admin', '0700000000', '1234', ?, 'admin');
        """, (generate_password_hash("admin123"),))

    # Compatibility view
    cursor.execute("DROP VIEW IF EXISTS view_current_stock;")
    cursor.execute("""
        CREATE VIEW view_current_stock AS
        SELECT 
            item_id,
            shop_id,
            name,
            unit_price,
            current_stock,
            reorder_level,
            is_active
        FROM items
        WHERE is_active = 1;
    """)

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
<body class="bg-slate-50 dark:bg-slate-950 text-slate-900 dark:text-slate-100 min-h-screen font-sans antialiased transition-colors duration-200">

    <!-- Top Navigation -->
    <nav class="sticky top-0 z-40 backdrop-blur-xl bg-white/80 dark:bg-slate-900/80 border-b border-slate-200 dark:border-slate-800/80 px-4 py-3">
        <div class="max-w-3xl mx-auto flex items-center justify-between">
            <div class="flex items-center gap-2.5">
                <img src="/static/app_icon.svg" alt="Logo" class="w-9 h-9 rounded-xl shadow-md">
                <div>
                    <h1 class="text-base font-extrabold tracking-tight text-slate-900 dark:text-white leading-none">{{ session.get('shop_name', 'Kiosk Track') }}</h1>
                    <div class="flex items-center gap-2 mt-0.5">
                        <span class="text-[10px] font-bold text-emerald-600 dark:text-emerald-400 flex items-center gap-1">
                            <span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span> @{{ session.get('username') }} ({{ session.get('role')|capitalize }})
                        </span>
                        <a href="/logout" class="text-[10px] font-bold text-rose-500 hover:underline">Log out</a>
                    </div>
                </div>
            </div>

            <!-- Top Action Group -->
            <div class="flex items-center gap-1.5 sm:gap-2">
                <button id="directInstallBtn" onclick="triggerNativeInstall()" class="hidden bg-gradient-to-r from-emerald-500 to-teal-400 text-slate-950 text-xs font-black px-3 py-1.5 rounded-xl shadow-md active:scale-95 transition flex items-center gap-1">
                    <span>📲</span> Install
                </button>
                <button onclick="toggleTheme()" class="p-2 rounded-xl bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:scale-105 active:scale-95 transition">
                    <span id="themeIcon">🌙</span>
                </button>
                {% if session.get('role') == 'admin' %}
                <button onclick="openStaffModal()" title="Manage Staff" class="bg-slate-200 dark:bg-slate-800 hover:bg-slate-300 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-200 text-xs font-bold px-2.5 py-2 rounded-xl transition flex items-center gap-1">
                    <span>👥</span> Staff
                </button>
                <button onclick="openAddItemModal()" class="bg-indigo-600 hover:bg-indigo-500 active:scale-95 text-white text-xs font-bold px-3 py-2 rounded-xl shadow transition flex items-center gap-1">
                    <span>+</span> Item
                </button>
                {% endif %}
            </div>
        </div>
    </nav>

    <!-- Notification Toast -->
    <div id="toast" class="fixed top-4 left-1/2 -translate-x-1/2 z-50 transition-all duration-300 opacity-0 pointer-events-none transform -translate-y-2 max-w-sm w-11/12"></div>

    <main class="max-w-3xl mx-auto px-3 sm:px-4 pt-4 pb-28">

        <!-- SCREEN 1: POS COUNTER -->
        <section id="screen-counter" class="tab-screen">
            
            <!-- Date Context Selector -->
            <div class="bg-slate-100 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-2.5 mb-3 flex flex-wrap items-center justify-between gap-2 text-xs">
                <div class="flex items-center gap-2">
                    <span class="text-[10px] font-bold text-slate-400 uppercase tracking-wider">📅 Entry Date:</span>
                    <input type="date" id="activeSaleDate" value="{{ today_date }}" onchange="onSaleDateChange()" 
                           class="bg-white dark:bg-slate-950 border border-slate-300 dark:border-slate-700 rounded-xl px-2.5 py-1 text-xs font-bold text-slate-900 dark:text-white">
                </div>
                <div id="dateNotice" class="text-[11px] font-semibold text-emerald-600 dark:text-emerald-400 flex items-center gap-1">
                    <span>🟢</span> Live Mode (Deducts Stock)
                </div>
            </div>

            <!-- Stats Bar -->
            <div class="grid grid-cols-3 gap-2.5 mb-4">
                <div class="bg-white dark:bg-slate-900/80 border border-slate-200 dark:border-slate-800 rounded-2xl p-3 shadow-sm">
                    <span class="block text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">💵 Cash</span>
                    <div class="text-base sm:text-lg font-black text-emerald-600 dark:text-emerald-400" id="statCash">KES {{ "{:,.0f}".format(today_cash) }}</div>
                </div>
                <div class="bg-white dark:bg-slate-900/80 border border-slate-200 dark:border-slate-800 rounded-2xl p-3 shadow-sm">
                    <span class="block text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">📲 M-Pesa</span>
                    <div class="text-base sm:text-lg font-black text-green-600 dark:text-green-400" id="statMpesa">KES {{ "{:,.0f}".format(today_mpesa) }}</div>
                </div>
                <div class="bg-white dark:bg-slate-900/80 border border-slate-200 dark:border-slate-800 rounded-2xl p-3 shadow-sm">
                    <span class="block text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">📊 Total Sales</span>
                    <div class="text-base sm:text-lg font-black text-slate-900 dark:text-white" id="statTotal">KES {{ "{:,.0f}".format(today_cash + today_mpesa) }}</div>
                </div>
            </div>

            <!-- Fast Search -->
            <div class="sticky top-[61px] z-30 mb-4">
                <div class="relative shadow-sm rounded-2xl">
                    <div class="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-slate-400">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
                    </div>
                    <input type="text" id="counterSearch" oninput="filterList('counterSearch', '.counter-card')" placeholder="Search items..." 
                           class="w-full pl-11 pr-10 py-3 bg-white dark:bg-slate-900/95 backdrop-blur-md border border-slate-200 dark:border-slate-700/80 text-slate-900 dark:text-white rounded-2xl placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-emerald-500 text-sm font-medium">
                </div>
            </div>

            <!-- Products List -->
            <div class="space-y-2.5" id="counterList">
                {% for item in items %}
                <div class="counter-card bg-white dark:bg-slate-900/70 border border-slate-200 dark:border-slate-800/80 rounded-2xl p-3.5 hover:border-slate-300 dark:hover:border-slate-700 transition relative shadow-sm" 
                     id="item-card-{{ item['item_id'] }}" data-name="{{ item['name'] }}">
                    <div class="flex items-start justify-between gap-2 mb-2">
                        <div>
                            <h2 class="font-bold text-slate-900 dark:text-white text-sm sm:text-base leading-snug">{{ item['name'] }}</h2>
                            <span class="text-xs font-semibold text-emerald-600 dark:text-emerald-400">KES {{ "{:,.1f}".format(item['unit_price']) }}</span>
                        </div>
                        <div class="flex items-center gap-1.5">
                            <span id="badge-{{ item['item_id'] }}" class="px-2.5 py-1 rounded-full text-xs font-bold {% if item['current_stock'] <= 0 %}bg-rose-100 dark:bg-rose-950 text-rose-700 dark:text-rose-400 border border-rose-300 dark:border-rose-800{% elif item['current_stock'] <= item['reorder_level'] %}bg-amber-100 dark:bg-amber-950 text-amber-700 dark:text-amber-300 border border-amber-300 dark:border-amber-800{% else %}bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-200 border border-slate-300 dark:border-slate-700{% endif %}">
                                Stock: <span id="stock-val-{{ item['item_id'] }}">{{ item['current_stock'] }}</span>
                            </span>
                        </div>
                    </div>

                    <div class="flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-slate-100 dark:border-slate-800/70">
                        <div class="flex items-center gap-1.5">
                            <div class="flex items-center bg-slate-100 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl px-1">
                                <button onclick="adjustQty('qty-{{ item['item_id'] }}', -1)" class="w-6 h-7 text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white font-bold text-sm">-</button>
                                <input type="number" id="qty-{{ item['item_id'] }}" value="1" min="1" 
                                       class="w-8 bg-transparent text-center text-xs font-bold text-slate-900 dark:text-white focus:outline-none">
                                <button onclick="adjustQty('qty-{{ item['item_id'] }}', 1)" class="w-6 h-7 text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white font-bold text-sm">+</button>
                            </div>

                            <button onclick="makeSale({{ item['item_id'] }}, 'CASH')" 
                                    class="bg-emerald-600 hover:bg-emerald-500 text-white active:scale-95 px-2.5 py-1.5 rounded-xl text-xs font-bold shadow transition flex items-center gap-1">
                                <span>💵</span> Cash
                            </button>
                            <button onclick="makeSale({{ item['item_id'] }}, 'MPESA')" 
                                    class="bg-green-600 hover:bg-green-500 text-white active:scale-95 px-2.5 py-1.5 rounded-xl text-xs font-bold shadow transition flex items-center gap-1">
                                <span>📲</span> M-Pesa
                            </button>
                            <button onclick="openSplitModal({{ item['item_id'] }}, '{{ item['name'] }}', {{ item['unit_price'] }})" 
                                    class="bg-amber-100 dark:bg-amber-950/80 hover:bg-amber-200 dark:hover:bg-amber-900 border border-amber-300 dark:border-amber-800/60 text-amber-800 dark:text-amber-300 active:scale-95 px-2 py-1.5 rounded-xl text-xs font-bold transition flex items-center gap-1">
                                <span>⚡</span> Split
                            </button>
                        </div>

                        {% if session.get('role') == 'admin' %}
                        <div class="flex items-center gap-1.5">
                            <button onclick="reverseSale({{ item['item_id'] }})" 
                                    title="Undo accidental sale"
                                    class="bg-rose-50 dark:bg-rose-950/70 hover:bg-rose-100 dark:hover:bg-rose-900 text-rose-700 dark:text-rose-300 border border-rose-200 dark:border-rose-800/60 active:scale-95 px-2 py-1.5 rounded-xl text-xs font-bold transition flex items-center gap-1">
                                <span>↩</span> Return
                            </button>
                            <input type="number" id="restock-qty-{{ item['item_id'] }}" placeholder="+Qty" min="1" 
                                   class="w-12 px-2 py-1.5 bg-slate-100 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl text-center text-xs text-slate-900 dark:text-white focus:outline-none">
                            <button onclick="makeRestock({{ item['item_id'] }})" 
                                    class="bg-slate-200 dark:bg-slate-800 hover:bg-slate-300 dark:hover:bg-slate-700 text-sky-700 dark:text-sky-300 active:scale-95 px-2.5 py-1.5 rounded-xl text-xs font-bold border border-slate-300 dark:border-slate-700 transition flex items-center gap-1">
                                <span>📦</span> + In
                            </button>
                        </div>
                        {% endif %}
                    </div>
                </div>
                {% endfor %}
            </div>
        </section>

        {% if session.get('role') == 'admin' %}
        <!-- SCREEN 2: REPORTS & ANALYTICS (Admin Only) -->
        <section id="screen-reports" class="tab-screen hidden">
            <div class="bg-white dark:bg-slate-900/90 border border-slate-200 dark:border-slate-800 rounded-2xl p-4 mb-4 shadow-sm">
                <div class="flex flex-wrap items-center justify-between gap-3 mb-4">
                    <div>
                        <h2 class="text-base font-bold text-slate-900 dark:text-white flex items-center gap-1.5">
                            <span>📊</span> Sales & Staff Shifts
                        </h2>
                        <p class="text-xs text-slate-500">Historical performance and staff handovers</p>
                    </div>
                    <div class="flex items-center gap-1 bg-slate-100 dark:bg-slate-950 p-1 rounded-xl border border-slate-200 dark:border-slate-800 text-xs">
                        <button onclick="setReportRange('today', this)" class="report-range-btn px-2.5 py-1 rounded-lg font-bold bg-emerald-600 text-white">Today</button>
                        <button onclick="setReportRange('yesterday', this)" class="report-range-btn px-2.5 py-1 rounded-lg text-slate-500 dark:text-slate-400">Yesterday</button>
                        <button onclick="setReportRange('week', this)" class="report-range-btn px-2.5 py-1 rounded-lg text-slate-500 dark:text-slate-400">7 Days</button>
                        <button onclick="setReportRange('month', this)" class="report-range-btn px-2.5 py-1 rounded-lg text-slate-500 dark:text-slate-400">30 Days</button>
                    </div>
                </div>

                <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-5">
                    <div class="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-xl border border-slate-200 dark:border-slate-800/80">
                        <span class="text-[10px] text-slate-500 uppercase font-bold flex items-center gap-1"><span>💰</span> Revenue</span>
                        <div id="repTotalRev" class="text-base font-black text-slate-900 dark:text-white">KES 0</div>
                    </div>
                    <div class="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-xl border border-slate-200 dark:border-slate-800/80">
                        <span class="text-[10px] text-slate-500 uppercase font-bold flex items-center gap-1"><span>📦</span> Units Sold</span>
                        <div id="repTotalUnits" class="text-base font-black text-emerald-600 dark:text-emerald-400">0 pcs</div>
                    </div>
                    <div class="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-xl border border-slate-200 dark:border-slate-800/80">
                        <span class="text-[10px] text-slate-500 uppercase font-bold flex items-center gap-1"><span>💵</span> Cash</span>
                        <div id="repCash" class="text-base font-black text-emerald-500">KES 0</div>
                    </div>
                    <div class="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-xl border border-slate-200 dark:border-slate-800/80">
                        <span class="text-[10px] text-slate-500 uppercase font-bold flex items-center gap-1"><span>📲</span> M-Pesa</span>
                        <div id="repMpesa" class="text-base font-black text-green-500">KES 0</div>
                    </div>
                </div>

                <!-- Staff Performance Breakdown Table -->
                <div class="bg-slate-50 dark:bg-slate-950 p-4 rounded-2xl border border-slate-200 dark:border-slate-800 mb-4">
                    <h3 class="text-xs font-bold text-slate-700 dark:text-slate-300 uppercase tracking-wider mb-3 flex items-center gap-1.5">
                        <span>👥</span> Staff Shift Breakdown
                    </h3>
                    <div class="overflow-x-auto">
                        <table class="w-full text-xs text-left">
                            <thead class="text-[10px] uppercase text-slate-400 border-b border-slate-200 dark:border-slate-800">
                                <tr>
                                    <th class="py-2">Staff</th>
                                    <th class="py-2">Role</th>
                                    <th class="py-2">Sales</th>
                                    <th class="py-2">Cash</th>
                                    <th class="py-2">M-Pesa</th>
                                    <th class="py-2 font-bold">Total</th>
                                </tr>
                            </thead>
                            <tbody id="staffTableBody" class="divide-y divide-slate-100 dark:divide-slate-800/70">
                                <tr><td colspan="6" class="py-3 text-center text-slate-400">Loading staff shift details...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>

            </div>
        </section>

        <!-- SCREEN 3: PHYSICAL STOCK TAKE (Admin Only) -->
        <section id="screen-audit" class="tab-screen hidden">
            <div class="bg-white dark:bg-slate-900/90 border border-slate-200 dark:border-slate-800 rounded-2xl p-4 mb-4 shadow-sm">
                <div class="mb-4">
                    <h2 class="text-base font-bold text-slate-900 dark:text-white flex items-center gap-1.5">
                        <span>📋</span> Physical Stock Calibration
                    </h2>
                    <p class="text-xs text-slate-500">Setting counts here overrides your shelf total directly</p>
                </div>
                <div class="divide-y divide-slate-100 dark:divide-slate-800/80 max-h-[500px] overflow-y-auto pr-1">
                    {% for item in items %}
                    <div class="audit-row py-2.5 flex items-center justify-between gap-2">
                        <div>
                            <div class="font-bold text-slate-900 dark:text-white text-xs leading-snug">{{ item['name'] }}</div>
                            <span class="text-[11px] text-slate-500">Current Count: <b id="audit-sys-{{ item['item_id'] }}">{{ item['current_stock'] }}</b></span>
                        </div>
                        <div class="flex items-center gap-1.5">
                            <input type="number" id="counted-{{ item['item_id'] }}" placeholder="Counted" 
                                   class="w-16 px-2 py-1 bg-slate-100 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-lg text-center text-xs font-bold text-slate-900 dark:text-white">
                            <button onclick="updateStockTake({{ item['item_id'] }})" 
                                    class="bg-sky-600 hover:bg-sky-500 text-white font-bold text-xs px-2.5 py-1 rounded-lg flex items-center gap-1">
                                <span>✓</span> Set
                            </button>
                        </div>
                    </div>
                    {% endfor %}
                </div>
            </div>
        </section>
        {% endif %}

        <!-- STAFF MANAGEMENT MODAL (Admin Only) -->
        <div id="staffModal" class="hidden fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
            <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl w-full max-w-md p-5 shadow-2xl space-y-4">
                <div class="flex items-center justify-between border-b border-slate-200 dark:border-slate-800 pb-2">
                    <h3 class="text-sm font-bold text-slate-900 dark:text-white">Manage Cashiers</h3>
                    <button onclick="closeStaffModal()" class="text-slate-400 text-lg">&times;</button>
                </div>
                
                <!-- Existing Staff List with Password Reset -->
                <div class="space-y-2 max-h-48 overflow-y-auto pr-1">
                    <h4 class="text-[10px] font-bold uppercase text-slate-400">Current Team</h4>
                    <div id="existingStaffList" class="divide-y divide-slate-100 dark:divide-slate-800 text-xs">
                        <!-- Loaded dynamically -->
                    </div>
                </div>

                <div class="pt-3 border-t border-slate-200 dark:border-slate-800 space-y-2 text-xs">
                    <h4 class="text-[10px] font-bold uppercase text-slate-400">Create New Cashier</h4>
                    <div>
                        <label class="block font-semibold mb-1">Username</label>
                        <input type="text" id="staffUsername" class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-300 dark:border-slate-700 rounded-xl px-3 py-2">
                    </div>
                    <div>
                        <label class="block font-semibold mb-1">Password / PIN</label>
                        <input type="password" id="staffPassword" class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-300 dark:border-slate-700 rounded-xl px-3 py-2">
                    </div>
                </div>
                <div class="flex justify-end gap-2 pt-2 border-t border-slate-200 dark:border-slate-800">
                    <button onclick="closeStaffModal()" class="px-3 py-1.5 text-xs text-slate-500">Close</button>
                    <button onclick="submitNewStaff()" class="bg-indigo-600 text-white font-bold text-xs px-4 py-1.5 rounded-xl">Create Cashier</button>
                </div>
            </div>
        </div>

        <!-- ADD ITEM MODAL -->
        <div id="addItemModal" class="hidden fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
            <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl w-full max-w-sm p-5 shadow-2xl space-y-4">
                <div class="flex items-center justify-between border-b border-slate-200 dark:border-slate-800 pb-2.5">
                    <h3 class="text-sm font-bold text-slate-900 dark:text-white">Add New Product</h3>
                    <button onclick="closeAddItemModal()" class="text-slate-400 text-lg">&times;</button>
                </div>
                <div class="space-y-3 text-xs">
                    <div>
                        <label class="block font-semibold mb-1">Product Name</label>
                        <input type="text" id="newItemName" class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-300 dark:border-slate-700 rounded-xl px-3 py-2">
                    </div>
                    <div class="grid grid-cols-2 gap-2">
                        <div>
                            <label class="block font-semibold mb-1">Selling Price (KES)</label>
                            <input type="number" id="newItemPrice" step="0.5" class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-300 dark:border-slate-700 rounded-xl px-3 py-2">
                        </div>
                        <div>
                            <label class="block font-semibold mb-1">Initial Stock</label>
                            <input type="number" id="newItemStock" class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-300 dark:border-slate-700 rounded-xl px-3 py-2">
                        </div>
                    </div>
                </div>
                <div class="flex justify-end gap-2 pt-2 border-t border-slate-200 dark:border-slate-800">
                    <button onclick="closeAddItemModal()" class="px-3 py-1.5 text-xs text-slate-500">Cancel</button>
                    <button onclick="submitNewItem()" class="bg-indigo-600 text-white font-bold text-xs px-4 py-1.5 rounded-xl">Save</button>
                </div>
            </div>
        </div>

        <!-- SPLIT PAYMENT MODAL -->
        <div id="splitModal" class="hidden fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
            <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl w-full max-w-sm p-5 shadow-2xl space-y-4">
                <div class="flex items-center justify-between border-b border-slate-200 dark:border-slate-800 pb-2.5">
                    <div>
                        <h3 class="text-sm font-bold" id="splitItemName">Item Name</h3>
                        <span class="text-xs text-emerald-600 font-bold" id="splitTotalDisplay">Total: KES 0</span>
                    </div>
                    <button onclick="closeSplitModal()" class="text-slate-400 text-lg">&times;</button>
                </div>
                <div class="space-y-3 text-xs">
                    <div>
                        <label class="block font-semibold mb-1">Cash (KES)</label>
                        <input type="number" id="splitCashInput" oninput="autoCalculateMpesa()" class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-300 dark:border-slate-700 rounded-xl px-3 py-2">
                    </div>
                    <div>
                        <label class="block font-semibold mb-1">M-Pesa (KES)</label>
                        <input type="number" id="splitMpesaInput" class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-300 dark:border-slate-700 rounded-xl px-3 py-2">
                    </div>
                </div>
                <div class="flex justify-end gap-2 pt-2 border-t border-slate-200 dark:border-slate-800">
                    <button onclick="closeSplitModal()" class="px-3 py-1.5 text-xs text-slate-500">Cancel</button>
                    <button onclick="submitSplitSale()" class="bg-emerald-600 text-white font-bold text-xs px-4 py-2 rounded-xl">Complete Sale</button>
                </div>
            </div>
        </div>

    </main>

    <!-- Bottom Navigation -->
    <nav class="fixed bottom-0 left-0 right-0 z-40 bg-white/95 dark:bg-slate-900/95 backdrop-blur-xl border-t border-slate-200 dark:border-slate-800/90 pb-[env(safe-area-inset-bottom)]">
        <div class="max-w-md mx-auto grid {% if session.get('role') == 'admin' %}grid-cols-3{% else %}grid-cols-1{% endif %} h-16">
            <button onclick="switchTab('counter', this)" class="nav-tab flex flex-col items-center justify-center gap-1 text-emerald-600">
                <div class="w-10 h-7 rounded-full flex items-center justify-center bg-emerald-100 dark:bg-emerald-950/80 border border-emerald-300 dark:border-emerald-800/60 shadow-sm tab-indicator">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M16 11V7a4 4 0 00-8 0v4M5 9h14l1 12H4L5 9z"/></svg>
                </div>
                <span class="text-[11px] font-bold tracking-tight">Counter</span>
            </button>
            {% if session.get('role') == 'admin' %}
            <button onclick="switchTab('reports', this)" class="nav-tab flex flex-col items-center justify-center gap-1 text-slate-400">
                <div class="w-10 h-7 rounded-full flex items-center justify-center bg-transparent border border-transparent tab-indicator">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>
                </div>
                <span class="text-[11px] font-bold tracking-tight">Reports</span>
            </button>
            <button onclick="switchTab('audit', this)" class="nav-tab flex flex-col items-center justify-center gap-1 text-slate-400">
                <div class="w-10 h-7 rounded-full flex items-center justify-center bg-transparent border border-transparent tab-indicator">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"/></svg>
                </div>
                <span class="text-[11px] font-bold tracking-tight">Stock Take</span>
            </button>
            {% endif %}
        </div>
    </nav>

    <script>
        const TODAY_STR = "{{ today_date }}";

        let deferredPrompt = null;
        const installBtn = document.getElementById('directInstallBtn');

        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/sw.js', { scope: '/' })
                .catch(err => console.error('SW Registration Failed:', err));
        }

        window.addEventListener('beforeinstallprompt', (e) => {
            e.preventDefault();
            deferredPrompt = e;
            if (installBtn) installBtn.classList.remove('hidden');
        });

        async function triggerNativeInstall() {
            if (!deferredPrompt) {
                alert("To install, open browser menu (⋮) and tap 'Install app' or 'Add to Home screen'.");
                return;
            }
            deferredPrompt.prompt();
            const { outcome } = await deferredPrompt.userChoice;
            if (outcome === 'accepted' && installBtn) {
                installBtn.classList.add('hidden');
            }
            deferredPrompt = null;
        }

        window.addEventListener('appinstalled', () => {
            if (installBtn) installBtn.classList.add('hidden');
            showToast("Kiosk Track installed successfully!");
        });

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
            document.documentElement.classList.toggle('dark');
        }

        function switchTab(name, btn) {
            document.querySelectorAll('.tab-screen').forEach(s => s.classList.add('hidden'));
            document.getElementById(`screen-${name}`)?.classList.remove('hidden');
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
                b.className = 'report-range-btn px-2.5 py-1 rounded-lg text-slate-500 dark:text-slate-400';
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

        async function openStaffModal() {
            document.getElementById('staffModal').classList.remove('hidden');
            const res = await fetch('/api/staff/list');
            const data = await res.json();
            const listEl = document.getElementById('existingStaffList');
            if (data.users && data.users.length > 0) {
                listEl.innerHTML = data.users.map(u => `
                    <div class="py-2 flex items-center justify-between">
                        <div>
                            <span class="font-bold">@${u.username}</span> 
                            <span class="text-[10px] text-slate-400 uppercase">(${u.role})</span>
                        </div>
                        ${u.role !== 'admin' ? `
                            <button onclick="resetStaffPassword(${u.user_id}, '${u.username}')" class="text-[11px] font-bold text-indigo-500 hover:underline">
                                Reset Password
                            </button>
                        ` : '<span class="text-[10px] text-emerald-500 font-bold">Owner</span>'}
                    </div>
                `).join('');
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
<body class="bg-slate-950 text-slate-100 min-h-screen flex items-center justify-center p-4">
    <div class="bg-slate-900 border border-slate-800 rounded-3xl p-6 sm:p-8 max-w-sm w-full shadow-2xl">
        <h1 class="text-xl font-black text-center mb-1 text-white tracking-tight">Kiosk Track</h1>
        <p class="text-xs text-slate-400 text-center mb-6">Cloud Inventory & Point of Sale</p>

        {% if error %}
        <div class="bg-rose-950/80 border border-rose-800 text-rose-300 text-xs p-3 rounded-xl mb-4 text-center font-semibold">
            {{ error }}
        </div>
        {% endif %}
        {% if message %}
        <div class="bg-emerald-950/80 border border-emerald-800 text-emerald-300 text-xs p-3 rounded-xl mb-4 text-center font-semibold">
            {{ message }}
        </div>
        {% endif %}

        <form method="POST" action="{{ action_url }}" class="space-y-3.5 text-xs">
            {% if mode == 'register' %}
            <div>
                <label class="block text-slate-400 font-bold mb-1 uppercase text-[10px]">Shop Name</label>
                <input type="text" name="shop_name" required placeholder="e.g. Westlands Mini Mart" 
                       class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-white focus:outline-none focus:border-emerald-500 font-semibold">
            </div>
            <div>
                <label class="block text-slate-400 font-bold mb-1 uppercase text-[10px]">Your Mobile Phone</label>
                <input type="tel" name="phone" required placeholder="e.g. 0712345678" 
                       class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-white focus:outline-none focus:border-emerald-500 font-semibold">
            </div>
            <div>
                <label class="block text-slate-400 font-bold mb-1 uppercase text-[10px]">Admin Username</label>
                <input type="text" name="username" required placeholder="Enter username" 
                       class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-white focus:outline-none focus:border-emerald-500 font-semibold">
            </div>
            <div>
                <label class="block text-slate-400 font-bold mb-1 uppercase text-[10px]">Password</label>
                <input type="password" name="password" required placeholder="••••••••" 
                       class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-white focus:outline-none focus:border-emerald-500 font-semibold">
            </div>
            <div>
                <label class="block text-slate-400 font-bold mb-1 uppercase text-[10px]">4-Digit Recovery PIN (Used if you forget password)</label>
                <input type="password" name="recovery_pin" maxlength="4" required placeholder="4-digit PIN (e.g. 1997)" 
                       class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-white focus:outline-none focus:border-emerald-500 font-semibold">
            </div>

            {% elif mode == 'forgot' %}
            <div>
                <label class="block text-slate-400 font-bold mb-1 uppercase text-[10px]">Registered Phone Number</label>
                <input type="tel" name="phone" required placeholder="e.g. 0712345678" 
                       class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-white focus:outline-none focus:border-emerald-500 font-semibold">
            </div>
            <div>
                <label class="block text-slate-400 font-bold mb-1 uppercase text-[10px]">4-Digit Recovery PIN</label>
                <input type="password" name="recovery_pin" maxlength="4" required placeholder="••••" 
                       class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-white focus:outline-none focus:border-emerald-500 font-semibold">
            </div>
            <div>
                <label class="block text-slate-400 font-bold mb-1 uppercase text-[10px]">New Password</label>
                <input type="password" name="new_password" required placeholder="Enter new password" 
                       class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-white focus:outline-none focus:border-emerald-500 font-semibold">
            </div>

            {% else %}
            <div>
                <label class="block text-slate-400 font-bold mb-1 uppercase text-[10px]">Username or Phone</label>
                <input type="text" name="login_identifier" required placeholder="Enter username or phone" 
                       class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-white focus:outline-none focus:border-emerald-500 font-semibold">
            </div>
            <div>
                <label class="block text-slate-400 font-bold mb-1 uppercase text-[10px]">Password</label>
                <input type="password" name="password" required placeholder="••••••••" 
                       class="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-white focus:outline-none focus:border-emerald-500 font-semibold">
            </div>
            {% endif %}

            <button type="submit" class="w-full bg-emerald-600 hover:bg-emerald-500 text-white font-black py-2.5 rounded-xl transition text-xs shadow-lg mt-2">
                {{ button_text }}
            </button>
        </form>

        <div class="mt-6 pt-4 border-t border-slate-800 text-center text-xs space-y-2">
            {% if mode == 'login' %}
            <div><a href="/forgot-password" class="text-slate-400 hover:text-white">Forgot Password?</a></div>
            <div><span class="text-slate-500">Want to run your shop?</span> <a href="/register-shop" class="text-emerald-400 font-bold hover:underline">Register New Shop</a></div>
            {% elif mode == 'register' %}
            <div><span class="text-slate-500">Already registered?</span> <a href="/login" class="text-emerald-400 font-bold hover:underline">Log In</a></div>
            {% else %}
            <div><a href="/login" class="text-emerald-400 font-bold hover:underline">Back to Login</a></div>
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


@app.route("/api/staff/list", methods=["GET"])
@admin_required
def list_staff():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, role FROM users WHERE shop_id = ? ORDER BY role ASC, username ASC;", (session["shop_id"],))
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
    cursor.execute("UPDATE users SET password_hash = ? WHERE user_id = ? AND shop_id = ? AND role != 'admin'", 
                   (generate_password_hash(new_password), user_id, session["shop_id"]))
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

    # Overall Summary
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

    # Staff Breakdown
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


@app.route("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json", mimetype="application/json")


@app.route("/sw.js")
def service_worker():
    return send_from_directory("static", "sw.js", mimetype="application/javascript")


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))