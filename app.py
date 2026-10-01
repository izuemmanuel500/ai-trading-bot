import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template, request


# ============================================================
# TRADEMIND V4.1
# AI PAPER TRADING ENGINE
#
# REAL MONEY: DISABLED
# ALL TRADES ARE SIMULATED
#
# LONG PAPER TRADING ONLY
# ONE OPEN POSITION PER MARKET
# DUPLICATE TRADE PROTECTION ENABLED
# ============================================================

app = Flask(__name__)

DATABASE = "trading.db"

STARTING_BALANCE = 1000.00
TRADE_AMOUNT = 50.00

STOP_LOSS_ATR_MULTIPLIER = 1.5
TAKE_PROFIT_ATR_MULTIPLIER = 2.5

TWELVE_DATA_API_KEY = os.getenv(
    "TWELVE_DATA_API_KEY",
    ""
).strip()

TWELVE_DATA_URL = "https://api.twelvedata.com"


# ============================================================
# TRADE PROTECTION
# ============================================================

# Prevents two trade requests from executing at the same time.
TRADE_EXECUTION_LOCK = threading.Lock()

# Prevents accidental rapid repeated executions.
TRADE_COOLDOWN_SECONDS = 8

# Stores the last successful trade execution time per market.
LAST_TRADE_EXECUTION = {}


# ============================================================
# SUPPORTED MARKETS
# ============================================================

MARKETS = {
    "BTC/USD": {
        "name": "Bitcoin",
        "symbol": "BTC/USD"
    },
    "ETH/USD": {
        "name": "Ethereum",
        "symbol": "ETH/USD"
    },
    "EUR/USD": {
        "name": "Euro / US Dollar",
        "symbol": "EUR/USD"
    },
    "GBP/USD": {
        "name": "British Pound / US Dollar",
        "symbol": "GBP/USD"
    },
    "USD/JPY": {
        "name": "US Dollar / Japanese Yen",
        "symbol": "USD/JPY"
    },
    "GBP/JPY": {
        "name": "British Pound / Japanese Yen",
        "symbol": "GBP/JPY"
    },
    "XAU/USD": {
        "name": "Gold",
        "symbol": "XAU/USD"
    },
    "XAG/USD": {
        "name": "Silver",
        "symbol": "XAG/USD"
    },
    "WTI/USD": {
        "name": "WTI Crude Oil",
        "symbol": "WTI/USD"
    }
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

    account = conn.execute(
        "SELECT id FROM account WHERE id = 1"
    ).fetchone()

    if not account:

        conn.execute(
            """
            INSERT INTO account (id, balance)
            VALUES (1, ?)
            """,
            (STARTING_BALANCE,)
        )

    conn.commit()
    conn.close()


# ============================================================
# ACCOUNT
# ============================================================

def get_balance():

    conn = db()

    row = conn.execute(
        """
        SELECT balance
        FROM account
        WHERE id = 1
        """
    ).fetchone()

    conn.close()

    if row:
        return float(row["balance"])

    return STARTING_BALANCE


def set_balance(value):

    conn = db()

    conn.execute(
        """
        UPDATE account
        SET balance = ?
        WHERE id = 1
        """,
        (float(value),)
    )

    conn.commit()
    conn.close()


# ============================================================
# POSITIONS
# ============================================================

def get_position(market):

    conn = db()

    row = conn.execute(
        """
        SELECT *
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
        SELECT *
        FROM positions
        ORDER BY market
        """
    ).fetchall()

    conn.close()

    return [dict(row) for row in rows]


# ============================================================
# TRADE HISTORY
# ============================================================

def record_trade(
    market,
    side,
    quantity,
    price,
    value,
    pnl=0,
    signal=""
):

    conn = db()

    conn.execute(
        """
        INSERT INTO trades
        (
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
            quantity,
            price,
            value,
            pnl,
            signal,
            datetime.now(timezone.utc).isoformat()
        )
    )

    conn.commit()
    conn.close()


def get_trades(limit=50):

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        ORDER BY id DESC
        LIMIT ?
        """,
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

        result = (
            (price - result) * multiplier
        ) + result

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
            (avg_gain * (period - 1))
            + gains[i]
        ) / period

        avg_loss = (
            (avg_loss * (period - 1))
            + losses[i]
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

            macd_values.append(
                fast - slow
            )

    if not macd_values:
        return None, None

    current_macd = macd_values[-1]

    signal = ema(
        macd_values,
        9
    )

    return current_macd, signal


def atr(candles, period=14):

    if len(candles) <= period:
        return None

    true_ranges = []

    for i in range(1, len(candles)):

        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        previous_close = float(
            candles[i - 1]["close"]
        )

        true_range = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close)
        )

        true_ranges.append(true_range)

    if len(true_ranges) < period:
        return None

    return sum(
        true_ranges[-period:]
    ) / period


