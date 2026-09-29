import os
import sqlite3
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# ============================================================
# TradeMind V2 - PAPER TRADING ONLY
# ============================================================

DATABASE = "trading.db"
STARTING_BALANCE = 1000.00
TRADE_AMOUNT = 50.00

# ATR-based protection
STOP_LOSS_ATR_MULTIPLIER = 1.5
TAKE_PROFIT_ATR_MULTIPLIER = 2.5

TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "").strip()
TWELVE_DATA_URL = "https://api.twelvedata.com"

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
    conn = sqlite3.connect(DATABASE)
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
        market,
        side,
        quantity,
        price,
        value,
        pnl,
        signal,
        datetime.now(timezone.utc).isoformat()
    ))
    conn.commit()
    conn.close()


def get_trades(limit=50):
    conn = db()
    rows = conn.execute("""
        SELECT * FROM trades
        ORDER BY id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ============================================================
# INDICATORS
# ============================================================

def ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period

    for price in values[period:]:
        result = (price - result) * multiplier + result

    return result


def rsi(values, period=14):
    if len(values) <= period:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (
            (avg_gain * (period - 1)) + gains[i]
        ) / period
        avg_loss = (
            (avg_loss * (period - 1)) + losses[i]
        ) / period

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

    current_macd = macd_values[-1]
    signal = ema(macd_values, 9)

    return current_macd, signal


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
# MARKET DATA
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
            data.get(
                "message",
                "Twelve Data returned a price error."
            )
        )

    if "price" not in data:
        raise RuntimeError("Twelve Data did not return a price.")

    return float(data["price"])


# ============================================================
# V2 ANALYSIS
# ============================================================

def calculate_confidence(
    score,
    ema_fast,
    ema_slow,
    current_rsi,
    current_macd,
    macd_signal
):
    # This is indicator agreement, NOT probability of profit.
    confidence = 50

    if abs(score) == 3:
        confidence += 25
    elif abs(score) == 2:
        confidence += 15
    elif abs(score) == 1:
        confidence += 5

    if ema_fast is not None and ema_slow is not None:
        if score > 0 and ema_fast > ema_slow:
            confidence += 5
        elif score < 0 and ema_fast < ema_slow:
            confidence += 5

    if current_macd is not None and macd_signal is not None:
        if score > 0 and current_macd > macd_signal:
            confidence += 5
        elif score < 0 and current_macd < macd_signal:
            confidence += 5

    if current_rsi is not None and 40 <= current_rsi <= 60:
        confidence -= 5

    return max(0, min(100, int(round(confidence))))


def analyze_market(market):
    candles = get_candles(
        market,
        interval="1h",
        outputsize=100
    )

    closes = [float(c["close"]) for c in candles]
    price = closes[-1]

    ema_fast = ema(closes, 12)
    ema_slow = ema(closes, 26)
    current_rsi = rsi(closes, 14)
    current_macd, macd_signal = macd(closes)
    current_atr = atr(candles, 14)

    score = 0
    reasons = []

    if ema_fast is not None and ema_slow is not None:
        if ema_fast > ema_slow:
            score += 1
            reasons.append("EMA trend is bullish")
        else:
            score -= 1
            reasons.append("EMA trend is bearish")

    if current_rsi is not None:
        if current_rsi < 30:
            score += 1
            reasons.append("RSI is oversold")
        elif current_rsi > 70:
            score -= 1
            reasons.append("RSI is overbought")
        else:
            reasons.append("RSI is neutral")

    if current_macd is not None and macd_signal is not None:
        if current_macd > macd_signal:
            score += 1
            reasons.append("MACD momentum is bullish")
        else:
            score -= 1
            reasons.append("MACD momentum is bearish")

    if score >= 2:
        signal = "BUY"
    elif score <= -2:
        signal = "SELL"
    else:
        signal = "HOLD"

    if score > 0:
        market_strength = "BULLISH"
    elif score < 0:
        market_strength = "BEARISH"
    else:
        market_strength = "NEUTRAL"

    confidence = calculate_confidence(
        score,
        ema_fast,
        ema_slow,
        current_rsi,
        current_macd,
        macd_signal
    )

    if current_atr is None:
        risk_level = "UNKNOWN"
    else:
        atr_percent = (current_atr / price) * 100

        if atr_percent >= 3:
            risk_level = "HIGH"
        elif atr_percent >= 1:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

    # Always provide reference protection levels.
    # For HOLD, these are neutral reference levels only;
    # they are NOT an instruction to open a trade.
    stop_loss = None
    take_profit = None

    if current_atr is not None:
        if signal == "SELL":
            stop_loss = price + (
                current_atr * STOP_LOSS_ATR_MULTIPLIER
            )
            take_profit = price - (
                current_atr * TAKE_PROFIT_ATR_MULTIPLIER
            )
        else:
            # BUY and HOLD use long-side reference levels.
            stop_loss = price - (
                current_atr * STOP_LOSS_ATR_MULTIPLIER
            )
            take_profit = price + (
                current_atr * TAKE_PROFIT_ATR_MULTIPLIER
            )

    if signal == "HOLD":
        message = (
            " | ".join(reasons)
            + ". The indicators are mixed, so "
              "TradeMind is waiting for stronger confirmation."
        )
    else:
        message = (
            " | ".join(reasons)
            + ". This is a paper-trading signal only."
        )

    # IMPORTANT: all V2 fields are explicitly returned here.
    return {
        "market": market,
        "price": price,
        "signal": signal,
        "score": score,
        "confidence": confidence,
        "market_strength": market_strength,
        "risk_level": risk_level,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "rsi": current_rsi,
        "macd": current_macd,
        "macd_signal": macd_signal,
        "atr": current_atr,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "reasons": reasons,
        "message": message,
        "explanation": " | ".join(reasons),
        "paper_only": True
    }


# ============================================================
# PAPER TRADING
# ============================================================

def paper_buy(market, price, signal):
    balance = get_balance()
    position = get_position(market)

    amount = min(TRADE_AMOUNT, balance)

    if amount <= 0:
        return {
            "action": "NONE",
            "message": "Insufficient paper balance."
        }

    quantity = amount / price

    if position:
        old_quantity = float(position["quantity"])
        old_avg = float(position["avg_price"])
        new_quantity = old_quantity + quantity

        new_avg = (
            (old_quantity * old_avg)
            + (quantity * price)
        ) / new_quantity

        conn = db()
        conn.execute("""
            UPDATE positions
            SET quantity = ?, avg_price = ?
            WHERE market = ?
        """, (new_quantity, new_avg, market))
        conn.commit()
        conn.close()

    else:
        conn = db()
        conn.execute("""
            INSERT INTO positions
            (market, quantity, avg_price)
            VALUES (?, ?, ?)
        """, (market, quantity, price))
        conn.commit()
        conn.close()

    set_balance(balance - amount)

    record_trade(
        market, "BUY", quantity, price, amount, 0, signal
    )

    return {
        "action": "BUY",
        "quantity": quantity,
        "value": amount,
        "message": "Paper BUY executed."
    }


def paper_sell(market, price, signal):
    position = get_position(market)

    if not position:
        return {
            "action": "NONE",
            "message": "No paper position to sell."
        }

    quantity = float(position["quantity"])
    avg_price = float(position["avg_price"])

    value = quantity * price
    pnl = (price - avg_price) * quantity

    set_balance(get_balance() + value)

    conn = db()
    conn.execute(
        "DELETE FROM positions WHERE market = ?",
        (market,)
    )
    conn.commit()
    conn.close()

    record_trade(
        market, "SELL", quantity, price, value, pnl, signal
    )

    return {
        "action": "SELL",
        "quantity": quantity,
        "value": value,
        "pnl": pnl,
        "message": "Paper SELL executed."
    }


def apply_risk_rules(market, price, analysis):
    position = get_position(market)

    if not position:
        return None

    stop_loss = analysis.get("stop_loss")
    take_profit = analysis.get("take_profit")

    if stop_loss is None or take_profit is None:
        return None

    avg_price = float(position["avg_price"])

    # Existing paper positions are long positions.
    # Only trigger protection when the current price
    # crosses the calculated levels.
    if price <= stop_loss:
        return paper_sell(market, price, "STOP LOSS")

    if price >= take_profit:
        return paper_sell(market, price, "TAKE PROFIT")

    # If current ATR levels are below/above the old
    # position's entry in an unusual way, do not force
    # a trade. This keeps paper testing conservative.
    _ = avg_price

    return None


# ============================================================
# PORTFOLIO
# ============================================================

def get_portfolio():
    balance = get_balance()
    positions = get_all_positions()

    holdings_value = 0
    enriched_positions = []

    for position in positions:
        market = position["market"]
        quantity = float(position["quantity"])
        avg_price = float(position["avg_price"])

        try:
            price = get_market_price(market)
        except Exception:
            price = avg_price

        value = quantity * price
        pnl = (price - avg_price) * quantity
        holdings_value += value

        enriched_positions.append({
            **position,
            "current_price": price,
            "value": value,
            "pnl": pnl
        })

    return {
        "balance": balance,
        "cash": balance,
        "holdings_value": holdings_value,
        "total_value": balance + holdings_value,
        "positions": enriched_positions
    }


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def dashboard():
    selected_market = request.args.get("market", "BTC/USD")

    if selected_market not in MARKETS:
        selected_market = "BTC/USD"

    try:
        portfolio = get_portfolio()
    except Exception:
        balance = get_balance()
        portfolio = {
            "balance": balance,
            "cash": balance,
            "holdings_value": 0,
            "total_value": balance,
            "positions": []
        }

    try:
        price = get_market_price(selected_market)
    except Exception:
        price = 0.0

    return render_template(
        "dashboard.html",
        market=selected_market,
        selected_market=selected_market,
        markets=MARKETS,
        portfolio=portfolio,
        price=price,
        current_price=price
    )


@app.route("/api/run", methods=["POST"])
def run_bot():
    data = request.get_json(silent=True) or {}
    market = data.get("market", "BTC/USD")

    if market not in MARKETS:
        return jsonify({"error": "Unsupported market."}), 400

    try:
        analysis = analyze_market(market)

        risk_action = apply_risk_rules(
            market,
            analysis["price"],
            analysis
        )

        if risk_action:
            trade_action = risk_action
        elif analysis["signal"] == "BUY":
            trade_action = paper_buy(
                market,
                analysis["price"],
                analysis["signal"]
            )
        elif analysis["signal"] == "SELL":
            trade_action = paper_sell(
                market,
                analysis["price"],
                analysis["signal"]
            )
        else:
            trade_action = {
                "action": "HOLD",
                "message": "No trade executed."
            }

        return jsonify({
            **analysis,
            "trade": trade_action,
            "paper_only": True
        })

    except Exception as exc:
        return jsonify({
            "error": str(exc)
        }), 500


@app.route("/api/price")
def api_price():
    market = request.args.get("market", "BTC/USD")

    if market not in MARKETS:
        return jsonify({"error": "Unsupported market."}), 400

    try:
        return jsonify({
            "market": market,
            "price": get_market_price(market)
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/analysis")
def api_analysis():
    market = request.args.get("market", "BTC/USD")

    if market not in MARKETS:
        return jsonify({"error": "Unsupported market."}), 400

    try:
        return jsonify(analyze_market(market))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/portfolio")
def api_portfolio():
    try:
        return jsonify(get_portfolio())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/trades")
def api_trades():
    return jsonify(get_trades())


@app.route("/api/markets")
def api_markets():
    return jsonify(MARKETS)


@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    data = request.get_json(silent=True) or {}
    market = data.get("market", "BTC/USD")

    if market not in MARKETS:
        return jsonify({"error": "Unsupported market."}), 400

    try:
        candles = get_candles(
            market,
            interval=data.get("interval", "1h"),
            outputsize=100
        )

        closes = [float(c["close"]) for c in candles]

        if len(closes) < 2:
            raise RuntimeError("Not enough historical data.")

        start_price = closes[0]
        end_price = closes[-1]

        change_pct = (
            (end_price - start_price) / start_price
        ) * 100

        return jsonify({
            "market": market,
            "start_price": start_price,
            "end_price": end_price,
            "change_percent": change_pct,
            "candles": len(candles),
            "note": (
                "This is a basic historical market test, "
                "not a guarantee of future results."
            )
        })

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "TradeMind",
        "version": "V2",
        "paper_trading": True,
        "markets": len(MARKETS)
    })


initialize_database()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=False
)
