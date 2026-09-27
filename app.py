import os
import sqlite3
from datetime import datetime

import requests
from flask import Flask, render_template, jsonify, request


app = Flask(__name__)

# ============================================================
# TRADEMIND - MULTI-MARKET PAPER TRADING V1
# ============================================================

DATABASE = "trading.db"

STARTING_BALANCE = 1000.00
TRADE_AMOUNT = 50.00

STOP_LOSS_PERCENT = 3.0
TAKE_PROFIT_PERCENT = 6.0

TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY")


# ------------------------------------------------------------
# SUPPORTED MARKETS
# ------------------------------------------------------------

MARKETS = {
    "BTC/USD": {
        "name": "Bitcoin",
        "type": "Crypto",
        "symbol": "BTC/USD"
    },

    "ETH/USD": {
        "name": "Ethereum",
        "type": "Crypto",
        "symbol": "ETH/USD"
    },

    "EUR/USD": {
        "name": "Euro / US Dollar",
        "type": "Forex",
        "symbol": "EUR/USD"
    },

    "GBP/USD": {
        "name": "British Pound / US Dollar",
        "type": "Forex",
        "symbol": "GBP/USD"
    },

    "USD/JPY": {
        "name": "US Dollar / Japanese Yen",
        "type": "Forex",
        "symbol": "USD/JPY"
    },

    "GBP/JPY": {
        "name": "British Pound / Japanese Yen",
        "type": "Forex",
        "symbol": "GBP/JPY"
    },

    "XAU/USD": {
        "name": "Gold",
        "type": "Metal",
        "symbol": "XAU/USD"
    },

    "XAG/USD": {
        "name": "Silver",
        "type": "Metal",
        "symbol": "XAG/USD"
    },

    "WTI/USD": {
        "name": "US Oil (WTI)",
        "type": "Commodity",
        "symbol": "WTI/USD"
    }
}


# ------------------------------------------------------------
# DATABASE
# ------------------------------------------------------------

def get_db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():

    connection = get_db()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY,
            usdt REAL NOT NULL,
            btc REAL NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            market TEXT NOT NULL,
            action TEXT NOT NULL,
            price REAL NOT NULL,
            quantity REAL NOT NULL,
            amount REAL NOT NULL,
            reason TEXT NOT NULL
        )
    """)

    # Create starting portfolio if it does not exist
    cursor.execute("SELECT COUNT(*) FROM portfolio")
    count = cursor.fetchone()[0]

    if count == 0:
        cursor.execute(
            "INSERT INTO portfolio (id, usdt, btc) VALUES (1, ?, ?)",
            (STARTING_BALANCE, 0.0)
        )

    connection.commit()
    connection.close()


# ------------------------------------------------------------
# MARKET DATA
# ------------------------------------------------------------

def get_market_price(market):

    if not TWELVE_DATA_API_KEY:
        print("ERROR: TWELVE_DATA_API_KEY is not configured.")
        return None

    if market not in MARKETS:
        print("ERROR: Unsupported market:", market)
        return None

    symbol = MARKETS[market]["symbol"]

    try:

        response = requests.get(
            "https://api.twelvedata.com/price",
            params={
                "symbol": symbol,
                "apikey": TWELVE_DATA_API_KEY
            },
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        if "price" not in data:

            print("Twelve Data response:", data)

            return None

        return float(data["price"])

    except Exception as error:

        print("Market data error:", error)

        return None


# ------------------------------------------------------------
# PORTFOLIO
# ------------------------------------------------------------

def get_portfolio():

    connection = get_db()

    portfolio = connection.execute(
        "SELECT * FROM portfolio WHERE id = 1"
    ).fetchone()

    connection.close()

    return dict(portfolio)


# ------------------------------------------------------------
# TRADING SIGNAL
# ------------------------------------------------------------

def generate_signal(price):

    if price is None:
        return "ERROR"

    # V1 is intentionally simple.
    # We will replace this with indicator/AI analysis later.

    return "HOLD"


# ------------------------------------------------------------
# TRADE LOGGING
# ------------------------------------------------------------

def record_trade(
    market,
    action,
    price,
    quantity,
    amount,
    reason
):

    connection = get_db()

    connection.execute(
        """
        INSERT INTO trades
        (timestamp, market, action, price, quantity, amount, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.utcnow().isoformat(),
            market,
            action,
            price,
            quantity,
            amount,
            reason
        )
    )

    connection.commit()
    connection.close()


# ------------------------------------------------------------
# HOME
# ------------------------------------------------------------

@app.route("/")
def home():

    selected_market = request.args.get(
        "market",
        "BTC/USD"
    )

    if selected_market not in MARKETS:
        selected_market = "BTC/USD"

    price = get_market_price(selected_market)

    portfolio = get_portfolio()

    if price is None:
        signal = "NO DATA"
    else:
        signal = generate_signal(price)

    return render_template(
        "dashboard.html",
        price=price,
        usdt=round(portfolio["usdt"], 2),
        btc=round(portfolio["btc"], 8),
        portfolio=portfolio,
        signal=signal,
        market=selected_market,
        markets=MARKETS,
        stop_loss=STOP_LOSS_PERCENT,
        take_profit=TAKE_PROFIT_PERCENT
    )


# ------------------------------------------------------------
# RUN PAPER TRADER
# ------------------------------------------------------------

@app.route("/api/run", methods=["POST"])
def run_bot():

    data = request.get_json(silent=True) or {}

    market = data.get(
        "market",
        "BTC/USD"
    )

    if market not in MARKETS:

        return jsonify({
            "error": "Unsupported market."
        }), 400

    price = get_market_price(market)

    if price is None:

        return jsonify({
            "error": "Market data unavailable."
        }), 503

    signal = generate_signal(price)

    return jsonify({
        "success": True,
        "market": market,
        "price": price,
        "signal": signal,
        "message": "Paper-trading analysis completed."
    })


# ------------------------------------------------------------
# MARKET LIST
# ------------------------------------------------------------

@app.route("/api/markets")
def markets_api():

    return jsonify(MARKETS)


# ------------------------------------------------------------
# PRICE API
# ------------------------------------------------------------

@app.route("/api/price")
def price_api():

    market = request.args.get(
        "market",
        "BTC/USD"
    )

    if market not in MARKETS:

        return jsonify({
            "error": "Unsupported market."
        }), 400

    price = get_market_price(market)

    if price is None:

        return jsonify({
            "error": "Market data unavailable."
        }), 503

    return jsonify({
        "market": market,
        "price": price
    })


# ------------------------------------------------------------
# PORTFOLIO API
# ------------------------------------------------------------

@app.route("/api/portfolio")
def portfolio_api():

    return jsonify(get_portfolio())


# ------------------------------------------------------------
# TRADE HISTORY API
# ------------------------------------------------------------

@app.route("/api/trades")
def trades_api():

    connection = get_db()

    trades = connection.execute(
        """
        SELECT *
        FROM trades
        ORDER BY id DESC
        LIMIT 50
        """
    ).fetchall()

    connection.close()

    return jsonify([
        dict(trade)
        for trade in trades
    ])


# ------------------------------------------------------------
# HEALTH CHECK
# ------------------------------------------------------------

@app.route("/health")
def health():

    return jsonify({
        "status": "online",
        "paper_trading": True,
        "real_money": False,
        "market_data": bool(TWELVE_DATA_API_KEY)
    })


# ------------------------------------------------------------
# START APPLICATION
# ------------------------------------------------------------

initialize_database()


if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
)
