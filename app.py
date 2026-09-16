import csv
import io
import os
import sqlite3
from flask import Flask, render_template_string, request, jsonify, send_from_directory

app = Flask(__name__)
app.secret_key = "kiosk_pos_enterprise_key"

BASE_DIR = os.environ.get(
    "RENDER_DISK_PATH",
    os.path.abspath(os.path.dirname(__file__))
)
DB_FILE = os.path.join(BASE_DIR, "shop.db")


def init_db():
    os.makedirs(BASE_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS items (
            item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            unit_price REAL NOT NULL DEFAULT 0.0,
            reorder_level INTEGER NOT NULL DEFAULT 5,
            is_active INTEGER NOT NULL DEFAULT 1
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
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

    cursor.execute("DROP VIEW IF EXISTS view_current_stock;")
    cursor.execute("""
        CREATE VIEW view_current_stock AS
        SELECT 
            i.item_id,
            i.name,
            i.unit_price,
            i.reorder_level,
            i.is_active,
            COALESCE(SUM(
                CASE 
                    WHEN t.movement_type = 'IN' THEN t.quantity
                    WHEN t.movement_type = 'OUT' THEN -t.quantity
                    WHEN t.movement_type = 'ADJUSTMENT' THEN t.quantity
                    ELSE 0 
                END
            ), 0) AS current_stock
        FROM items i
        LEFT JOIN transactions t ON i.item_id = t.item_id
        WHERE i.is_active = 1
        GROUP BY i.item_id;
    """)

    conn.commit()
    conn.close()


def get_db():
    init_db()
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


with app.app_context():
    init_db()


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
            theme: {
                extend: {
                    fontFamily: { sans: ['"Plus Jakarta Sans"', 'sans-serif'] }
                }
            }
        }
    </script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body class="bg-slate-50 dark:bg-slate-950 text-slate-900 dark:text-slate-100 min-h-screen font-sans antialiased transition-colors duration-200">

    <!-- Top Navigation -->
    <nav class="sticky top-0 z-40 backdrop-blur-xl bg-white/80 dark:bg-slate-900/80 border-b border-slate-200 dark:border-slate-800/80 px-4 py-3">
        <div class="max-w-3xl mx-auto flex items-center justify-between">
            <div class="flex items-center gap-2.5">
                <img src="/static/app_icon.svg" alt="Logo" class="w-9 h-9 rounded-xl shadow-md">
                <div>
                    <h1 class="text-base font-extrabold tracking-tight text-slate-900 dark:text-white leading-none">Kiosk Track</h1>
                    <span id="connStatus" class="text-[10px] font-medium text-emerald-600 dark:text-emerald-400 tracking-wide flex items-center gap-1 mt-0.5">
                        <span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span> Online
                    </span>
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
                <button onclick="openImportModal()" class="bg-slate-200 dark:bg-slate-800 hover:bg-slate-300 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-200 text-xs font-bold px-2.5 py-2 rounded-xl transition">
                    📂 Import
                </button>
                <button onclick="openAddItemModal()" class="bg-indigo-600 hover:bg-indigo-500 active:scale-95 text-white text-xs font-bold px-3 py-2 rounded-xl shadow transition flex items-center gap-1">
                    <span>+</span> Item
                </button>
            </div>
        </div>
    </nav>

    <!-- Notification Toast -->
    <div id="toast" class="fixed top-4 left-1/2 -translate-x-1/2 z-50 transition-all duration-300 opacity-0 pointer-events-none transform -translate-y-2 max-w-sm w-11/12"></div>

    <main class="max-w-3xl mx-auto px-3 sm:px-4 pt-4 pb-28">

        <!-- SCREEN 1: POS COUNTER -->
        <section id="screen-counter" class="tab-screen">
            <div class="grid grid-cols-3 gap-2.5 mb-4">
                <div class="bg-white dark:bg-slate-900/80 border border-slate-200 dark:border-slate-800 rounded-2xl p-3 shadow-sm">
                    <span class="block text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">Cash</span>
                    <div class="text-base sm:text-lg font-black text-emerald-600 dark:text-emerald-400" id="statCash">KES {{ "{:,.0f}".format(today_cash) }}</div>
                </div>
                <div class="bg-white dark:bg-slate-900/80 border border-slate-200 dark:border-slate-800 rounded-2xl p-3 shadow-sm">
                    <span class="block text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">M-Pesa</span>
                    <div class="text-base sm:text-lg font-black text-green-600 dark:text-green-400" id="statMpesa">KES {{ "{:,.0f}".format(today_mpesa) }}</div>
                </div>
                <div class="bg-white dark:bg-slate-900/80 border border-slate-200 dark:border-slate-800 rounded-2xl p-3 shadow-sm">
                    <span class="block text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">Total Sales</span>
                    <div class="text-base sm:text-lg font-black text-slate-900 dark:text-white" id="statTotal">KES {{ "{:,.0f}".format(today_cash + today_mpesa) }}</div>
                </div>
            </div>

            <!-- Fast Search -->
            <div class="sticky top-[61px] z-30 mb-4">
                <div class="relative shadow-sm rounded-2xl">
                    <div class="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-slate-400">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
                    </div>
                    <input type="text" id="counterSearch" oninput="filterList('counterSearch', '.counter-card')" placeholder="Search items, medicines, spirits..." 
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
                            <button onclick="archiveItem({{ item['item_id'] }}, '{{ item['name'] }}')" title="Archive product" class="text-slate-400 hover:text-rose-500 p-1 text-sm font-bold">&times;</button>
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
                                    class="bg-emerald-600 hover:bg-emerald-500 text-white active:scale-95 px-2.5 py-1.5 rounded-xl text-xs font-bold shadow transition">
                                💵 Cash
                            </button>
                            <button onclick="makeSale({{ item['item_id'] }}, 'MPESA')" 
                                    class="bg-green-600 hover:bg-green-500 text-white active:scale-95 px-2.5 py-1.5 rounded-xl text-xs font-bold shadow transition">
                                📲 M-Pesa
                            </button>
                            <button onclick="openSplitModal({{ item['item_id'] }}, '{{ item['name'] }}', {{ item['unit_price'] }})" 
                                    class="bg-amber-100 dark:bg-amber-950/80 hover:bg-amber-200 dark:hover:bg-amber-900 border border-amber-300 dark:border-amber-800/60 text-amber-800 dark:text-amber-300 active:scale-95 px-2 py-1.5 rounded-xl text-xs font-bold transition">
                                ⚡ Split
                            </button>
                        </div>

                        <div class="flex items-center gap-1.5">
                            <button onclick="reverseSale({{ item['item_id'] }})" 
                                    title="Undo accidental sale"
                                    class="bg-rose-50 dark:bg-rose-950/70 hover:bg-rose-100 dark:hover:bg-rose-900 text-rose-700 dark:text-rose-300 border border-rose-200 dark:border-rose-800/60 active:scale-95 px-2 py-1.5 rounded-xl text-xs font-bold transition">
                                ↩ Return
                            </button>

                            <input type="number" id="restock-qty-{{ item['item_id'] }}" placeholder="+Qty" min="1" 
                                   class="w-12 px-2 py-1.5 bg-slate-100 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl text-center text-xs text-slate-900 dark:text-white focus:outline-none">
                            <button onclick="makeRestock({{ item['item_id'] }})" 
                                    class="bg-slate-200 dark:bg-slate-800 hover:bg-slate-300 dark:hover:bg-slate-700 text-sky-700 dark:text-sky-300 active:scale-95 px-2.5 py-1.5 rounded-xl text-xs font-bold border border-slate-300 dark:border-slate-700 transition">
                                + In
                            </button>
                        </div>
                    </div>
                </div>
                {% endfor %}
            </div>
        </section>

        <!-- SCREEN 2: REPORTS & ANALYTICS -->
        <section id="screen-reports" class="tab-screen hidden">
            <div class="bg-white dark:bg-slate-900/90 border border-slate-200 dark:border-slate-800 rounded-2xl p-4 mb-4 shadow-sm">
                
                <div class="flex flex-wrap items-center justify-between gap-3 mb-4">
                    <div>
                        <h2 class="text-base font-bold text-slate-900 dark:text-white">Sales & Visual Analytics</h2>
                        <p class="text-xs text-slate-500">Live charts and performance metrics</p>
                    </div>
                    <div class="flex items-center gap-1 bg-slate-100 dark:bg-slate-950 p-1 rounded-xl border border-slate-200 dark:border-slate-800 text-xs">
                        <button onclick="setReportRange('today', this)" class="report-range-btn px-2.5 py-1 rounded-lg font-bold bg-emerald-600 text-white">Today</button>
                        <button onclick="setReportRange('week', this)" class="report-range-btn px-2.5 py-1 rounded-lg text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white">7 Days</button>
                        <button onclick="setReportRange('month', this)" class="report-range-btn px-2.5 py-1 rounded-lg text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white">30 Days</button>
                    </div>
                </div>

                <!-- Custom Range Row -->
                <div class="flex flex-wrap items-center gap-2 mb-4 text-xs bg-slate-50 dark:bg-slate-950/60 p-2.5 rounded-xl border border-slate-200 dark:border-slate-800">
                    <span class="text-slate-500 font-bold uppercase text-[10px]">Custom:</span>
                    <input type="date" id="reportStart" class="bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-200 border border-slate-300 dark:border-slate-700 rounded-lg px-2 py-1 text-xs">
                    <span class="text-slate-400">to</span>
                    <input type="date" id="reportEnd" class="bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-200 border border-slate-300 dark:border-slate-700 rounded-lg px-2 py-1 text-xs">
                    <button onclick="fetchCustomReports()" class="bg-indigo-600 hover:bg-indigo-500 text-white font-bold px-3 py-1 rounded-lg transition">Apply</button>
                </div>

                <!-- Metric Cards -->
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-5">
                    <div class="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-xl border border-slate-200 dark:border-slate-800/80">
                        <span class="text-[10px] text-slate-500 uppercase font-bold">Revenue</span>
                        <div id="repTotalRev" class="text-base font-black text-slate-900 dark:text-white">KES 0</div>
                    </div>
                    <div class="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-xl border border-slate-200 dark:border-slate-800/80">
                        <span class="text-[10px] text-slate-500 uppercase font-bold">Units Sold</span>
                        <div id="repTotalUnits" class="text-base font-black text-emerald-600 dark:text-emerald-400">0 pcs</div>
                    </div>
                    <div class="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-xl border border-slate-200 dark:border-slate-800/80">
                        <span class="text-[10px] text-slate-500 uppercase font-bold">Cash</span>
                        <div id="repCash" class="text-base font-black text-emerald-500">KES 0</div>
                    </div>
                    <div class="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-xl border border-slate-200 dark:border-slate-800/80">
                        <span class="text-[10px] text-slate-500 uppercase font-bold">M-Pesa</span>
                        <div id="repMpesa" class="text-base font-black text-green-500">KES 0</div>
                    </div>
                </div>

                <!-- Interactive Charts -->
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
                    <div class="bg-slate-50 dark:bg-slate-950 p-3 rounded-2xl border border-slate-200 dark:border-slate-800 flex flex-col items-center">
                        <h3 class="text-xs font-bold text-slate-700 dark:text-slate-300 uppercase mb-2">Payment Distribution</h3>
                        <div class="w-full h-44 flex items-center justify-center">
                            <canvas id="chartPayment"></canvas>
                        </div>
                    </div>
                    <div class="bg-slate-50 dark:bg-slate-950 p-3 rounded-2xl border border-slate-200 dark:border-slate-800">
                        <h3 class="text-xs font-bold text-slate-700 dark:text-slate-300 uppercase mb-2">Top Selling Items</h3>
                        <div class="w-full h-44">
                            <canvas id="chartTopItems"></canvas>
                        </div>
                    </div>
                </div>

                <!-- Breakdown Lists -->
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-6">
                    <div class="bg-slate-50 dark:bg-slate-950 p-3 rounded-xl border border-slate-200 dark:border-slate-800">
                        <h3 class="text-xs font-bold text-emerald-600 dark:text-emerald-400 mb-2 uppercase flex items-center justify-between">
                            <span>🔥 Top Sellers by Volume</span>
                            <span class="text-[10px] text-slate-400">Units</span>
                        </h3>
                        <ul id="listTopQty" class="divide-y divide-slate-200 dark:divide-slate-800/60 text-xs text-slate-700 dark:text-slate-300"></ul>
                    </div>

                    <div class="bg-slate-50 dark:bg-slate-950 p-3 rounded-xl border border-slate-200 dark:border-slate-800">
                        <h3 class="text-xs font-bold text-indigo-600 dark:text-indigo-400 mb-2 uppercase flex items-center justify-between">
                            <span>💰 Top Earners by Sales</span>
                            <span class="text-[10px] text-slate-400">KES</span>
                        </h3>
                        <ul id="listTopRev" class="divide-y divide-slate-200 dark:divide-slate-800/60 text-xs text-slate-700 dark:text-slate-300"></ul>
                    </div>

                    <div class="bg-slate-50 dark:bg-slate-950 p-3 rounded-xl border border-slate-200 dark:border-slate-800">
                        <h3 class="text-xs font-bold text-rose-600 dark:text-rose-400 mb-2 uppercase flex items-center justify-between">
                            <span>📉 Slowest Moving Items</span>
                            <span class="text-[10px] text-slate-400">Units</span>
                        </h3>
                        <ul id="listLowestQty" class="divide-y divide-slate-200 dark:divide-slate-800/60 text-xs text-slate-700 dark:text-slate-300"></ul>
                    </div>

                    <div class="bg-slate-50 dark:bg-slate-950 p-3 rounded-xl border border-slate-200 dark:border-slate-800">
                        <h3 class="text-xs font-bold text-amber-600 dark:text-amber-400 mb-2 uppercase flex items-center justify-between">
                            <span>⚡ High Volume, Low Value</span>
                            <span class="text-[10px] text-slate-400">Units vs KES</span>
                        </h3>
                        <ul id="listDivergence" class="divide-y divide-slate-200 dark:divide-slate-800/60 text-xs text-slate-700 dark:text-slate-300"></ul>
                    </div>
                </div>

                <!-- Danger Zone: Reset Sales Memory -->
                <div class="border-t border-slate-200 dark:border-slate-800 pt-4 flex items-center justify-between">
                    <div>
                        <span class="text-xs font-bold text-slate-800 dark:text-slate-200">Erase Test Memory</span>
                        <p class="text-[11px] text-slate-500">Wipes all sales transactions, leaving catalog items intact</p>
                    </div>
                    <button onclick="confirmWipeAllSalesHistory()" class="bg-rose-100 dark:bg-rose-950/80 hover:bg-rose-200 dark:hover:bg-rose-900 border border-rose-300 dark:border-rose-800 text-rose-700 dark:text-rose-300 text-xs font-bold px-3 py-1.5 rounded-xl transition">
                        🗑️ Clear Sales History
                    </button>
                </div>

            </div>
        </section>

        <!-- SCREEN 3: PHYSICAL STOCK TAKE & AUDIT -->
        <section id="screen-audit" class="tab-screen hidden">
            <div class="bg-white dark:bg-slate-900/90 border border-slate-200 dark:border-slate-800 rounded-2xl p-4 mb-4 shadow-sm">
                <div class="flex flex-wrap items-center justify-between gap-3 mb-4">
                    <div>
                        <h2 class="text-base font-bold text-slate-900 dark:text-white">Physical Stock Take</h2>
                        <p class="text-xs text-slate-500">Calibrate shelf counts directly</p>
                    </div>
                    <button onclick="confirmResetAllZero()" class="bg-rose-100 dark:bg-rose-950/80 hover:bg-rose-200 dark:hover:bg-rose-900 border border-rose-300 dark:border-rose-800 text-rose-700 dark:text-rose-300 text-xs font-bold px-3 py-1.5 rounded-xl transition flex items-center gap-1">
                        ⚠️ Reset All to 0
                    </button>
                </div>

                <div class="mb-3">
                    <input type="text" id="auditSearch" oninput="filterList('auditSearch', '.audit-row')" placeholder="Search item to calibrate stock..." 
                           class="w-full px-3.5 py-2.5 bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl text-xs text-slate-900 dark:text-white placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-sky-500">
                </div>

                <div class="divide-y divide-slate-100 dark:divide-slate-800/80 max-h-[500px] overflow-y-auto pr-1" id="auditList">
                    {% for item in items %}
                    <div class="audit-row py-2.5 flex items-center justify-between gap-2" data-name="{{ item['name'] }}">
                        <div>
                            <div class="font-bold text-slate-900 dark:text-white text-xs leading-snug">{{ item['name'] }}</div>
                            <span class="text-[11px] text-slate-500">System Count: <b id="audit-sys-{{ item['item_id'] }}">{{ item['current_stock'] }}</b></span>
                        </div>
                        <div class="flex items-center gap-1.5">
                            <input type="number" id="counted-{{ item['item_id'] }}" placeholder="Counted" 
                                   class="w-16 px-2 py-1 bg-slate-100 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-lg text-center text-xs font-bold text-slate-900 dark:text-white focus:outline-none focus:border-sky-500">
                            <button onclick="updateStockTake({{ item['item_id'] }})" 
                                    class="bg-sky-600 hover:bg-sky-500 active:scale-95 text-white font-bold text-xs px-2.5 py-1 rounded-lg transition">
                                Set
                            </button>
                        </div>
                    </div>
                    {% endfor %}
                </div>
            </div>
        </section>

        <!-- ADD ITEM MODAL -->
        <div id="addItemModal" class="hidden fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
            <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl w-full max-w-sm p-5 shadow-2xl space-y-4">
                <div class="flex items-center justify-between border-b border-slate-200 dark:border-slate-800 pb-2.5">
                    <h3 class="text-sm font-bold text-slate-900 dark:text-white">Add New Product</h3>
                    <button onclick="closeAddItemModal()" class="text-slate-400 hover:text-slate-700 dark:hover:text-white text-lg">&times;</button>
                </div>

                <div class="space-y-3 text-xs">
                    <div>
                        <label class="block font-semibold text-slate-700 dark:text-slate-300 mb-1">Product Name & Spec</label>
                        <input type="text" id="newItemName" placeholder="e.g., Viceroy 750ml, Panadol Extra" 
                               class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-300 dark:border-slate-700 rounded-xl px-3 py-2 text-slate-900 dark:text-white placeholder-slate-400 focus:outline-none focus:border-indigo-500">
                    </div>

                    <div class="grid grid-cols-2 gap-2">
                        <div>
                            <label class="block font-semibold text-slate-700 dark:text-slate-300 mb-1">Selling Price (KES)</label>
                            <input type="number" id="newItemPrice" placeholder="50" step="0.5" 
                                   class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-300 dark:border-slate-700 rounded-xl px-3 py-2 text-slate-900 dark:text-white placeholder-slate-400 focus:outline-none focus:border-indigo-500">
                        </div>
                        <div>
                            <label class="block font-semibold text-slate-700 dark:text-slate-300 mb-1">Initial Stock</label>
                            <input type="number" id="newItemStock" placeholder="10" min="0" 
                                   class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-300 dark:border-slate-700 rounded-xl px-3 py-2 text-slate-900 dark:text-white placeholder-slate-400 focus:outline-none focus:border-indigo-500">
                        </div>
                    </div>
                </div>

                <div class="flex items-center justify-end gap-2 pt-2 border-t border-slate-200 dark:border-slate-800">
                    <button onclick="closeAddItemModal()" class="px-3 py-1.5 rounded-xl text-xs font-bold text-slate-500 hover:text-slate-800 dark:hover:text-white">Cancel</button>
                    <button onclick="submitNewItem()" class="bg-indigo-600 hover:bg-indigo-500 active:scale-95 text-white font-bold text-xs px-4 py-1.5 rounded-xl transition">
                        Save Product
                    </button>
                </div>
            </div>
        </div>

        <!-- EXCEL/CSV BULK IMPORT MODAL -->
        <div id="importModal" class="hidden fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
            <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl w-full max-w-sm p-5 shadow-2xl space-y-4">
                <div class="flex items-center justify-between border-b border-slate-200 dark:border-slate-800 pb-2.5">
                    <div>
                        <h3 class="text-sm font-bold text-slate-900 dark:text-white">Bulk Import Catalog</h3>
                        <span class="text-[11px] text-slate-400">Upload CSV from Excel</span>
                    </div>
                    <button onclick="closeImportModal()" class="text-slate-400 hover:text-slate-700 dark:hover:text-white text-lg">&times;</button>
                </div>

                <div class="text-xs space-y-3">
                    <p class="text-slate-500">CSV file must include header columns: <b class="text-slate-700 dark:text-slate-300">name, unit_price, initial_stock</b></p>
                    <input type="file" id="csvFileInput" accept=".csv" class="w-full text-xs text-slate-500 file:mr-2 file:py-1.5 file:px-3 file:rounded-xl file:border-0 file:text-xs file:font-semibold file:bg-indigo-50 dark:file:bg-indigo-950 file:text-indigo-600 dark:file:text-indigo-400">
                </div>

                <div class="flex items-center justify-end gap-2 pt-2 border-t border-slate-200 dark:border-slate-800">
                    <button onclick="closeImportModal()" class="px-3 py-1.5 rounded-xl text-xs font-bold text-slate-500 hover:text-slate-800 dark:hover:text-white">Cancel</button>
                    <button onclick="submitCsvImport()" class="bg-indigo-600 hover:bg-indigo-500 text-white font-bold text-xs px-4 py-1.5 rounded-xl transition">
                        Import CSV
                    </button>
                </div>
            </div>
        </div>

        <!-- SPLIT PAYMENT MODAL -->
        <div id="splitModal" class="hidden fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
            <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl w-full max-w-sm p-5 shadow-2xl space-y-4">
                <div class="flex items-center justify-between border-b border-slate-200 dark:border-slate-800 pb-2.5">
                    <div>
                        <h3 class="text-sm font-bold text-slate-900 dark:text-white" id="splitItemName">Item Name</h3>
                        <span class="text-xs text-emerald-600 dark:text-emerald-400 font-bold" id="splitTotalDisplay">Total: KES 0</span>
                    </div>
                    <button onclick="closeSplitModal()" class="text-slate-400 hover:text-slate-700 dark:hover:text-white text-lg">&times;</button>
                </div>

                <div class="space-y-3 text-xs">
                    <div>
                        <label class="block font-semibold text-slate-700 dark:text-slate-300 mb-1">Cash Received (KES)</label>
                        <input type="number" id="splitCashInput" oninput="autoCalculateMpesa()" placeholder="0" 
                               class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-300 dark:border-slate-700 rounded-xl px-3 py-2 text-slate-900 dark:text-white text-sm font-bold focus:outline-none focus:border-emerald-500">
                    </div>
                    <div>
                        <label class="block font-semibold text-slate-700 dark:text-slate-300 mb-1">M-Pesa Received (KES)</label>
                        <input type="number" id="splitMpesaInput" placeholder="0" 
                               class="w-full bg-slate-50 dark:bg-slate-950 border border-slate-300 dark:border-slate-700 rounded-xl px-3 py-2 text-slate-900 dark:text-white text-sm font-bold focus:outline-none focus:border-green-500">
                    </div>
                </div>

                <div class="flex items-center justify-end gap-2 pt-2 border-t border-slate-200 dark:border-slate-800">
                    <button onclick="closeSplitModal()" class="px-3 py-1.5 rounded-xl text-xs font-bold text-slate-500 hover:text-slate-800 dark:hover:text-white">Cancel</button>
                    <button onclick="submitSplitSale()" class="bg-gradient-to-r from-emerald-600 to-green-600 hover:from-emerald-500 text-white font-bold text-xs px-4 py-2 rounded-xl transition">
                        Complete Sale
                    </button>
                </div>
            </div>
        </div>

    </main>

    <!-- Bottom Navigation -->
    <nav class="fixed bottom-0 left-0 right-0 z-40 bg-white/95 dark:bg-slate-900/95 backdrop-blur-xl border-t border-slate-200 dark:border-slate-800/90 pb-[env(safe-area-inset-bottom)]">
        <div class="max-w-md mx-auto grid grid-cols-3 h-16 relative">
            
            <button onclick="switchTab('counter', this)" class="nav-tab flex flex-col items-center justify-center gap-1 text-emerald-600 dark:text-emerald-400 active:scale-95 transition-all">
                <div class="w-10 h-7 rounded-full flex items-center justify-center transition-all bg-emerald-100 dark:bg-emerald-950/80 border border-emerald-300 dark:border-emerald-800/60 shadow-sm tab-indicator">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M16 11V7a4 4 0 00-8 0v4M5 9h14l1 12H4L5 9z"/></svg>
                </div>
                <span class="text-[11px] font-bold tracking-tight">Counter</span>
            </button>

            <button onclick="switchTab('reports', this)" class="nav-tab flex flex-col items-center justify-center gap-1 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 active:scale-95 transition-all">
                <div class="w-10 h-7 rounded-full flex items-center justify-center transition-all bg-transparent border border-transparent tab-indicator">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>
                </div>
                <span class="text-[11px] font-bold tracking-tight">Reports</span>
            </button>

            <button onclick="switchTab('audit', this)" class="nav-tab flex flex-col items-center justify-center gap-1 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 active:scale-95 transition-all">
                <div class="w-10 h-7 rounded-full flex items-center justify-center transition-all bg-transparent border border-transparent tab-indicator">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"/></svg>
                </div>
                <span class="text-[11px] font-bold tracking-tight">Stock Take</span>
            </button>

        </div>
    </nav>

    <!-- Client Script -->
    <script>
        // PWA Native Install Event Handler
        let deferredPrompt = null;
        const installBtn = document.getElementById('directInstallBtn');

        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/sw.js', { scope: '/' })
                .then(reg => console.log('SW Registered:', reg.scope))
                .catch(err => console.error('SW Registration Failed:', err));
        }

        window.addEventListener('beforeinstallprompt', (e) => {
            e.preventDefault();
            deferredPrompt = e;
            if (installBtn) installBtn.classList.remove('hidden');
        });

        async function triggerNativeInstall() {
            if (!deferredPrompt) {
                alert("If prompt does not show, open browser menu (⋮) -> 'Add to Home screen'.");
                return;
            }
            deferredPrompt.prompt();
            const { outcome } = await deferredPrompt.userChoice;
            if (outcome === 'accepted') {
                if (installBtn) installBtn.classList.add('hidden');
            }
            deferredPrompt = null;
        }

        window.addEventListener('appinstalled', () => {
            if (installBtn) installBtn.classList.add('hidden');
            showToast("Kiosk Track installed successfully! Check home screen.");
        });

        // Offline IndexedDB Engine
        let dbPromise = indexedDB.open('KioskOfflineQueue', 1);
        dbPromise.onupgradeneeded = (e) => {
            let db = e.target.result;
            if (!db.objectStoreNames.contains('pending_sales')) {
                db.createObjectStore('pending_sales', { autoIncrement: true });
            }
        };

        function queueOfflineSale(payload) {
            let request = indexedDB.open('KioskOfflineQueue', 1);
            request.onsuccess = (e) => {
                let db = e.target.result;
                let tx = db.transaction('pending_sales', 'readwrite');
                tx.objectStore('pending_sales').add(payload);
            };
        }

        async function syncOfflineSales() {
            let request = indexedDB.open('KioskOfflineQueue', 1);
            request.onsuccess = (e) => {
                let db = e.target.result;
                let tx = db.transaction('pending_sales', 'readwrite');
                let store = tx.objectStore('pending_sales');
                let getAll = store.openCursor();

                getAll.onsuccess = async (ev) => {
                    let cursor = ev.target.result;
                    if (cursor) {
                        const item = cursor.value;
                        try {
                            const res = await fetch('/api/sale', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify(item)
                            });
                            if (res.ok) cursor.delete();
                        } catch(err) {}
                        cursor.continue();
                    }
                };
            };
        }

        window.addEventListener('online', () => {
            document.getElementById('connStatus').innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span> Online';
            syncOfflineSales();
        });
        window.addEventListener('offline', () => {
            document.getElementById('connStatus').innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-amber-500"></span> Offline Mode';
        });

        // Theme Management
        function applySavedTheme() {
            const isDark = localStorage.getItem('kiosk_theme') !== 'light';
            if (isDark) {
                document.documentElement.classList.add('dark');
                document.getElementById('themeIcon').innerText = '🌙';
            } else {
                document.documentElement.classList.remove('dark');
                document.getElementById('themeIcon').innerText = '☀️';
            }
        }
        applySavedTheme();

        function toggleTheme() {
            const isDark = document.documentElement.classList.toggle('dark');
            localStorage.setItem('kiosk_theme', isDark ? 'dark' : 'light');
            document.getElementById('themeIcon').innerText = isDark ? '🌙' : '☀️';
            if (chartPaymentInstance) renderChartsWithTheme();
        }

        function switchTab(screenName, btnEl) {
            document.querySelectorAll('.tab-screen').forEach(el => el.classList.add('hidden'));
            const target = document.getElementById(`screen-${screenName}`);
            if (target) {
                target.classList.remove('hidden');
                window.scrollTo({ top: 0, behavior: 'smooth' });
            }

            document.querySelectorAll('.nav-tab').forEach(tab => {
                tab.classList.remove('text-emerald-600', 'dark:text-emerald-400');
                tab.classList.add('text-slate-400');
                const ind = tab.querySelector('.tab-indicator');
                ind.className = 'w-10 h-7 rounded-full flex items-center justify-center transition-all bg-transparent border border-transparent tab-indicator';
            });

            btnEl.classList.remove('text-slate-400');
            btnEl.classList.add('text-emerald-600', 'dark:text-emerald-400');
            const activeInd = btnEl.querySelector('.tab-indicator');
            activeInd.className = 'w-10 h-7 rounded-full flex items-center justify-center transition-all bg-emerald-100 dark:bg-emerald-950/80 border border-emerald-300 dark:border-emerald-800/60 shadow-sm tab-indicator';

            if (screenName === 'reports') {
                loadReports('today');
            }
        }

        function filterList(inputId, rowSelector) {
            const input = document.getElementById(inputId).value.toLowerCase().trim();
            document.querySelectorAll(rowSelector).forEach(row => {
                const name = row.getAttribute('data-name').toLowerCase();
                row.style.display = name.includes(input) ? '' : 'none';
            });
        }

        function adjustQty(id, delta) {
            const el = document.getElementById(id);
            let val = parseInt(el.value) || 1;
            el.value = Math.max(1, val + delta);
        }

        function showToast(message, isSuccess = true) {
            const toast = document.getElementById('toast');
            toast.className = `fixed top-4 left-1/2 -translate-x-1/2 z-50 p-3 rounded-2xl text-xs font-bold shadow-2xl flex items-center gap-2 border transition-all duration-300 max-w-sm w-11/12 ${
                isSuccess 
                ? 'bg-emerald-900/90 text-emerald-200 border-emerald-700 backdrop-blur' 
                : 'bg-rose-900/90 text-rose-200 border-rose-700 backdrop-blur'
            }`;
            toast.innerHTML = `<span>${isSuccess ? '✓' : '⚠'}</span> <span>${message}</span>`;
            toast.classList.remove('opacity-0', '-translate-y-2');
            toast.classList.add('opacity-100', 'translate-y-0');

            setTimeout(() => {
                toast.classList.add('opacity-0', '-translate-y-2');
                toast.classList.remove('opacity-100', 'translate-y-0');
            }, 2500);
        }

        // Sale API
        async function makeSale(itemId, payment) {
            const qtyInput = document.getElementById(`qty-${itemId}`);
            const qty = parseInt(qtyInput.value) || 1;
            const payload = { item_id: itemId, quantity: qty, payment_method: payment };

            if (!navigator.onLine) {
                queueOfflineSale(payload);
                const valEl = document.getElementById(`stock-val-${itemId}`);
                valEl.innerText = (parseInt(valEl.innerText) || 0) - qty;
                showToast(`Offline: Sale queued for sync`, true);
                qtyInput.value = 1;
                return;
            }

            try {
                const res = await fetch('/api/sale', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (!res.ok) {
                    showToast(data.error || 'Sale failed', false);
                    return;
                }

                updateStockUI(itemId, data.new_stock, data.reorder_level);
                document.getElementById('statCash').innerText = `KES ${Math.round(data.today_cash).toLocaleString()}`;
                document.getElementById('statMpesa').innerText = `KES ${Math.round(data.today_mpesa).toLocaleString()}`;
                document.getElementById('statTotal').innerText = `KES ${Math.round(data.today_cash + data.today_mpesa).toLocaleString()}`;

                showToast(`Sold ${qty}x ${data.item_name} via ${payment}`);
                qtyInput.value = 1;
            } catch (err) {
                queueOfflineSale(payload);
                showToast('Network drop: Queued in offline storage', true);
            }
        }

        // Reversal API
        async function reverseSale(itemId) {
            const qtyInput = document.getElementById(`qty-${itemId}`);
            const qty = parseInt(qtyInput.value) || 1;

            if (!confirm(`Reverse sale of ${qty} unit(s)? This will return item to stock and refund today's totals.`)) {
                return;
            }

            try {
                const res = await fetch('/api/sale/reverse', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ item_id: itemId, quantity: qty, payment_method: 'CASH' })
                });
                const data = await res.json();
                if (!res.ok) {
                    showToast(data.error || 'Reversal failed', false);
                    return;
                }

                updateStockUI(itemId, data.new_stock, data.reorder_level);
                document.getElementById('statCash').innerText = `KES ${Math.round(data.today_cash).toLocaleString()}`;
                document.getElementById('statMpesa').innerText = `KES ${Math.round(data.today_mpesa).toLocaleString()}`;
                document.getElementById('statTotal').innerText = `KES ${Math.round(data.today_cash + data.today_mpesa).toLocaleString()}`;

                showToast(`↩ Returned ${qty}x ${data.item_name} to stock`);
            } catch (err) {
                showToast('Connection error', false);
            }
        }

        // Restock API
        async function makeRestock(itemId) {
            const restockInput = document.getElementById(`restock-qty-${itemId}`);
            const qty = parseInt(restockInput.value);
            if (!qty || qty <= 0) {
                showToast('Enter quantity to restock', false);
                return;
            }

            try {
                const res = await fetch('/api/restock', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ item_id: itemId, quantity: qty })
                });
                const data = await res.json();
                if (!res.ok) {
                    showToast(data.error || 'Restock failed', false);
                    return;
                }

                updateStockUI(itemId, data.new_stock, data.reorder_level);
                showToast(`Stock updated: now ${data.new_stock} pcs on shelf`);
                restockInput.value = '';
            } catch (err) {
                showToast('Connection error', false);
            }
        }

        // Soft Delete / Archive Item API
        async function archiveItem(itemId, itemName) {
            if (!confirm(`Discontinue '${itemName}'? It will be removed from your active shelf without losing past sales records.`)) {
                return;
            }

            try {
                const res = await fetch(`/api/items/archive/${itemId}`, { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    document.getElementById(`item-card-${itemId}`)?.remove();
                    showToast(`Archived ${itemName}`);
                }
            } catch (err) {
                showToast('Failed to archive product', false);
            }
        }

        function updateStockUI(itemId, currentStock, reorderLevel) {
            const valEl = document.getElementById(`stock-val-${itemId}`);
            const badgeEl = document.getElementById(`badge-${itemId}`);
            if (valEl) valEl.innerText = currentStock;

            const auditSys = document.getElementById(`audit-sys-${itemId}`);
            if (auditSys) auditSys.innerText = currentStock;

            if (badgeEl) {
                badgeEl.className = 'px-2.5 py-1 rounded-full text-xs font-bold ';
                if (currentStock <= 0) {
                    badgeEl.className += 'bg-rose-100 dark:bg-rose-950 text-rose-700 dark:text-rose-400 border border-rose-300 dark:border-rose-800';
                } else if (currentStock <= reorderLevel) {
                    badgeEl.className += 'bg-amber-100 dark:bg-amber-950 text-amber-700 dark:text-amber-300 border border-amber-300 dark:border-amber-800';
                } else {
                    badgeEl.className += 'bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-200 border border-slate-300 dark:border-slate-700';
                }
            }
        }

        // Split Payment Functions
        let activeSplitItemId = null;
        let activeSplitTotalPrice = 0;
        let activeSplitQty = 1;

        function openSplitModal(itemId, name, unitPrice) {
            activeSplitItemId = itemId;
            const qtyInput = document.getElementById(`qty-${itemId}`);
            activeSplitQty = parseInt(qtyInput ? qtyInput.value : 1) || 1;
            activeSplitTotalPrice = unitPrice * activeSplitQty;

            document.getElementById('splitItemName').innerText = `${activeSplitQty}x ${name}`;
            document.getElementById('splitTotalDisplay').innerText = `Total Due: KES ${activeSplitTotalPrice.toLocaleString()}`;
            document.getElementById('splitCashInput').value = '';
            document.getElementById('splitMpesaInput').value = activeSplitTotalPrice;
            document.getElementById('splitModal').classList.remove('hidden');
            document.getElementById('splitCashInput').focus();
        }

        function autoCalculateMpesa() {
            const cash = parseFloat(document.getElementById('splitCashInput').value) || 0;
            const remainder = Math.max(0, activeSplitTotalPrice - cash);
            document.getElementById('splitMpesaInput').value = remainder;
        }

        function closeSplitModal() {
            document.getElementById('splitModal').classList.add('hidden');
            activeSplitItemId = null;
        }

        async function submitSplitSale() {
            const cash = parseFloat(document.getElementById('splitCashInput').value) || 0;
            const mpesa = parseFloat(document.getElementById('splitMpesaInput').value) || 0;

            if (Math.round(cash + mpesa) !== Math.round(activeSplitTotalPrice)) {
                showToast(`Sum must equal KES ${activeSplitTotalPrice}`, false);
                return;
            }

            try {
                const res = await fetch('/api/sale/split', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        item_id: activeSplitItemId,
                        quantity: activeSplitQty,
                        cash_amount: cash,
                        mpesa_amount: mpesa
                    })
                });
                const data = await res.json();
                if (!res.ok) {
                    showToast(data.error || 'Sale failed', false);
                    return;
                }

                updateStockUI(activeSplitItemId, data.new_stock, data.reorder_level);
                document.getElementById('statCash').innerText = `KES ${Math.round(data.today_cash).toLocaleString()}`;
                document.getElementById('statMpesa').innerText = `KES ${Math.round(data.today_mpesa).toLocaleString()}`;
                document.getElementById('statTotal').innerText = `KES ${Math.round(data.today_cash + data.today_mpesa).toLocaleString()}`;

                showToast(`Split Sale completed`);
                closeSplitModal();
            } catch (err) {
                showToast("Connection error", false);
            }
        }

        // Reports & Interactive Charts
        let chartPaymentInstance = null;
        let chartTopItemsInstance = null;
        let lastReportData = null;

        function setReportRange(range, btnEl) {
            document.querySelectorAll('.report-range-btn').forEach(b => {
                b.className = 'report-range-btn px-2.5 py-1 rounded-lg text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white';
            });
            btnEl.className = 'report-range-btn px-2.5 py-1 rounded-lg font-bold bg-emerald-600 text-white';
            loadReports(range);
        }

        async function loadReports(range) {
            try {
                const res = await fetch(`/api/reports?range=${range}`);
                const data = await res.json();
                lastReportData = data;
                renderReportDashboard(data);
            } catch (err) {
                showToast('Could not load reports', false);
            }
        }

        async function fetchCustomReports() {
            const start = document.getElementById('reportStart').value;
            const end = document.getElementById('reportEnd').value;
            if (!start || !end) {
                showToast('Select start and end dates', false);
                return;
            }

            try {
                const res = await fetch(`/api/reports?range=custom&start_date=${start}&end_date=${end}`);
                const data = await res.json();
                lastReportData = data;
                renderReportDashboard(data);
            } catch (err) {
                showToast('Could not load custom report', false);
            }
        }

        function renderReportDashboard(data) {
            document.getElementById('repTotalRev').innerText = `KES ${Math.round(data.summary.total_revenue).toLocaleString()}`;
            document.getElementById('repTotalUnits').innerText = `${data.summary.total_units} pcs`;
            document.getElementById('repCash').innerText = `KES ${Math.round(data.summary.cash).toLocaleString()}`;
            document.getElementById('repMpesa').innerText = `KES ${Math.round(data.summary.mpesa).toLocaleString()}`;

            const renderList = (id, items, isVal) => {
                const el = document.getElementById(id);
                if (!items || items.length === 0) {
                    el.innerHTML = '<li class="py-2 text-slate-400 text-center">No transactions recorded</li>';
                    return;
                }
                el.innerHTML = items.map((it, idx) => `
                    <li class="py-1.5 flex justify-between items-center">
                        <span class="truncate pr-2">${idx + 1}. ${it.name}</span>
                        <span class="font-bold font-mono ${isVal ? 'text-indigo-600 dark:text-indigo-300' : 'text-emerald-600 dark:text-emerald-400'}">
                            ${isVal ? 'KES ' + Math.round(it.total_sales_val).toLocaleString() : it.total_units_sold + ' pcs'}
                        </span>
                    </li>
                `).join('');
            };

            renderList('listTopQty', data.top_qty, false);
            renderList('listTopRev', data.top_revenue, true);
            renderList('listLowestQty', data.lowest_qty, false);
            renderList('listDivergence', data.high_qty_low_rev, false);

            renderInteractiveCharts(data);
        }

        function renderInteractiveCharts(data) {
            const isDark = document.documentElement.classList.contains('dark');
            const textColor = isDark ? '#94a3b8' : '#475569';

            const ctxPay = document.getElementById('chartPayment').getContext('2d');
            if (chartPaymentInstance) chartPaymentInstance.destroy();

            const cashVal = Math.max(0, data.summary.cash);
            const mpesaVal = Math.max(0, data.summary.mpesa);

            chartPaymentInstance = new Chart(ctxPay, {
                type: 'doughnut',
                data: {
                    labels: ['Cash', 'M-Pesa'],
                    datasets: [{
                        data: (cashVal === 0 && mpesaVal === 0) ? [1] : [cashVal, mpesaVal],
                        backgroundColor: (cashVal === 0 && mpesaVal === 0) ? ['#334155'] : ['#10b981', '#22c55e'],
                        borderWidth: 0
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { position: 'bottom', labels: { color: textColor, font: { size: 10, weight: 'bold' } } }
                    },
                    cutout: '70%'
                }
            });

            const ctxTop = document.getElementById('chartTopItems').getContext('2d');
            if (chartTopItemsInstance) chartTopItemsInstance.destroy();

            const top5 = (data.top_qty || []).slice(0, 5);
            chartTopItemsInstance = new Chart(ctxTop, {
                type: 'bar',
                data: {
                    labels: top5.map(i => i.name.length > 14 ? i.name.substring(0, 12) + '..' : i.name),
                    datasets: [{
                        label: 'Units Sold',
                        data: top5.map(i => i.total_units_sold),
                        backgroundColor: '#6366f1',
                        borderRadius: 6
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    indexAxis: 'y',
                    scales: {
                        x: { ticks: { color: textColor, stepSize: 1 }, grid: { display: false } },
                        y: { ticks: { color: textColor, font: { size: 10 } }, grid: { display: false } }
                    },
                    plugins: {
                        legend: { display: false }
                    }
                }
            });
        }

        function renderChartsWithTheme() {
            if (lastReportData) renderInteractiveCharts(lastReportData);
        }

        // Wipe Sales Ledger Memory
        async function confirmWipeAllSalesHistory() {
            const promptVal = prompt("Type 'CLEAR' to erase all sales history and restore cash/totals to KES 0:");
            if (promptVal !== 'CLEAR') {
                showToast("Action cancelled", false);
                return;
            }

            try {
                const res = await fetch('/api/admin/clear-all-transactions', { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    showToast("All historical transactions cleared!");
                    setTimeout(() => location.reload(), 800);
                }
            } catch (err) {
                showToast("Failed to erase memory", false);
            }
        }

        // Stock Take API
        async function confirmResetAllZero() {
            if (!confirm("Reset all current stock numbers to 0 for a physical stock-take? Sales history will be preserved.")) {
                return;
            }

            try {
                const res = await fetch('/api/stocktake/reset-all-zero', { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    showToast("All items set to 0. Enter your shelf counts.");
                    document.querySelectorAll('[id^="stock-val-"]').forEach(el => el.innerText = '0');
                    document.querySelectorAll('[id^="audit-sys-"]').forEach(el => el.innerText = '0');
                }
            } catch (err) {
                showToast("Failed to reset inventory", false);
            }
        }

        async function updateStockTake(itemId) {
            const countedInput = document.getElementById(`counted-${itemId}`);
            const countVal = countedInput.value;
            if (countVal === '' || isNaN(parseInt(countVal))) {
                showToast('Enter valid physical count', false);
                return;
            }

            try {
                const res = await fetch('/api/stocktake/update-count', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ item_id: itemId, counted_quantity: parseInt(countVal) })
                });
                const data = await res.json();
                if (data.success) {
                    updateStockUI(itemId, data.new_stock, 5);
                    showToast(`Updated ${data.item_name} to ${data.new_stock} pcs`);
                    countedInput.value = '';
                } else {
                    showToast(data.error || 'Failed to update count', false);
                }
            } catch (err) {
                showToast('Failed to save stock take count', false);
            }
        }

        // Add Single Item Modal
        function openAddItemModal() {
            document.getElementById('addItemModal').classList.remove('hidden');
            document.getElementById('newItemName').focus();
        }

        function closeAddItemModal() {
            document.getElementById('addItemModal').classList.add('hidden');
            document.getElementById('newItemName').value = '';
            document.getElementById('newItemPrice').value = '';
            document.getElementById('newItemStock').value = '';
        }

        async function submitNewItem() {
            const name = document.getElementById('newItemName').value.trim();
            const price = parseFloat(document.getElementById('newItemPrice').value);
            const stock = parseInt(document.getElementById('newItemStock').value) || 0;

            if (!name || isNaN(price) || price < 0) {
                showToast("Please provide valid name and price", false);
                return;
            }

            try {
                const res = await fetch('/api/items/add', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name, unit_price: price, initial_stock: stock })
                });
                const data = await res.json();

                if (!res.ok) {
                    showToast(data.error || 'Failed to add item', false);
                    return;
                }

                showToast(`Added ${data.name} (KES ${data.unit_price})`);
                closeAddItemModal();
                setTimeout(() => location.reload(), 600);
            } catch (err) {
                showToast("Network error while adding item", false);
            }
        }

        // CSV Import Modal
        function openImportModal() {
            document.getElementById('importModal').classList.remove('hidden');
        }

        function closeImportModal() {
            document.getElementById('importModal').classList.add('hidden');
            document.getElementById('csvFileInput').value = '';
        }

        async function submitCsvImport() {
            const fileInput = document.getElementById('csvFileInput');
            if (!fileInput.files || fileInput.files.length === 0) {
                showToast("Select a CSV file first", false);
                return;
            }

            const formData = new FormData();
            formData.append('file', fileInput.files[0]);

            try {
                const res = await fetch('/api/items/import-csv', {
                    method: 'POST',
                    body: formData
                });
                const data = await res.json();
                if (data.success) {
                    showToast(`Successfully imported ${data.imported_count} products!`);
                    closeImportModal();
                    setTimeout(() => location.reload(), 800);
                } else {
                    showToast(data.error || 'Import failed', false);
                }
            } catch (err) {
                showToast('Error uploading CSV file', false);
            }
        }
    </script>