# ============================================================
# TWELVE DATA
# ============================================================

def get_candles(
    market,
    interval="1h",
    outputsize=100
):

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
            data.get(
                "message",
                "Twelve Data returned an error."
            )
        )

    values = data.get("values")

    if not values:

        raise RuntimeError(
            "No market data was returned."
        )

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

        raise RuntimeError(
            "Twelve Data did not return a price."
        )

    return float(data["price"])


# ============================================================
# CONFIDENCE
# ============================================================

def calculate_confidence(
    score,
    current_rsi,
    ema_fast,
    ema_slow,
    current_macd,
    macd_signal
):

    confidence = 50

    if score in (3, -3):
        confidence += 25

    elif score in (2, -2):
        confidence += 15

    elif score in (1, -1):
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

    return max(0, min(100, confidence))


# ============================================================
# AI MARKET ANALYSIS
# ============================================================

def analyze_market(market):

    candles = get_candles(
        market,
        interval="1h",
        outputsize=100
    )

    closes = [
        float(c["close"])
        for c in candles
    ]

    price = closes[-1]

    ema_fast = ema(closes, 12)
    ema_slow = ema(closes, 26)

    current_rsi = rsi(closes, 14)

    current_macd, macd_signal = macd(closes)

    current_atr = atr(candles, 14)

    score = 0

    reasons = []

    # ========================================================
    # EMA
    # ========================================================

    if ema_fast is not None and ema_slow is not None:

        if ema_fast > ema_slow:

            score += 1

            reasons.append(
                "EMA trend is bullish"
            )

        else:

            score -= 1

            reasons.append(
                "EMA trend is bearish"
            )

    # ========================================================
    # RSI
    # ========================================================

    if current_rsi is not None:

        if current_rsi < 30:

            score += 1

            reasons.append(
                "RSI is oversold"
            )

        elif current_rsi > 70:

            score -= 1

            reasons.append(
                "RSI is overbought"
            )

        else:

            reasons.append(
                "RSI is neutral"
            )

    # ========================================================
    # MACD
    # ========================================================

    if current_macd is not None and macd_signal is not None:

        if current_macd > macd_signal:

            score += 1

            reasons.append(
                "MACD momentum is bullish"
            )

        else:

            score -= 1

            reasons.append(
                "MACD momentum is bearish"
            )

    # ========================================================
    # SIGNAL
    # ========================================================

    if score >= 2:

        signal = "BUY"
        market_strength = "BULLISH"

    elif score <= -2:

        signal = "SELL"
        market_strength = "BEARISH"

    else:

        signal = "HOLD"
        market_strength = "NEUTRAL"

    # ========================================================
    # CONFIDENCE
    # ========================================================

    confidence = calculate_confidence(
        score,
        current_rsi,
        ema_fast,
        ema_slow,
        current_macd,
        macd_signal
    )

    # ========================================================
    # RISK LEVEL
    # ========================================================

    if current_atr is None:

        risk_level = "UNKNOWN"

    else:

        atr_percent = (
            current_atr / price
        ) * 100

        if atr_percent >= 3:
            risk_level = "HIGH"

        elif atr_percent >= 1:
            risk_level = "MEDIUM"

        else:
            risk_level = "LOW"

    # ========================================================
    # SIGNAL-BASED REFERENCE LEVELS
    # ========================================================

    stop_loss = None
    take_profit = None

    if current_atr is not None:

        if signal == "BUY":

            stop_loss = (
                price
                - current_atr * STOP_LOSS_ATR_MULTIPLIER
            )

            take_profit = (
                price
                + current_atr * TAKE_PROFIT_ATR_MULTIPLIER
            )

        elif signal == "SELL":

            stop_loss = (
                price
                + current_atr * STOP_LOSS_ATR_MULTIPLIER
            )

            take_profit = (
                price
                - current_atr * TAKE_PROFIT_ATR_MULTIPLIER
            )

    # ========================================================
    # EXPLANATION
    # ========================================================

    if signal == "BUY":

        message = (
            " | ".join(reasons)
            + ". Bullish confirmation is present, "
            "but this remains a paper-trading signal."
        )

    elif signal == "SELL":

        message = (
            " | ".join(reasons)
            + ". Bearish confirmation is present, "
            "but this remains a paper-trading signal."
        )

    else:

        message = (
            " | ".join(reasons)
            + ". The indicators are mixed, "
            "so TradeMind is waiting for stronger confirmation."
        )

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

        "paper_only": True
    }


