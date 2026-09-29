import os
import sqlite3
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template, request


app = Flask(__name__)


# ============================================================
# SETTINGS
# ============================================================

DATABASE = "trading.db"

STARTING_BALANCE = 1000.0
TRADE_AMOUNT = 50.0

TWELVE_DATA_URL = "https://api.twelvedata.com"


MARKETS = {
    "BTC/USD": "Bitcoin — BTC/USD",
    "ETH/USD": "Ethereum — ETH/USD",
    "EUR/USD": "Euro / US Dollar — EUR/USD",
    "GBP/USD": "British Pound / US Dollar — GBP/USD",
    "USD/JPY": "US Dollar / Japanese Yen — USD/JPY",
    "GBP/JPY": "British Pound / Japanese Yen — GBP/JPY",
    "XAU/USD": "Gold — XAU/USD",
    "XAG/USD": "Silver — XAG/USD",
    "WTI/USD": "WTI Crude Oil — WTI/USD"
}


# ============================================================
# TIME
# ============================================================

def now():
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS account (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            cash REAL NOT NULL
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
            price REAL NOT NULL,
            quantity REAL NOT NULL,
            pnl REAL DEFAULT 0,
            timestamp TEXT NOT NULL
        )
    """)

    account = conn.execute(
        "SELECT id FROM account WHERE id = 1"
    ).fetchone()

    if account is None:
        conn.execute(
            "INSERT INTO account (id, cash) VALUES (1, ?)",
            (STARTING_BALANCE,)
        )

    conn.commit()
    conn.close()


# ============================================================
# TWELVE DATA API
# ============================================================

def api_key():
    key = os.getenv("TWELVE_DATA_API_KEY")

    if not key:
        raise RuntimeError(
            "TWELVE_DATA_API_KEY is not configured on Render."
        )

    return key


def twelve_get(path, params):
    params = dict(params)
    params["apikey"] = api_key()

    response = requests.get(
        TWELVE_DATA_URL + path,
        params=params,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    if data.get("status") == "error":
        raise RuntimeError(
            data.get(
                "message",
                "Twelve Data error"
            )
        )

    return data


def get_candles(
    market,
    interval="1h",
    outputsize=120
):
    data = twelve_get(
        "/time_series",
        {
            "symbol": market,
            "interval": interval,
            "outputsize": outputsize,
            "format": "JSON"
        }
    )

    values = data.get("values") or []

    if len(values) < 30:
        raise RuntimeError(
            "Not enough market data returned."
        )

    return list(reversed(values))


def get_market_price(market):
    data = twelve_get(
        "/price",
        {
            "symbol": market
        }
    )

    price = float(data["price"])

    if price <= 0:
        raise RuntimeError(
            "Invalid market price."
        )

    return price


# ============================================================
# PRICE DATA
# ============================================================

def closes(candles):
    return [
        float(item["close"])
        for item in candles
    ]


# ============================================================
# EMA
# ============================================================

def ema(values, period):
    if len(values) < period:
        return values[-1]

    value = (
        sum(values[:period]) /
        period
    )

    multiplier = 2.0 / (
        period + 1
    )

    for price in values[period:]:
        value = (
            (price - value) *
            multiplier
        ) + value

    return value


# ============================================================
# RSI
# ============================================================

def rsi(values, period=14):
    if len(values) <= period:
        return 50.0

    gains = []
    losses = []

    for i in range(1, period + 1):
        change = (
            values[i] -
            values[i - 1]
        )

        gains.append(
            max(change, 0)
        )

        losses.append(
            max(-change, 0)
        )

    avg_gain = (
        sum(gains) /
        period
    )

    avg_loss = (
        sum(losses) /
        period
    )

    for i in range(
        period + 1,
        len(values)
    ):
        change = (
            values[i] -
            values[i - 1]
        )

        gain = max(
            change,
            0
        )

        loss = max(
            -change,
            0
        )

        avg_gain = (
            (
                avg_gain *
                (period - 1)
            ) + gain
        ) / period

        avg_loss = (
            (
                avg_loss *
                (period - 1)
            ) + loss
        ) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100.0 - (
        100.0 /
        (1.0 + rs)
    )


# ============================================================
# MACD
# ============================================================

def macd(values):
    fast = ema(
        values,
        12
    )

    slow = ema(
        values,
        26
    )

    line = fast - slow

    macd_values = []

    for i in range(
        26,
        len(values) + 1
    ):
        part = values[:i]

        macd_values.append(
            ema(part, 12) -
            ema(part, 26)
        )

    if macd_values:
        signal = ema(
            macd_values,
            9
        )
    else:
        signal = 0.0

    return line, signal


# ============================================================
# ATR
# ============================================================

def atr(candles, period=14):
    if len(candles) < 2:
        return 0.0

    true_ranges = []

    for i in range(
        1,
        len(candles)
    ):
        high = float(
            candles[i]["high"]
        )

        low = float(
            candles[i]["low"]
        )

        previous_close = float(
            candles[i - 1]["close"]
        )

        current_range = max(
            high - low,
            abs(
                high -
                previous_close
            ),
            abs(
                low -
                previous_close
            )
        )

        true_ranges.append(
            current_range
        )

    if not true_ranges:
        return 0.0

    recent = true_ranges[-period:]

    return (
        sum(recent) /
        len(recent)
    )


# ============================================================
# AI MARKET ANALYSIS
# ============================================================

def analyze_market(market):
    candles = get_candles(
        market
    )

    values = closes(
        candles
    )

    price = values[-1]

    fast = ema(
        values,
        9
    )

    slow = ema(
        values,
        21
    )

    rsi_value = rsi(
        values
    )

    macd_value, macd_signal = macd(
        values
    )

    atr_value = atr(
        candles
    )

    score = 0
    reasons = []


    # EMA
    if fast > slow:
        score += 1

        reasons.append(
            "EMA trend is bullish"
        )

    elif fast < slow:
        score -= 1

        reasons.append(
            "EMA trend is bearish"
        )

    else:
        reasons.append(
            "EMA trend is neutral"
        )


    # RSI
    if rsi_value < 30:
        score += 1

        reasons.append(
            "RSI is oversold"
        )

    elif rsi_value > 70:
        score -= 1

        reasons.append(
            "RSI is overbought"
        )

    else:
        reasons.append(
            "RSI is neutral"
        )


    # MACD
    if macd_value > macd_signal:
        score += 1

        reasons.append(
            "MACD momentum is bullish"
        )

    elif macd_value < macd_signal:
        score -= 1

        reasons.append(
            "MACD momentum is bearish"
        )

    else:
        reasons.append(
            "MACD momentum is neutral"
        )


    # SIGNAL
    if score >= 2:
        signal = "BUY"
        strength = "BULLISH"

    elif score <= -2:
        signal = "SELL"
        strength = "BEARISH"

    else:
        signal = "HOLD"
        strength = "NEUTRAL"


    # CONFIDENCE
    confidence = 40 + (
        abs(score) * 15
    )

    if confidence > 85:
        confidence = 85


    # RISK
    risk = "LOW"

    if atr_value > (
        price * 0.02
    ):
        risk = "HIGH"

    elif atr_value > (
        price * 0.01
    ):
        risk = "MEDIUM"


    # STOP LOSS
    stop_loss = price - (
        1.5 * atr_value
    )


    # TAKE PROFIT
    take_profit = price + (
        2.5 * atr_value
    )


    return {
        "market": market,
        "price": price,
        "signal": signal,
        "confidence": confidence,
        "market_strength": strength,
        "risk_level": risk,
        "ema_fast": fast,
        "ema_slow": slow,
        "rsi": rsi_value,
        "macd": macd_value,
        "macd_signal": macd_signal,
        "atr": atr_value,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "score": score,
        "reasons": reasons,
        "explanation": " | ".join(
            reasons
        ),
        "message": (
            " | ".join(reasons) +
            ". This is a "
            "paper-trading signal only."
        ),
        "paper_only": True
    }


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

    return row


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

    return rows


# ============================================================
# PORTFOLIO
# ============================================================

def get_portfolio():
    conn = db()

    account = conn.execute(
        """
        SELECT cash
        FROM account
        WHERE id = 1
        """
    ).fetchone()

    positions = conn.execute(
        """
        SELECT *
        FROM positions
        """
    ).fetchall()

    conn.close()

    cash = float(
        account["cash"]
    )

    holdings = 0.0

    for position in positions:
        try:
            price = get_market_price(
                position["market"]
            )

        except Exception:
            price = float(
                position["avg_price"]
            )

        holdings += (
            float(
                position["quantity"]
            ) *
            price
        )

    return {
        "cash": cash,
        "holdings_value": holdings,
        "total_value": (
            cash + holdings
        ),
        "positions": len(
            positions
        ),
        "paper_only": True
    }


# ============================================================
# PAPER BUY
# ============================================================

def execute_buy(
    market,
    price
):
    conn = db()

    account = conn.execute(
        """
        SELECT cash
        FROM account
        WHERE id = 1
        """
    ).fetchone()

    cash = float(
        account["cash"]
    )

    if cash < TRADE_AMOUNT:
        conn.close()

        return (
            False,
            "Not enough paper cash."
        )

    existing = conn.execute(
        """
        SELECT *
        FROM positions
        WHERE market = ?
        """,
        (market,)
    ).fetchone()

    if existing:
        conn.close()

        return (
            False,
            "A paper position "
            "is already open."
        )

    quantity = (
        TRADE_AMOUNT /
        price
    )

    new_cash = (
        cash -
        TRADE_AMOUNT
    )

    conn.execute(
        """
        UPDATE account
        SET cash = ?
        WHERE id = 1
        """,
        (new_cash,)
    )

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

    conn.execute(
        """
        INSERT INTO trades
        (
            market,
            side,
            price,
            quantity,
            pnl,
            timestamp
        )
        VALUES (?, 'BUY', ?, ?, 0, ?)
        """,
        (
            market,
            price,
            quantity,
            now()
        )
    )

    conn.commit()
    conn.close()

    return (
        True,
        "Paper BUY executed."
    )


# ============================================================
# PAPER SELL
# ============================================================

def execute_sell(
    market,
    price
):
    conn = db()

    position = conn.execute(
        """
        SELECT *
        FROM positions
        WHERE market = ?
        """,
        (market,)
    ).fetchone()

    if position is None:
        conn.close()

        return (
            False,
            "No paper position "
            "is open."
        )

    quantity = float(
        position["quantity"]
    )

    avg_price = float(
        position["avg_price"]
    )

    proceeds = (
        quantity *
        price
    )

    pnl = (
        price -
        avg_price
    ) * quantity

    account = conn.execute(
        """
        SELECT cash
        FROM account
        WHERE id = 1
        """
    ).fetchone()