</body>
</html>
"""


@app.route("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json", mimetype="application/json")


@app.route("/sw.js")
def service_worker():
    return send_from_directory("static", "sw.js", mimetype="application/javascript")


@app.route("/")
def index():
    init_db()
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM view_current_stock ORDER BY name ASC;")
    items = cursor.fetchall()

    cursor.execute("""
        SELECT 
            COALESCE(SUM(CASE WHEN payment_method = 'CASH' THEN total_amount ELSE 0 END), 0) as cash_total,
            COALESCE(SUM(CASE WHEN payment_method = 'MPESA' THEN total_amount ELSE 0 END), 0) as mpesa_total
        FROM transactions
        WHERE DATE(timestamp, 'localtime') = DATE('now', 'localtime');
    """)
    totals = cursor.fetchone()
    conn.close()

    return render_template_string(
        HTML_TEMPLATE,
        items=items,
        today_cash=totals["cash_total"],
        today_mpesa=totals["mpesa_total"]
    )


@app.route("/api/sale", methods=["POST"])
def api_sale():
    init_db()
    data = request.get_json() or {}
    item_id = int(data.get("item_id", 0))
    qty = int(data.get("quantity", 1))
    payment = data.get("payment_method", "CASH")

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT name, unit_price, current_stock, reorder_level FROM view_current_stock WHERE item_id = ?", (item_id,))
    item = cursor.fetchone()

    if not item:
        conn.close()
        return jsonify({"error": "Item not found"}), 404

    total = qty * item["unit_price"]
    cursor.execute("""
        INSERT INTO transactions (item_id, movement_type, payment_method, quantity, unit_price, total_amount)
        VALUES (?, 'OUT', ?, ?, ?, ?)
    """, (item_id, payment, qty, item["unit_price"], total))

    cursor.execute("SELECT current_stock FROM view_current_stock WHERE item_id = ?", (item_id,))
    new_stock = cursor.fetchone()["current_stock"]

    cursor.execute("""
        SELECT 
            COALESCE(SUM(CASE WHEN payment_method = 'CASH' THEN total_amount ELSE 0 END), 0) as cash_total,
            COALESCE(SUM(CASE WHEN payment_method = 'MPESA' THEN total_amount ELSE 0 END), 0) as mpesa_total
        FROM transactions
        WHERE DATE(timestamp, 'localtime') = DATE('now', 'localtime');
    """)
    totals = cursor.fetchone()

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "item_name": item["name"],
        "total": total,
        "new_stock": new_stock,
        "reorder_level": item["reorder_level"],
        "today_cash": totals["cash_total"],
        "today_mpesa": totals["mpesa_total"]
    })


@app.route("/api/sale/split", methods=["POST"])
def api_sale_split():
    init_db()
    data = request.get_json() or {}
    item_id = int(data.get("item_id", 0))
    qty = int(data.get("quantity", 1))
    cash_amount = float(data.get("cash_amount", 0.0))
    mpesa_amount = float(data.get("mpesa_amount", 0.0))

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT name, unit_price, current_stock, reorder_level FROM view_current_stock WHERE item_id = ?", (item_id,))
    item = cursor.fetchone()

    if not item:
        conn.close()
        return jsonify({"error": "Item not found"}), 404

    expected_total = qty * item["unit_price"]
    if round(cash_amount + mpesa_amount, 2) != round(expected_total, 2):
        conn.close()
        return jsonify({"error": f"Sum must equal KES {expected_total}"}), 400

    cursor.execute("""
        INSERT INTO transactions (item_id, movement_type, payment_method, quantity, unit_price, total_amount)
        VALUES (?, 'OUT', 'CASH', ?, ?, ?)
    """, (item_id, qty, item["unit_price"], cash_amount))

    cursor.execute("""
        INSERT INTO transactions (item_id, movement_type, payment_method, quantity, unit_price, total_amount)
        VALUES (?, 'OUT', 'MPESA', 0, ?, ?)
    """, (item_id, item["unit_price"], mpesa_amount))

    cursor.execute("SELECT current_stock FROM view_current_stock WHERE item_id = ?", (item_id,))
    new_stock = cursor.fetchone()["current_stock"]

    cursor.execute("""
        SELECT 
            COALESCE(SUM(CASE WHEN payment_method = 'CASH' THEN total_amount ELSE 0 END), 0) as cash_total,
            COALESCE(SUM(CASE WHEN payment_method = 'MPESA' THEN total_amount ELSE 0 END), 0) as mpesa_total
        FROM transactions
        WHERE DATE(timestamp, 'localtime') = DATE('now', 'localtime');
    """)
    totals = cursor.fetchone()

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "item_name": item["name"],
        "new_stock": new_stock,
        "reorder_level": item["reorder_level"],
        "today_cash": totals["cash_total"],
        "today_mpesa": totals["mpesa_total"]
    })


@app.route("/api/sale/reverse", methods=["POST"])
def api_sale_reverse():
    init_db()
    data = request.get_json() or {}
    item_id = int(data.get("item_id", 0))
    qty = int(data.get("quantity", 1))
    payment = data.get("payment_method", "CASH")

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT name, unit_price, current_stock, reorder_level FROM view_current_stock WHERE item_id = ?", (item_id,))
    item = cursor.fetchone()

    if not item:
        conn.close()
        return jsonify({"error": "Item not found"}), 404

    total = qty * item["unit_price"]
    cursor.execute("""
        INSERT INTO transactions (item_id, movement_type, payment_method, quantity, unit_price, total_amount)
        VALUES (?, 'IN', ?, ?, ?, ?)
    """, (item_id, payment, qty, item["unit_price"], -total))

    cursor.execute("SELECT current_stock FROM view_current_stock WHERE item_id = ?", (item_id,))
    new_stock = cursor.fetchone()["current_stock"]

    cursor.execute("""
        SELECT 
            COALESCE(SUM(CASE WHEN payment_method = 'CASH' THEN total_amount ELSE 0 END), 0) as cash_total,
            COALESCE(SUM(CASE WHEN payment_method = 'MPESA' THEN total_amount ELSE 0 END), 0) as mpesa_total
        FROM transactions
        WHERE DATE(timestamp, 'localtime') = DATE('now', 'localtime');
    """)
    totals = cursor.fetchone()

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "item_name": item["name"],
        "new_stock": new_stock,
        "reorder_level": item["reorder_level"],
        "today_cash": totals["cash_total"],
        "today_mpesa": totals["mpesa_total"]
    })


@app.route("/api/restock", methods=["POST"])
def api_restock():
    init_db()
    data = request.get_json() or {}
    item_id = int(data.get("item_id", 0))
    qty = int(data.get("quantity", 0))

    if qty <= 0:
        return jsonify({"error": "Restock quantity must be positive"}), 400

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT name, unit_price, current_stock, reorder_level FROM view_current_stock WHERE item_id = ?", (item_id,))
    item = cursor.fetchone()

    if not item:
        conn.close()
        return jsonify({"error": "Item not found"}), 404

    if item["current_stock"] < 0:
        deficit = abs(item["current_stock"])
        cursor.execute("""
            INSERT INTO transactions (item_id, movement_type, payment_method, quantity, unit_price, total_amount)
            VALUES (?, 'ADJUSTMENT', 'N/A', ?, ?, 0.0)
        """, (item_id, deficit, item["unit_price"]))

    total = qty * item["unit_price"]
    cursor.execute("""
        INSERT INTO transactions (item_id, movement_type, payment_method, quantity, unit_price, total_amount)
        VALUES (?, 'IN', 'N/A', ?, ?, ?)
    """, (item_id, qty, item["unit_price"], total))

    cursor.execute("SELECT current_stock FROM view_current_stock WHERE item_id = ?", (item_id,))
    new_stock = cursor.fetchone()["current_stock"]

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "item_name": item["name"],
        "new_stock": new_stock,
        "reorder_level": item["reorder_level"]
    })


@app.route("/api/items/add", methods=["POST"])
def add_new_item():
    init_db()
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    try:
        unit_price = float(data.get("unit_price", 0))
        initial_stock = int(data.get("initial_stock", 0))
        reorder_level = int(data.get("reorder_level", 5))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid numbers provided"}), 400

    if not name:
        return jsonify({"error": "Item name cannot be empty"}), 400

    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO items (name, unit_price, reorder_level, is_active)
            VALUES (?, ?, ?, 1)
        """, (name, unit_price, reorder_level))
        item_id = cursor.lastrowid

        if initial_stock > 0:
            cursor.execute("""
                INSERT INTO transactions (item_id, movement_type, payment_method, quantity, unit_price, total_amount)
                VALUES (?, 'IN', 'N/A', ?, ?, ?)
            """, (item_id, initial_stock, unit_price, initial_stock * unit_price))

        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": f"An item named '{name}' already exists"}), 400

    conn.close()
    return jsonify({
        "success": True,
        "item_id": item_id,
        "name": name,
        "unit_price": unit_price,
        "current_stock": initial_stock
    })