# ============================================================
# PAPER BUY
# ============================================================

def paper_buy(
    market,
    price,
    signal="BUY"
):

    price = float(price)

    if price <= 0:

        return {
            "action": "NONE",
            "executed": False,
            "message": "Invalid market price."
        }

    # ========================================================
    # ONE OPEN POSITION PER MARKET
    # ========================================================

    position = get_position(market)

    if position:

        return {

            "action": "NONE",

            "executed": False,

            "position_exists": True,

            "message": (
                "BUY signal detected, but an open "
                "paper position already exists for "
                f"{market}. No additional money was invested."
            )
        }

    # ========================================================
    # CHECK BALANCE
    # ========================================================

    balance = get_balance()

    if balance <= 0:

        return {

            "action": "NONE",

            "executed": False,

            "message": "Insufficient paper balance."
        }

    amount = min(
        TRADE_AMOUNT,
        balance
    )

    if amount <= 0:

        return {

            "action": "NONE",

            "executed": False,

            "message": "Insufficient paper balance."
        }

    quantity = amount / price

    if quantity <= 0:

        return {

            "action": "NONE",

            "executed": False,

            "message": "Unable to calculate paper quantity."
        }

    # ========================================================
    # CREATE POSITION
    # ========================================================

    conn = db()

    try:

        conn.execute(
            """
            INSERT INTO positions
            (
                market,
                quantity,
                avg_price
            )
            VALUES (?, ?, ?)
            """,
            (
                market,
                quantity,
                price
            )
        )

        conn.commit()

    except sqlite3.IntegrityError:

        conn.rollback()

        return {

            "action": "NONE",

            "executed": False,

            "position_exists": True,

            "message": (
                f"An open paper position for "
                f"{market} already exists."
            )
        }

    finally:

        conn.close()

    # ========================================================
    # UPDATE BALANCE
    # ========================================================

    set_balance(
        balance - amount
    )

    # ========================================================
    # RECORD TRADE
    # ========================================================

    record_trade(
        market,
        "BUY",
        quantity,
        price,
        amount,
        0,
        signal
    )

    return {

        "action": "BUY",

        "executed": True,

        "quantity": quantity,

        "value": amount,

        "pnl": 0,

        "message": (
            "Paper BUY executed. "
            "New paper position opened."
        )
    }


# ============================================================
# PAPER SELL
# ============================================================

def paper_sell(
    market,
    price,
    signal="SELL"
):

    position = get_position(market)

    if not position:

        return {

            "action": "NONE",

            "executed": False,

            "message": (
                "SELL signal detected, "
                "but there is no open "
                "paper position to close."
            ),

            "signal_only": True
        }

    quantity = float(
        position["quantity"]
    )

    avg_price = float(
        position["avg_price"]
    )

    value = quantity * price

    pnl = (
        price - avg_price
    ) * quantity

    set_balance(
        get_balance() + value
    )

    conn = db()

    conn.execute(
        """
        DELETE FROM positions
        WHERE market = ?
        """,
        (market,)
    )

    conn.commit()
    conn.close()

    record_trade(
        market,
        "SELL",
        quantity,
        price,
        value,
        pnl,
        signal
    )

    return {

        "action": "SELL",

        "executed": True,

        "quantity": quantity,

        "value": value,

        "pnl": pnl,

        "message": (
            "Paper SELL executed. "
            "Position closed."
        )
    }


# ============================================================
# LONG POSITION RISK MANAGEMENT
# ============================================================

def apply_risk_rules(
    market,
    price,
    analysis
):

    position = get_position(market)

    if not position:
        return None

    avg_price = float(
        position["avg_price"]
    )

    current_atr = analysis.get("atr")

    # ========================================================
    # RISK LEVELS BASED ON POSITION ENTRY
    # ========================================================

    if current_atr is not None:

        stop_loss = (
            avg_price
            - current_atr * STOP_LOSS_ATR_MULTIPLIER
        )

        take_profit = (
            avg_price
            + current_atr * TAKE_PROFIT_ATR_MULTIPLIER
        )

    else:

        stop_loss = avg_price * 0.98

        take_profit = avg_price * 1.04

    # ========================================================
    # STOP LOSS
    # ========================================================

    if price <= stop_loss:

        result = paper_sell(
            market,
            price,
            "STOP LOSS"
        )

       
