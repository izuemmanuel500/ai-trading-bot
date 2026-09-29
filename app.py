import os
import sqlite3
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# ============================================================
# TradeMind V3 - PAPER TRADING ONLY
# ============================================================

DATABASE = "trading.db"

STARTING_BALANCE = 1000.00
TRADE_AMOUNT = 50.00

# ATR-based protection
STOP_LOSS_ATR_MULTIPLIER = 1.5
TAKE_PROFIT_ATR_MULTIPLIER = 2.5

TWELVE_DATA_API_KEY = os.getenv(
    "TWELVE_DATA_API_KEY",
    ""
).strip()

TWELVE_DATA_URL = "https://api.twelvedata.com"


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

    existing_account = conn.execute(
        "SELECT id FROM account WHERE id = 1"
    ).fetchone()

    if not existing_account:
        conn.execute(
            """
            INSERT INTO account (id, balance)
            VALUES (1, ?)
            """,
            (STARTING_BALANCE,)
        )

    conn.commit()
    conn.close()


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
# PERFORMANCE
# ============================================================

def get_performance():
    conn = db()

    rows = conn.execute(
        """
        SELECT pnl
        FROM trades
        WHERE side = 'SELL'
        ORDER BY id ASC
        """
    ).fetchall()

    conn.close()

    realized_pnl = sum(
        float(row["pnl"] or 0)
        for row in rows
    )

    winning_trades = sum(
        1
        for row in rows
        if float(row["pnl"] or 0) > 0
    )

    losing_trades = sum(
        1
        for row in rows
        if float(row["pnl"] or 0) < 0
    )

    completed_trades = (
        winning_trades +
        losing_trades
    )

    if completed_trades > 0:
        win_rate = (
            winning_trades /
            completed_trades
        ) * 100
    else:
        win_rate = 0

    # --------------------------------------------------------
    # Unrealized P/L
    # --------------------------------------------------------

    unrealized_pnl = 0.0

    positions = get_all_positions()

    for position in positions:
        market = position["market"]

        quantity = float(
            position["quantity"]
        )

        avg_price = float(
            position["avg_price"]
        )

        try:
            current_price = get_market_price(
                market
            )
        except Exception:
            current_price = avg_price

        unrealized_pnl += (
            current_price - avg_price
        ) * quantity

    total_pnl = (
        realized_pnl +
        unrealized_pnl
    )

    portfolio = get_portfolio()

    return {
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "total_pnl": total_pnl,

        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "completed_trades": completed_trades,
        "win_rate": win_rate,

        "cash": portfolio["cash"],
        "holdings_value": portfolio["holdings_value"],
        "portfolio_value": portfolio["total_value"],

        "paper_only": True
    }


# ============================================================
# INDICATORS
# ============================================================

def ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    result = (
        sum(values[:period]) /
        period
    )

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
        sum(gains[:period]) /
        period
    )

    avg_loss = (
        sum(losses[:period]) /
        period
    )

    for i in range(period, len(gains)):
        avg_gain = (
            (
                avg_gain * (period - 1)
            ) +
            gains[i]
        ) / period

        avg_loss = (
            (
                avg_loss * (period - 1)
            ) +
            losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100 - (
        100 / (1 + rs)
    )


def macd(values):
    if len(values) < 35:
        return None, None

    macd_values = []

    for i in range(26, len(values)):
        fast = ema(
            values[:i + 1],
            12
        )

        slow = ema(
            values[:i + 1],
            26
        )

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
        high = float(
            candles[i]["high"]
        )

        low = float(
            candles[i]["low"]
        )

        previous_close = float(
            candles[i - 1]["close"]
        )

        true_ranges.append(
            max(
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
        )

    if len(true_ranges) < period:
        return None

    return (
        sum(true_ranges[-period:]) /
        period
    )


# ============================================================
# MARKET DATA
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

    return list(
        reversed(values)
    )


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

    return float(
        data["price"]
    )


# ============================================================
# AI ANALYSIS
# ============================================================

def calculate_confidence(
    score,
    ema_fast,
    ema_slow,
    current_rsi,
    current_macd,
    macd_signal
):
    # Indicator agreement.
    # This is NOT probability of profit.

    confidence = 50

    if abs(score) == 3:
        confidence += 25

    elif abs(score) == 2:
        confidence += 15

   