@app.route("/api/items/archive/<int:item_id>", methods=["POST"])
def archive_product(item_id):
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE items SET is_active = 0 WHERE item_id = ?", (item_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "Product archived from active view."})


@app.route("/api/items/import-csv", methods=["POST"])
def import_csv_catalog():
    init_db()
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["file"]
    
    stream = io.StringIO(file.stream.read().decode("utf-8-sig"), newline=None)
    reader = csv.DictReader(stream)

    conn = get_db()
    cursor = conn.cursor()
    imported_count = 0

    for row in reader:
        row_clean = {k.strip().lower(): v for k, v in row.items() if k}
        name = row_clean.get("name", "").strip()
        try:
            price = float(row_clean.get("unit_price", 0.0))
            stock = int(row_clean.get("initial_stock", 0))
        except (ValueError, TypeError):
            continue

        if not name:
            continue

        try:
            cursor.execute("INSERT INTO items (name, unit_price, is_active) VALUES (?, ?, 1)", (name, price))
            new_id = cursor.lastrowid
            if stock > 0:
                cursor.execute("""
                    INSERT INTO transactions (item_id, movement_type, payment_method, quantity, unit_price, total_amount)
                    VALUES (?, 'IN', 'N/A', ?, ?, ?)
                """, (new_id, stock, price, stock * price))
            imported_count += 1
        except sqlite3.IntegrityError:
            pass

    conn.commit()
    conn.close()
    return jsonify({"success": True, "imported_count": imported_count})


