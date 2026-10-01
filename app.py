import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template, request


# ============================================================
# TRADEMIND V5.1
# AI PAPER TRADING
# REAL ORDERS: DISABLED
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


# ============================================================
# MARKETS
# ============================================================

MARKETS = {
    "BTC/USD": {
        "name": "Bitcoin",
        "symbol": "BTC/USD",
        "asset_type": "crypto",
    },
    "ETH/USD": {
        "name": "Ethereum",
        "symbol": "ETH/USD",
        "asset_type": "crypto",
    },
    "EUR/USD": {
        "name": "Euro / US Dollar",
        "symbol": "EUR/USD",
        "asset_type": "forex",
    },
    "GBP/USD": {
        "name": "British Pound / US Dollar",
        "symbol": "GBP/USD",
        "asset_type": "forex",
    },
    "USD/JPY": {
        "name": "US Dollar / Japanese Yen",
        "symbol": "USD/JPY",
        "asset_type": "forex",
    },
    "GBP/JPY": {
        "name": "British Pound / Japanese Yen",
        "symbol": "GBP/JPY",
        "asset_type": "forex",
    },
    "XAU/USD": {
        "name": "Gold",
        "symbol": "XAU/USD",
        "asset_type": "commodity",
    },
    "XAG/USD": {
        "name": "Silver",
        "symbol": "XAG/USD",
        "asset_type": "commodity",
    },
    "WTI/USD": {
        "name": "WTI Crude Oil",
        "symbol": "WTI/USD",
        "asset_type": "commodity",
    },
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
            id INTEGER PRIMARY KEY,
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
            pnl REAL NOT NULL DEFAULT 0,
            signal TEXT,
            created_at TEXT NOT NULL
        )
    """)

    account = conn.execute(
        "SELECT id FROM account WHERE id = 1"
    ).fetchone()

    if account is None:
        conn.execute(
            "INSERT INTO account (id, balance) VALUES (1, ?)",
            (STARTING_BALANCE,)
        )

    conn.commit()
    conn.close()


# ============================================================
# ACCOUNT HELPERS
# ============================================================

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
        """
        SELECT market, quantity, avg_price
        FROM positions
        WHERE market = ?
        """,
        (market,)
    ).fetchone()
    conn.close()

    return dict(row) if row else None


def get_all_positions():
    conn = db()
    rows = conn.execute(
        """
        SELECT market, quantity, avg_price
        FROM positions
        ORDER BY market
        """
    ).fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_trade_history(limit=100):
    conn = db()
    rows = conn.execute(
        """
        SELECT id, market, side, quantity, price, value,
               pnl, signal, created_at
        FROM trades
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(limit),)
    ).fetchall()
    conn.close()

    return [dict(row) for row in rows]


