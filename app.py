from flask import Flask, render_template, jsonify
import requests
import sqlite3
from datetime import datetime
import os

app = Flask(__name__)

DATABASE = "trading.db"

STARTING_BALANCE = 1000.00
TRADE_AMOUNT = 50.00

BUY_BELOW = 65000.00
SELL_ABOVE = 67000.00

STOP_LOSS_PERCENT = 3.0
TAKE_PROFIT_PERCENT = 6.0


# -----------------------------
# DATABASE
# -----------------------------

def get_db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():

    connection = get_db()

    connection.execute("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY,
            usdt REAL NOT NULL,
            btc REAL NOT NULL
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            action TEXT NOT NULL,
            price REAL NOT NULL,
            btc_amount REAL NOT NULL,
            usdt_amount REAL NOT NULL,
            reason TEXT
        )
    """)

    existing = connection.execute(
        "SELECT id FROM portfolio WHERE id = 1"
    ).fetchone()

    if not existing:
        connection.execute(
            "INSERT INTO portfolio (id, usdt, btc) VALUES (1, ?, ?)",
            (STARTING_BALANCE, 0.0)
        )

    connection.commit()
    connection.close()


# -----------------------------
# MARKET DATA
# -----------------------------

def get_btc_price():

    try:

        response = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": "BTCUSDT"},
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        return float(data["price"])

    except Exception as error:

        print("Market data error:", error)

        return None


# -----------------------------
# PORTFOLIO
# -----------------------------

def get_portfolio():

    connection = get_db()

    portfolio = connection.execute(
        "SELECT * FROM portfolio WHERE id = 1"
    ).fetchone()

    connection.close()

    return portfolio


# -----------------------------
# TRADING STRATEGY
# -----------------------------

def generate_signal(price):

    portfolio = get_portfolio()

    btc = portfolio["btc"]
    usdt = portfolio["usdt"]

    # BUY SIGNAL
    if price <= BUY_BELOW and usdt >= TRADE_AMOUNT:

        return {
            "signal": "BUY",
            "reason": "BTC is below the configured buy threshold."
        }

    # SELL SIGNAL
    if price >= SELL_ABOVE and btc > 0:

        return {
            "signal": "SELL",
            "reason": "BTC reached the configured sell threshold."
        }

    return {
        "signal": "HOLD",
        "reason": "No trading condition has been triggered."
    }


# -----------------------------
# PAPER TRADE EXECUTION
# -----------------------------

def execute_trade(price, signal, reason):

    connection = get_db()

    portfolio = connection.execute(
        "SELECT * FROM portfolio WHERE id = 1"
    ).fetchone()

    usdt = portfolio["usdt"]
    btc = portfolio["btc"]

    timestamp = datetime.utcnow().isoformat()

    # BUY
    if signal == "BUY" and usdt >= TRADE_AMOUNT:

        btc_bought = TRADE_AMOUNT / price

        new_usdt = usdt - TRADE_AMOUNT
        new_btc = btc + btc_bought

        connection.execute(
            """
            UPDATE portfolio
            SET usdt = ?, btc = ?
            WHERE id = 1
            """,
            (new_usdt, new_btc)
        )

        connection.execute(
            """
            INSERT INTO trades
            (timestamp, action, price, btc_amount, usdt_amount, reason)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                "BUY",
                price,
                btc_bought,
                TRADE_AMOUNT,
                reason
            )
        )

        connection.commit()
        connection.close()

        return "BUY"

    # SELL
    if signal == "SELL" and btc > 0:

        usdt_received = btc * price

        new_usdt = usdt + usdt_received
        new_btc = 0.0

        connection.execute(
            """
            UPDATE portfolio
            SET usdt = ?, btc = ?
            WHERE id = 1
            """,
            (new_usdt, new_btc)
        )

        connection.execute(
            """
            INSERT INTO trades
            (timestamp, action, price, btc_amount, usdt_amount, reason)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                "SELL",
                price,
                btc,
                usdt_received,
                reason
            )
        )

        connection.commit()
        connection.close()

        return "SELL"

    connection.close()

    return "HOLD"


# -----------------------------
# DASHBOARD
# -----------------------------

@app.route("/")
def dashboard():

    price = get_btc_price()

    if price is None:

        return render_template(
            "dashboard.html",
            error="Unable to retrieve market data."
        )

    portfolio = get_portfolio()

    signal_data = generate_signal(price)

    return render_template(
        "dashboard.html",
        price=price,
        usdt=portfolio["usdt"],
        btc=portfolio["btc"],
        signal=signal_data["signal"],
        reason=signal_data["reason"]
    )


# -----------------------------
# RUN PAPER TRADING
# -----------------------------

@app.route("/api/run", methods=["POST"])
def run_bot():

    price = get_btc_price()

    if price is None:

        return jsonify({
            "success": False,
            "error": "Market data unavailable."
        }), 503

    signal_data = generate_signal(price)

    action = execute_trade(
        price,
        signal_data["signal"],
        signal_data["reason"]
    )

    portfolio = get_portfolio()

    portfolio_value = (
        portfolio["usdt"] +
        portfolio["btc"] * price
    )

    profit = portfolio_value - STARTING_BALANCE

    return jsonify({
        "success": True,
        "price": price,
        "signal": signal_data["signal"],
        "action": action,
        "reason": signal_data["reason"],
        "usdt": portfolio["usdt"],
        "btc": portfolio["btc"],
        "portfolio_value": portfolio_value,
        "profit": profit
    })


# -----------------------------
# TRADE HISTORY
# -----------------------------

@app.route("/api/trades")
def trades():

    connection = get_db()

    rows = connection.execute(
        """
        SELECT *
        FROM trades
        ORDER BY id DESC
        LIMIT 50
        """
    ).fetchall()

    connection.close()

    return jsonify([
        dict(row)
        for row in rows
    ])


# -----------------------------
# PORTFOLIO API
# -----------------------------

@app.route("/api/portfolio")
def portfolio_api():

    price = get_btc_price()

    if price is None:
        return jsonify({
            "error": "Market data unavailable."
        }), 503

    portfolio = get_portfolio()

    total_value = (
        portfolio["usdt"] +
        portfolio["btc"] * price
    )

    profit = total_value - STARTING_BALANCE

    return jsonify({
        "usdt": portfolio["usdt"],
        "btc": portfolio["btc"],
        "btc_price": price,
        "total_value": total_value,
        "profit": profit
    })


# -----------------------------
# START
# -----------------------------

initialize_database()

if __name__ == "__main__":

    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
  )