@app.route("/api/stocktake/reset-all-zero", methods=["POST"])
def reset_all_zero():
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT item_id, current_stock, unit_price FROM view_current_stock WHERE current_stock != 0;")
    rows = cursor.fetchall()

    for r in rows:
        diff = -r["current_stock"]
        cursor.execute("""
            INSERT INTO transactions (item_id, movement_type, payment_method, quantity, unit_price, total_amount)
            VALUES (?, 'ADJUSTMENT', 'N/A', ?, ?, 0.0)
        """, (r["item_id"], diff, r["unit_price"]))

    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "All item stocks set to 0"})


@app.route("/api/stocktake/update-count", methods=["POST"])
def update_stock_count():
    init_db()
    data = request.get_json() or {}
    item_id = int(data.get("item_id", 0))
    counted_qty = int(data.get("counted_quantity", 0))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT current_stock, unit_price, name FROM view_current_stock WHERE item_id = ?", (item_id,))
    item = cursor.fetchone()

    if not item:
        conn.close()
        return jsonify({"error": "Item not found"}), 404

    diff = counted_qty - item["current_stock"]
    if diff != 0:
        cursor.execute("""
            INSERT INTO transactions (item_id, movement_type, payment_method, quantity, unit_price, total_amount)
            VALUES (?, 'ADJUSTMENT', 'N/A', ?, ?, 0.0)
        """, (item_id, diff, item["unit_price"]))
        conn.commit()

    conn.close()
    return jsonify({"success": True, "item_name": item["name"], "new_stock": counted_qty})