def insert_trade(
    market,
    side,
    quantity,
    price,
    value,
    pnl=0.0,
    signal=""
):
    conn = db()

    conn.execute(
        """
        INSERT INTO trades (
            market,
            side,
            quantity,
            price,
            value,
            pnl,
            signal,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            market,
            side,
            float(quantity),
            float(price),
            float(value),
            float(pnl),
            signal,
            datetime.now(timezone.utc).isoformat(),
        )
    )

    conn.commit()
    conn.close()


# ============================================================
# TECHNICAL INDICATORS
# ============================================================

def ema(values, period):
    if not values:
        return 0.0

    if len(values) < period:
        return sum(values) / len(values)

    multiplier = 2 / (period + 1)

    current = sum(values[:period]) / period

    for price in values[period:]:
        current = (
            (price - current) * multiplier
        ) + current

    return current


def rsi(values, period=14):
    if len(values) <= period:
        return 50.0

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))

    recent_gains = gains[-period:]
    recent_losses = losses[-period:]

    average_gain = sum(recent_gains) / period
    average_loss = sum(recent_losses) / period

    if average_loss == 0:
        return 100.0

    rs = average_gain / average_loss

    return 100 - (100 / (1 + rs))


def macd(values):
    if len(values) < 35:
        return 0.0, 0.0

    ema12 = ema(values, 12)
    ema26 = ema(values, 26)

    macd_value = ema12 - ema26

    macd_series = []

    for i in range(26, len(values) + 1):
        subset = values[:i]
        macd_series.append(
            ema(subset, 12) - ema(subset, 26)
        )

    signal = ema(macd_series, 9)

    return macd_value, signal


def atr(candles, period=14):
    if len(candles) < period + 1:
        return 0.0

    true_ranges = []

    for i in range(1, len(candles)):
        current = candles[i]
        previous = candles[i - 1]

        high = float(current["high"])
        low = float(current["low"])
        previous_close = float(previous["close"])

        true_range = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )

        true_ranges.append(true_range)

    if len(true_ranges) < period:
        return 0.0

    return sum(true_ranges[-period:]) / period


# ============================================================
# TWELVE DATA HELPERS
# ============================================================

def twelve_data_error(data, fallback="Twelve Data returned an unknown error."):
    if not isinstance(data, dict):
        return fallback

    message = data.get("message")
    code = data.get("code")
    status = data.get("status")

    parts = []

    if code:
        parts.append(f"code {code}")

    if message:
        parts.append(str(message))

    if status and status != "ok":
        parts.append(f"status {status}")

    if parts:
        return "Twelve Data: " + " — ".join(parts)

    return fallback


def twelve_data_request(endpoint, params):
    if not TWELVE_DATA_API_KEY:
        raise RuntimeError(
            "TWELVE_DATA_API_KEY is not configured on Render."
        )

    request_params = dict(params)
    request_params["apikey"] = TWELVE_DATA_API_KEY

    try:
        response = requests.get(
            f"{TWELVE_DATA_URL}/{endpoint}",
            params=request_params,
            timeout=20,
        )

        response.raise_for_status()

    except requests.RequestException as exc:
        raise RuntimeError(
            f"Twelve Data network error: {exc}"
        ) from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "Twelve Data returned invalid JSON."
        ) from exc

    if data.get("status") == "error":
        raise RuntimeError(
            twelve_data_error(data)
        )

    if "code" in data and data.get("code") not in (None, 200):
        raise RuntimeError(
            twelve_data_error(data)
        )

    return data


def get_candles(
    market,
    interval="1h",
    outputsize=100
):
    if market not in MARKETS:
        raise ValueError("Unsupported market.")

    market_info = MARKETS[market]

    # First try the requested 1-hour data.
    try:
        data = twelve_data_request(
            "time_series",
            {
                "symbol": market_info["symbol"],
                "interval": interval,
                "outputsize": outputsize,
                "format": "JSON",
            },
        )

        values = data.get("values")

        if values:
            return list(reversed(values)), interval

        raise RuntimeError(
            "Twelve Data returned no candle values."
        )

    except RuntimeError as first_error:

        # Commodity intraday data can vary by plan/instrument.
        # Try daily data as a safe fallback for analysis.
        if market_info["asset_type"] == "commodity" and interval != "1day":
            try:
                data = twelve_data_request(
                    "time_series",
                    {
                        "symbol": market_info["symbol"],
                        "interval": "1day",
                        "outputsize": outputsize,
                        "format": "JSON",
                    },
                )

                values = data.get("values")

                if values:
                    return list(reversed(values)), "1day"

                raise RuntimeError(
                    "Twelve Data returned no daily candle values."
                )

            except RuntimeError as second_error:
                raise RuntimeError(
                    f"{market} data unavailable. "
                    f"1h: {first_error} | "
                    f"daily fallback: {second_error}"
                ) from second_error

        raise first_error


def get_market_price(market):
    if market not in MARKETS:
        raise ValueError("Unsupported market.")

    market_info = MARKETS[market]

    # Try the dedicated latest-price endpoint first.
    try:
        data = twelve_data_request(
            "price",
            {
                "symbol": market_info["symbol"],
            },
        )

        if "price" in data:
            return float(data["price"])

        raise RuntimeError(
            "Twelve Data did not return a price."
        )

    except RuntimeError as price_error:

        # If latest-price access fails, use the latest candle close.
        # This is particularly useful for commodity instruments.
        try:
            candles, _ = get_candles(
                market,
                interval="1h",
                outputsize=5,
            )

            if candles:
                return float(candles[-1]["close"])

        except Exception:
            pass

        raise RuntimeError(
            f"Unable to get current price for {market}: {price_error}"
        ) from price_error


# ============================================================
# AI MARKET ANALYSIS
# ============================================================

def analyze_market(market):
    if market not in MARKETS:
        raise ValueError("Unsupported market.")

    candles, data_interval = get_candles(
        market,
        interval="1h",
        outputsize=100,
    )

    closes = [
        float(candle["close"])
        for candle in candles
    ]

    if len(closes) < 60:
        raise RuntimeError(
            f"Not enough candle data for {market}. "
            f"Received {len(closes)} candles."
        )

    price = closes[-1]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)

    rsi14 = rsi(closes, 14)

    macd_value, macd_signal = macd(closes)

    atr14 = atr(candles, 14)

    score = 0
    reasons = []

    if ema20 > ema50:
        score += 1
        reasons.append(
            "EMA20 is above EMA50."
        )
    else:
        score -= 1
        reasons.append(
            "EMA20 is below EMA50."
        )

    if rsi14 > 55:
        score += 1
        reasons.append(
            "RSI is above the neutral midpoint."
        )
    elif rsi14 < 45:
        score -= 1
        reasons.append(
            "RSI is below the neutral midpoint."
        )
    else:
        reasons.append(
            "RSI is near the neutral midpoint."
        )

    if macd_value > macd_signal:
        score += 1
        reasons.append(
            "MACD is above its signal line."
        )
    else:
        score -= 1
        reasons.append(
            "MACD is below its signal line."
        )

    if price > ema20:
        score += 1
        reasons.append(
            "Price is above EMA20."
        )
    else:
        score -= 1
        reasons.append(
            "Price is below EMA20."
        )

    if score >= 3:
        signal = "BUY"
    elif score <= -3:
        signal = "SELL"
    else:
        signal = "HOLD"

    confidence = int(
        min(
            95,
            max(
                50,
                50 + abs(score) * 10
            )
        )
    )

    if ema20 > ema50:
        strength = "BULLISH"
    elif ema20 < ema50:
        strength = "BEARISH"
    else:
        strength = "NEUTRAL"

    if atr14 <= 0 or price <= 0:
        risk = "UNKNOWN"
    else:
        atr_percent = (atr14 / price) * 100

        if atr_percent < 1:
            risk = "LOW"
        elif atr_percent < 2.5:
            risk = "MEDIUM"
        else:
            risk = "HIGH"

    if atr14 > 0:
        stop_loss = max(
            0.0,
            price - (
                atr14 * STOP_LOSS_ATR_MULTIPLIER
            )
        )

        take_profit = price + (
            atr14 * TAKE_PROFIT_ATR_MULTIPLIER
        )
    else:
        stop_loss = 0.0
        take_profit = 0.0

    return {
        "market": market,
        "name": MARKETS[market]["name"],
        "asset_type": MARKETS[market]["asset_type"],
        "price": price,
        "signal": signal,
        "score": score,
        "confidence": confidence,
        "strength": strength,
        "trend": strength,
        "risk": risk,
        "ema20": ema20,
        "ema50": ema50,
        "rsi14": rsi14,
        "macd": macd_value,
        "macd_signal": macd_signal,
        "atr14": atr14,
        "atr": atr14,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "data_interval": data_interval,
        "reasons": reasons,
        "explanation": (
            f"{MARKETS[market]['name']} is currently "
            f"{strength.lower()} with a {signal} signal "
            f"based on EMA, RSI, MACD and price structure."
        ),
    }


# ============================================================
# PAPER TRADING
# ============================================================

def paper_buy(market, price, signal="BUY"):
    existing = get_position(market)

    if existing:
        return {
            "executed": False,
            "reason": "POSITION_ALREADY_OPEN",
            "message": (
                f"{market} already has an open paper position."
            ),
        }

    balance = get_balance()

    if balance < TRADE_AMOUNT:
        return {
            "executed": False,
            "reason": "INSUFFICIENT_BALANCE",
            "message": (
                f"Paper balance is ${balance:.2f}; "
                f"${TRADE_AMOUNT:.2f} is required."
            ),
        }

    quantity = TRADE_AMOUNT / price

    set_balance(balance - TRADE_AMOUNT)

    conn = db()

    conn.execute(
        """
        INSERT INTO positions (
            market,
            quantity,
            avg_price
        )
        VALUES (?, ?, ?)
        """,
        (
            market,
            quantity,
            price,
        )
    )

    conn.commit()
    conn.close()

    insert_trade(
        market=market,
        side="BUY",
        quantity=quantity,
        price=price,
        value=TRADE_AMOUNT,
        pnl=0.0,
        signal=signal,
    )

    return {
        "executed": True,
        "side": "BUY",
        "market": market,
        "quantity": quantity,
        "price": price,
        "value": TRADE_AMOUNT,
    }


def paper_sell(market, price, signal="SELL"):
    position = get_position(market)

    if not position:
        return {
            "executed": False,
            "signal_only": True,
            "reason": "NO_OPEN_POSITION",
            "message": (
                f"No open paper position exists for {market}."
            ),
        }

    quantity = float(position["quantity"])
    avg_price = float(position["avg_price"])

    value = quantity * price
    pnl = (price - avg_price) * quantity

    balance = get_balance()
    set_balance(balance + value)

    conn = db()

    conn.execute(
        "DELETE FROM positions WHERE market = ?",
        (market,)
    )

    conn.commit()
    conn.close()

    insert_trade(
        market=market,
        side="SELL",
        quantity=quantity,
        price=price,
        value=value,
        pnl=pnl,
        signal=signal,
    )

    return {
        "executed": True,
        "side": "SELL",
        "market": market,
        "quantity": quantity,
        "price": price,
        "value": value,
        "pnl": pnl,
    }


def apply_risk_rules(market, analysis):
    position = get_position(market)

    if not position:
        return {
            "triggered": False
        }

    price = float(analysis["price"])
    stop_loss = float(analysis["stop_loss"])
    take_profit = float(analysis["take_profit"])

    if stop_loss > 0 and price <= stop_loss:
        result = paper_sell(
            market,
            price,
            signal="STOP_LOSS",
        )

        return {
            "triggered": True,
            "reason": "STOP LOSS",
            "result": result,
        }

    if take_profit > 0 and price >= take_profit:
        result = paper_sell(
            market,
            price,
            signal="TAKE_PROFIT",
        )

        return {
            "triggered": True,
            "reason": "TAKE PROFIT",
            "result": result,
        }

    return {
        "triggered": False
    }


def check_trade_protection(market):
    now = time.time()

    last_time = LAST_TRADE_EXECUTION.get(market, 0)

    remaining = (
        TRADE_COOLDOWN_SECONDS
        - (now - last_time)
    )

    if remaining > 0:
        return {
            "allowed": False,
            "reason": "COOLDOWN",
            "remaining_seconds": round(
                remaining,
                2,
            ),
        }

    return {
        "allowed": True
    }


def execute_trade_decision(market, analysis):
    signal = analysis["signal"]
    price = float(analysis["price"])

    with TRADE_EXECUTION_LOCK:

        protection = check_trade_protection(market)

        if not protection["allowed"]:
            return {
                "executed": False,
                "reason": protection["reason"],
                "remaining_seconds": protection[
                    "remaining_seconds"
                ],
                "message": (
                    "Trade protection is active. "
                    "Please wait before running again."
                ),
            }

        LAST_TRADE_EXECUTION[market] = time.time()

        risk_result = apply_risk_rules(
            market,
            analysis,
        )

        if risk_result.get("triggered"):
            return {
                "executed": risk_result["result"].get(
                    "executed",
                    False,
                ),
                "risk_action": risk_result,
            }

        if signal == "BUY":
            return paper_buy(
                market,
                price,
                signal="BUY",
            )

        if signal == "SELL":
            return paper_sell(
                market,
                price,
                signal="SELL",
            )

        return {
            "executed": False,
            "reason": "HOLD",
            "message": (
                "AI signal is HOLD. "
                "No paper order was executed."
            ),
        }


# ============================================================
# PORTFOLIO
# ============================================================

def get_portfolio():
    balance = get_balance()

    positions = []
    positions_value = 0.0
    unrealized_pnl = 0.0

    for position in get_all_positions():
        market = position["market"]
        quantity = float(position["quantity"])
        avg_price = float(position["avg_price"])

        try:
            current_price = get_market_price(market)

            value = quantity * current_price

            pnl = (
                current_price - avg_price
            ) * quantity

            positions_value += value
            unrealized_pnl += pnl

            positions.append({
                "market": market,
                "quantity": quantity,
                "avg_price": avg_price,
                "current_price": current_price,
                "value": value,
                "unrealized_pnl": pnl,
                "error": None,
            })

        except Exception as exc:
            positions.append({
                "market": market,
                "quantity": quantity,
                "avg_price": avg_price,
                "current_price": None,
                "value": 0.0,
                "unrealized_pnl": 0.0,
                "error": str(exc),
            })

    return {
        "balance": balance,
        "cash": balance,
        "positions_value": positions_value,
        "portfolio_value": balance + positions_value,
        "unrealized_pnl": unrealized_pnl,
        "open_positions": len(positions),
        "positions": positions,
    }


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def dashboard():
    return render_template("index.html")


@app.route("/api/run", methods=["POST"])
def api_run():
    payload = request.get_json(silent=True) or {}

    market = str(
        payload.get(
            "market",
            "BTC/USD",
        )
    ).upper()

    if market not in MARKETS:
        return jsonify({
            "success": False,
            "error": "Unsupported market.",
            "market": market,
            "available_markets": list(
                MARKETS.keys()
            ),
        }), 400

    try:
        analysis = analyze_market(market)

        trade = execute_trade_decision(
            market,
            analysis,
        )

        return jsonify({
            "success": True,
            "paper_only": True,
            "real_orders_disabled": True,
            "market": market,
            "analysis": analysis,
            "trade": trade,
            "portfolio": get_portfolio(),
        })

    except Exception as exc:
        return jsonify({
            "success": False,
            "paper_only": True,
            "real_orders_disabled": True,
            "market": market,
            "error": str(exc),
            "error_type": type(exc).__name__,
        }), 502


@app.route("/api/price")
def api_price():
    market = str(
        request.args.get(
            "market",
            "BTC/USD",
        )
    ).upper()

    if market not in MARKETS:
        return jsonify({
            "success": False,
            "error": "Unsupported market.",
        }), 400

    try:
        price = get_market_price(market)

        return jsonify({
            "success": True,
            "market": market,
            "price": price,
            "paper_only": True,
        })

    except Exception as exc:
        return jsonify({
            "success": False,
            "market": market,
            "error": str(exc),
        }), 502


@app.route("/api/analysis")
def api_analysis():
    market = str(
        request.args.get(
            "market",
            "BTC/USD",
        )
    ).upper()

    if market not in MARKETS:
        return jsonify({
            "success": False,
            "error": "Unsupported market.",
        }), 400

    try:
        analysis = analyze_market(market)

        return jsonify({
            "success": True,
            "analysis": analysis,
        })

    except Exception as exc:
        return jsonify({
            "success": False,
            "market": market,
            "error": str(exc),
        }), 502


@app.route("/api/portfolio")
def api_portfolio():
    try:
        return jsonify({
            "success": True,
            "paper_only": True,
            "real_orders_disabled": True,
            "portfolio": get_portfolio(),
        })

    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
        }), 500


@app.route("/api/trades")
def api_trades():
    try:
        limit = int(
            request.args.get(
                "limit",
                100,
            )
        )

        limit = max(
            1,
            min(
                limit,
                500,
            )
        )

        return jsonify({
            "success": True,
            "trades": get_trade_history(limit),
        })

    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
        }), 500


@app.route("/api/markets")
def api_markets():
    return jsonify({
        "success": True,
        "markets": MARKETS,
    })


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "version": "TradeMind V5.1",
        "paper_only": True,
        "real_orders_disabled": True,
        "markets": list(MARKETS.keys()),
        "trade_cooldown_seconds": TRADE_COOLDOWN_SECONDS,
        "duplicate_protection": True,
    })


# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "success": False,
        "error": "Route not found.",
    }), 404


@app.errorhandler(500)
def server_error(error):
    return jsonify({
        "success": False,
        "error": "Internal server error.",
    }), 500


# ============================================================
# STARTUP
# ============================================================

initialize_database()

print("=" * 60)
print("TradeMind V5.1 started")
print("PAPER TRADING ONLY")
print("REAL ORDERS DISABLED")
print("Markets:", ", ".join(MARKETS.keys()))
print("=" * 60)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "5000",
            )
        ),
        debug=False,
)
    
