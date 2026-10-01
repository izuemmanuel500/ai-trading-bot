import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template, request

# ============================================================
# TRADEMIND V5
# AI PAPER TRADING ENGINE
#
# REAL MONEY: DISABLED
# ALL TRADES ARE SIMULATED
# LONG PAPER TRADING ONLY
# ONE OPEN POSITION PER MARKET
# DUPLICATE TRADE PROTECTION ENABLED
# ============================================================

app = Flask(__name__)

DATABASE = os.getenv("TRADEMIND_DATABASE", "trading.db")
STARTING_BALANCE = 1000.00
TRADE_AMOUNT = 50.00

STOP_LOSS_ATR_MULTIPLIER = 1.5
TAKE_PROFIT_ATR_MULTIPLIER = 2.5
TRADE_COOLDOWN_SECONDS = 8

TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "").strip()
TWELVE_DATA_URL = "https://api.twelvedata.com"

TRADE_EXECUTION_LOCK = threading.Lock()
LAST_TRADE_EXECUTION = {}

MARKETS = {
    "BTC/USD": {"name": "Bitcoin", "symbol": "BTC/USD"},
    "ETH/USD": {"name": "Ethereum", "symbol": "ETH/USD"},
    "EUR/USD": {"name": "Euro / US Dollar", "symbol": "EUR/USD"},
    "GBP/USD": {"name": "British Pound / US Dollar", "symbol": "GBP/USD"},
    "USD/JPY": {"name": "US Dollar / Japanese Yen", "symbol": "USD/JPY"},
    "GBP/JPY": {"name": "British Pound / Japanese Yen", "symbol": "GBP/JPY"},
    "XAU/USD": {"name": "Gold", "symbol": "XAU/USD"},
    "XAG/USD": {"name": "Silver", "symbol": "XAG/USD"},
    "WTI/USD": {"name": "WTI Crude Oil", "symbol": "WTI/USD"},
}


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DATABASE, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_database():
    conn = db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS account (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            balance REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            market TEXT PRIMARY KEY,
            quantity REAL NOT NULL,
            avg_price REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            price REAL NOT NULL,
            value REAL NOT NULL,
            pnl REAL DEFAULT 0,
            signal TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    if not conn.execute(
        "SELECT id FROM account WHERE id = 1"
    ).fetchone():
        conn.execute(
            "INSERT INTO account (id, balance) VALUES (1, ?)",
            (STARTING_BALANCE,)
        )
    conn.commit()
    conn.close()


def get_balance():
    conn = db()
    row = conn.execute(
        "SELECT balance FROM account WHERE id = 1"
    ).fetchone()
    conn.close()
    return float(row["balance"]) if row else STARTING_BALANCE


def set_balance(value):
    conn = db()
    conn.execute(
        "UPDATE account SET balance = ? WHERE id = 1",
        (float(value),)
    )
    conn.commit()
    conn.close()


# ============================================================
# POSITIONS AND HISTORY
# ============================================================

def get_position(market):
    conn = db()
    row = conn.execute(
        "SELECT * FROM positions WHERE market = ?",
        (market,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_positions():
    conn = db()
    rows = conn.execute(
        "SELECT * FROM positions ORDER BY market"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def record_trade(market, side, quantity, price, value, pnl=0, signal=""):
    conn = db()
    conn.execute("""
        INSERT INTO trades
        (market, side, quantity, price, value, pnl, signal, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        market, side, quantity, price, value, pnl, signal,
        datetime.now(timezone.utc).isoformat()
    ))
    conn.commit()
    conn.close()


def get_trades(limit=50):
    limit = max(1, min(int(limit), 200))
    conn = db()
    rows = conn.execute(
        "SELECT * FROM trades ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ============================================================
# TECHNICAL INDICATORS
# ============================================================

def ema(values, period):
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period
    for price in values[period:]:
        result = ((price - result) * multiplier) + result
    return result


def rsi(values, period=14):
    if len(values) <= period:
        return None

    gains, losses = [], []
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(values):
    if len(values) < 35:
        return None, None

    macd_values = []
    for i in range(26, len(values)):
        fast = ema(values[:i + 1], 12)
        slow = ema(values[:i + 1], 26)
        if fast is not None and slow is not None:
            macd_values.append(fast - slow)

    if not macd_values:
        return None, None

    return macd_values[-1], ema(macd_values, 9)


def atr(candles, period=14):
    if len(candles) <= period:
        return None

    true_ranges = []
    for i in range(1, len(candles)):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        previous_close = float(candles[i - 1]["close"])
        true_ranges.append(max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close)
        ))

    if len(true_ranges) < period:
        return None

    return sum(true_ranges[-period:]) / period


# ============================================================
# TWELVE DATA
# ============================================================

def get_candles(market, interval="1h", outputsize=100):
    if not TWELVE_DATA_API_KEY:
        raise RuntimeError(
            "TWELVE_DATA_API_KEY is not configured on Render."
        )

    response = requests.get(
        f"{TWELVE_DATA_URL}/time_series",
        params={
            "symbol": market,
            "interval": interval,
            "outputsize": outputsize,
            "apikey": TWELVE_DATA_API_KEY,
            "format": "JSON"
        },
        timeout=20
    )
    response.raise_for_status()

    data = response.json()
    if data.get("status") == "error":
        raise RuntimeError(
            data.get("message", "Twelve Data returned an error.")
        )

    values = data.get("values")
    if not values:
        raise RuntimeError("No market data was returned.")

    return list(reversed(values))


def get_market_price(market):
    if not TWELVE_DATA_API_KEY:
        raise RuntimeError(
            "TWELVE_DATA_API_KEY is not configured on Render."
        )

    response = requests.get(
        f"{TWELVE_DATA_URL}/price",
        params={
            "symbol": market,
            "apikey": TWELVE_DATA_API_KEY
        },
        timeout=20
    )
    response.raise_for_status()

    data = response.json()
    if data.get("status") == "error":
        raise RuntimeError(
            data.get("message", "Twelve Data returned a price error.")
        )
    if "price" not in data:
        raise RuntimeError("Twelve Data did not return a price.")

    return float(data["price"])


# ============================================================
# MARKET ANALYSIS
# ============================================================

def analyze_market(market):
    if market not in MARKETS:
        raise ValueError("Unsupported market.")

    candles = get_candles(market)
    closes = [float(c["close"]) for c in candles]

    if len(closes) < 60:
        raise RuntimeError("Not enough market data for analysis.")

    price = closes[-1]
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    rsi14 = rsi(closes, 14)
    macd_value, macd_signal = macd(closes)
    atr14 = atr(candles, 14)

    score = 0
    reasons = []

    if ema20 is not None and ema50 is not None:
        if ema20 > ema50:
            score += 1
            reasons.append("EMA20 is above EMA50.")
        elif ema20 < ema50:
            score -= 1
            reasons.append("EMA20 is below EMA50.")

    if rsi14 is not None:
        if rsi14 < 30:
            score += 1
            reasons.append("RSI indicates oversold conditions.")
        elif rsi14 > 70:
            score -= 1
            reasons.append("RSI indicates overbought conditions.")
        elif rsi14 >= 50:
            score += 1
            reasons.append("RSI is above the neutral midpoint.")
        else:
            score -= 1
            reasons.append("RSI is below the neutral midpoint.")

    if macd_value is not None and macd_signal is not None:
        if macd_value > macd_signal:
            score += 1
            reasons.append("MACD is above its signal line.")
        elif macd_value < macd_signal:
            score -= 1
            reasons.append("MACD is below its signal line.")

    if price > ema20:
        score += 1
        reasons.append("Price is above EMA20.")
    elif price < ema20:
        score -= 1
        reasons.append("Price is below EMA20.")

    if score >= 3:
        signal = "BUY"
    elif score <= -3:
        signal = "SELL"
    else:
        signal = "HOLD"

    confidence = min(95, 50 + abs(score) * 10)
    strength = "BULLISH" if score > 0 else "BEARISH" if score < 0 else "NEUTRAL"

    if atr14 is None or price == 0:
        risk = "UNKNOWN"
        stop_loss = None
        take_profit = None
    else:
        atr_percent = (atr14 / price) * 100
        risk = "LOW" if atr_percent < 1 else "MEDIUM" if atr_percent < 2.5 else "HIGH"
        stop_loss = max(0.0, price - atr14 * STOP_LOSS_ATR_MULTIPLIER)
        take_profit = price + atr14 * TAKE_PROFIT_ATR_MULTIPLIER

    return {
        "market": market,
        "name": MARKETS[market]["name"],
        "price": price,
        "signal": signal,
        "score": score,
        "confidence": confidence,
        "strength": strength,
        "risk": risk,
        "ema20": ema20,
        "ema50": ema50,
        "rsi14": rsi14,
        "macd": macd_value,
        "macd_signal": macd_signal,
        "atr14": atr14,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "reasons": reasons,
        "explanation": (
            "TradeMind combines EMA, RSI, MACD and ATR signals. "
            "This is paper-trading analysis, not a guarantee of future movement."
        )
    }


# ============================================================
# PAPER TRADING
# ============================================================

def paper_buy(market, price, signal="BUY"):
    existing = get_position(market)
    if existing:
        return {
            "executed": False,
            "signal_only": True,
            "reason": "An open paper position already exists for this market.",
            "position": existing
        }

    balance = get_balance()
    if balance < TRADE_AMOUNT:
        return {
            "executed": False,
            "signal_only": True,
            "reason": "Insufficient paper cash."
        }

    quantity = TRADE_AMOUNT / price

    conn = db()
    conn.execute(
        "INSERT INTO positions (market, quantity, avg_price) VALUES (?, ?, ?)",
        (market, quantity, price)
    )
    conn.execute(
        "UPDATE account SET balance = balance - ? WHERE id = 1",
        (TRADE_AMOUNT,)
    )
    conn.commit()
    conn.close()

    record_trade(market, "BUY", quantity, price, TRADE_AMOUNT, 0, signal)

    return {
        "executed": True,
        "side": "BUY",
        "market": market,
        "quantity": quantity,
        "price": price,
        "value": TRADE_AMOUNT
    }


def paper_sell(market, price, signal="SELL"):
    position = get_position(market)
    if not position:
        return {
            "executed": False,
            "signal_only": True,
            "reason": "No open paper position exists for this market."
        }

    quantity = float(position["quantity"])
    entry_price = float(position["avg_price"])
    value = quantity * price
    pnl = (price - entry_price) * quantity

    conn = db()
    conn.execute("DELETE FROM positions WHERE market = ?", (market,))
    conn.execute(
        "UPDATE account SET balance = balance + ? WHERE id = 1",
        (value,)
    )
    conn.commit()
    conn.close()

    record_trade(market, "SELL", quantity, price, value, pnl, signal)

    return {
        "executed": True,
        "side": "SELL",
        "market": market,
        "quantity": quantity,
        "price": price,
        "value": value,
        "pnl": pnl
    }


# ============================================================
# RISK AND EXECUTION PROTECTION
# ============================================================

def apply_risk_rules(market, price, analysis):
    position = get_position(market)
    if not position:
        return {"triggered": False, "reason": "No open position."}

    stop_loss = analysis.get("stop_loss")
    take_profit = analysis.get("take_profit")

    if stop_loss is not None and price <= stop_loss:
        result = paper_sell(market, price, "STOP_LOSS")
        return {"triggered": True, "reason": "STOP LOSS", "result": result}

    if take_profit is not None and price >= take_profit:
        result = paper_sell(market, price, "TAKE_PROFIT")
        return {"triggered": True, "reason": "TAKE PROFIT", "result": result}

    return {"triggered": False, "reason": "Risk levels not triggered."}


def check_trade_protection(market):
    last = LAST_TRADE_EXECUTION.get(market)
    if last is None:
        return {"allowed": True, "reason": "No recent execution."}

    remaining = TRADE_COOLDOWN_SECONDS - (time.time() - last)
    if remaining > 0:
        return {
            "allowed": False,
            "reason": f"Trade cooldown active ({remaining:.1f}s remaining)."
        }

    return {"allowed": True, "reason": "Cooldown complete."}


def execute_trade_decision(market, analysis):
    with TRADE_EXECUTION_LOCK:
        protection = check_trade_protection(market)
        if not protection["allowed"]:
            return {
                "executed": False,
                "signal_only": True,
                "reason": protection["reason"]
            }

        price = float(analysis["price"])
        risk_result = apply_risk_rules(market, price, analysis)

        if risk_result["triggered"]:
            LAST_TRADE_EXECUTION[market] = time.time()
            result = risk_result["result"]
            return {
                "executed": result.get("executed", False),
                "signal_only": False,
                "action": risk_result["reason"],
                "reason": risk_result["reason"],
                "result": result
            }

        signal = analysis["signal"]

        if signal == "BUY":
            result = paper_buy(market, price, signal)
            if result.get("executed"):
                LAST_TRADE_EXECUTION[market] = time.time()
            return result

        if signal == "SELL":
            result = paper_sell(market, price, signal)
            if result.get("executed"):
                LAST_TRADE_EXECUTION[market] = time.time()
            return result

        return {
            "executed": False,
            "signal_only": True,
            "reason": "HOLD signal. No paper trade executed."
        }


# ============================================================
# PORTFOLIO
# ============================================================

def get_portfolio():
    cash = get_balance()
    positions = get_all_positions()
    details = []
    open_value = 0.0
    unrealized_pnl = 0.0

    for position in positions:
        market = position["market"]
        try:
            current_price = get_market_price(market)
            quantity = float(position["quantity"])
            avg_price = float(position["avg_price"])
            value = quantity * current_price
            pnl = (current_price - avg_price) * quantity

            open_value += value
            unrealized_pnl += pnl

            details.append({
                **position,
                "current_price": current_price,
                "value": value,
                "unrealized_pnl": pnl
            })
        except Exception as exc:
            details.append({
                **position,
                "current_price": None,
                "value": 0,
                "unrealized_pnl": 0,
                "error": str(exc)
            })

    return {
        "cash": cash,
        "paper_cash": cash,
        "open_position_value": open_value,
        "portfolio_value": cash + open_value,
        "unrealized_pnl": unrealized_pnl,
        "positions": details
    }


# ============================================================
# API ROUTES
# ============================================================

@app.route("/")
def dashboard():
    return render_template("index.html")


@app.route("/api/run", methods=["POST"])
def api_run():
    try:
        payload = request.get_json(silent=True) or {}
        market = str(payload.get("market", "BTC/USD")).upper()

        if market not in MARKETS:
            return jsonify({"success": False, "error": "Unsupported market."}), 400

        analysis = analyze_market(market)
        trade = execute_trade_decision(market, analysis)

        return jsonify({
            "success": True,
            "paper_only": True,
            "real_orders_disabled": True,
            "market": market,
            "analysis": analysis,
            "trade": trade,
            "portfolio": get_portfolio()
        })
    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
            "paper_only": True,
            "real_orders_disabled": True
        }), 500


@app.route("/api/price")
def api_price():
    try:
        market = request.args.get("market", "BTC/USD").upper()
        if market not in MARKETS:
            return jsonify({"success": False, "error": "Unsupported market."}), 400
        return jsonify({
            "success": True,
            "market": market,
            "price": get_market_price(market)
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/analysis")
def api_analysis():
    try:
        market = request.args.get("market", "BTC/USD").upper()
        if market not in MARKETS:
            return jsonify({"success": False, "error": "Unsupported market."}), 400
        return jsonify({"success": True, "analysis": analyze_market(market)})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/portfolio")
def api_portfolio():
    try:
        return jsonify({"success": True, **get_portfolio()})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/trades")
def api_trades():
    try:
        limit = request.args.get("limit", 50)
        return jsonify({"success": True, "trades": get_trades(limit)})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/markets")
def api_markets():
    return jsonify({"success": True, "markets": MARKETS})


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "app": "TradeMind",
        "version": "V5",
        "paper_trading": True,
        "real_orders": False,
        "duplicate_protection": True,
        "one_position_per_market": True,
        "trade_cooldown_seconds": TRADE_COOLDOWN_SECONDS,
        "markets": list(MARKETS.keys())
    })


@app.errorhandler(404)
def not_found(error):
    return jsonify({"success": False, "error": "Route not found."}), 404


@app.errorhandler(500)
def internal_error(error):
    return jsonify({"success": False, "error": "Internal server error."}), 500


# ============================================================
# STARTUP
# ============================================================

initialize_database()

print("============================================")
print("TradeMind V5")
print("Paper Trading Only")
print("Real Orders: DISABLED")
print("============================================")

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
    