@app.route("/api/admin/clear-all-transactions", methods=["POST"])
def clear_all_transactions():
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM transactions;")
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "Sales ledger memory cleared successfully"})


@app.route("/api/reports", methods=["GET"])
def get_reports():
    init_db()
    range_type = request.args.get("range", "today")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    if range_type == "today":
        date_filter = "DATE(t.timestamp, 'localtime') = DATE('now', 'localtime')"
        params = ()
    elif range_type == "week":
        date_filter = "DATE(t.timestamp, 'localtime') >= DATE('now', 'localtime', '-7 days')"
        params = ()
    elif range_type == "month":
        date_filter = "DATE(t.timestamp, 'localtime') >= DATE('now', 'localtime', '-30 days')"
        params = ()
    elif range_type == "custom" and start_date and end_date:
        date_filter = "DATE(t.timestamp, 'localtime') BETWEEN DATE(?) AND DATE(?)"
        params = (start_date, end_date)
    else:
        date_filter = "1=1"
        params = ()

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(f"""
        SELECT 
            i.item_id,
            i.name,
            i.unit_price,
            COALESCE(SUM(CASE WHEN t.movement_type = 'OUT' THEN t.quantity ELSE 0 END), 0) AS total_units_sold,
            COALESCE(SUM(t.total_amount), 0) AS total_sales_val,
            COALESCE(SUM(CASE WHEN t.payment_method = 'CASH' THEN t.total_amount ELSE 0 END), 0) AS cash_val,
            COALESCE(SUM(CASE WHEN t.payment_method = 'MPESA' THEN t.total_amount ELSE 0 END), 0) AS mpesa_val
        FROM items i
        JOIN transactions t ON i.item_id = t.item_id
        WHERE {date_filter}
        GROUP BY i.item_id
        HAVING total_units_sold > 0
    """, params)
    sales = [dict(row) for row in cursor.fetchall()]

    if not sales:
        conn.close()
        return jsonify({
            "summary": {"total_revenue": 0, "total_units": 0, "cash": 0, "mpesa": 0},
            "top_qty": [], "lowest_qty": [], "top_revenue": [], "high_qty_low_rev": []
        })

    total_rev = sum(s["total_sales_val"] for s in sales)
    total_qty = sum(s["total_units_sold"] for s in sales)
    cash_rev = sum(s["cash_val"] for s in sales)
    mpesa_rev = sum(s["mpesa_val"] for s in sales)

    sorted_by_qty = sorted(sales, key=lambda x: x["total_units_sold"], reverse=True)
    top_qty = sorted_by_qty[:10]
    lowest_qty = sorted_by_qty[-10:][::-1]

    sorted_by_rev = sorted(sales, key=lambda x: x["total_sales_val"], reverse=True)
    top_rev = sorted_by_rev[:10]

    avg_item_price = (total_rev / total_qty) if total_qty > 0 else 0
    divergence = [
        s for s in sales 
        if s["total_units_sold"] >= 3 and s["unit_price"] <= (avg_item_price * 0.5)
    ]
    high_qty_low_rev = sorted(divergence, key=lambda x: x["total_units_sold"], reverse=True)[:10]

    conn.close()
    return jsonify({
        "summary": {
            "total_revenue": total_rev,
            "total_units": total_qty,
            "cash": cash_rev,
            "mpesa": mpesa_rev
        },
        "top_qty": top_qty,
        "lowest_qty": lowest_qty,
        "top_revenue": top_rev,
        "high_qty_low_rev": high_qty_low_rev
    })


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))