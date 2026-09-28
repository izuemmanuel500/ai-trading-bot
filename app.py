import os
import sqlite3
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template, request


# =========================================================
# TRADEMIND
# PAPER TRADING ONLY
# =========================================================

app = Flask(__name__)

DATABASE = "trading.db"
STARTING_BALANCE = 1000.0
TRADE_AMOUNT = 50.0

TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY")

TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"

BACKTEST_MAX_CANDLES = 300


# =========================================================
# MARKETS
# =========================================================

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


# =========================================================
# DATABASE
# =========================================================

def get_db():
    connection = sqlite3.connect(
        DATABASE,
        timeout=30
    )

    connection.row_factory = sqlite3.Row

    return connection


def initialize_database():

    connection = get_db()

    connection.execute("""
        CREATE TABLE IF NOT EXISTS account (
            id INTEGER PRIMARY KEY,
            balance REAL NOT NULL
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT UNIQUE NOT NULL,
            quantity REAL NOT NULL,
            entry_price REAL NOT NULL,
            invested REAL NOT NULL,
            opened_at TEXT NOT NULL
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            price REAL NOT NULL,
            value REAL NOT NULL,
            pnl REAL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)

    account = connection.execute(
        "SELECT id FROM account WHERE id = 1"
    ).fetchone()

    if account is None:
        connection.execute(
            """
            INSERT INTO account (id, balance)
            VALUES (1, ?)
            """,
            (STARTING_BALANCE,)
        )

    connection.commit()
    connection.close()


# =========================================================
# MARKET DATA
# =========================================================

def get_market_candles(
    market,
    interval="1h",
    outputsize=100
):

    if not TWELVE_DATA_API_KEY:
        return {
            "success": False,
            "error": "TWELVE_DATA_API_KEY is not configured."
        }

    try:

        outputsize = max(
            20,
            min(int(outputsize), 5000)
        )

        response = requests.get(
            TWELVE_DATA_URL,
            params={
                "symbol": market,
                "interval": interval,
                "outputsize": outputsize,
                "apikey": TWELVE_DATA_API_KEY
            },
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

        if data.get("status") == "error":
            return {
                "success": False,
                "error": data.get(
                    "message",
                    "Twelve Data returned an error."
                )
            }

        values = data.get("values")

        if not values:
            return {
                "success": False,
                "error": "No market data returned."
            }

        candles = []

        for item in reversed(values):

            try:

                candles.append({
                    "datetime": item.get("datetime"),
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"]),
                    "volume": float(
                        item.get("volume", 0) or 0
                    )
                })

            except (
                KeyError,
                TypeError,
                ValueError
            ):
                continue

        if len(candles) < 20:
            return {
                "success": False,
                "error": "Not enough market data."
            }

        return {
            "success": True,
            "candles": candles
        }

    except requests.RequestException as error:

        return {
            "success": False,
            "error": "Market data request failed: " + str(error)
        }

    except Exception as error:

        return {
            "success": False,
            "error": str(error)
        }


def get_market_price(market):

    result = get_market_candles(
        market,
        interval="1min",
        outputsize=2
    )

    if not result["success"]:
        return None

    return result["candles"][-1]["close"]


# =========================================================
# INDICATORS
# =========================================================

def ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    current = sum(
        values[:period]
    ) / period

    for value in values[period:]:

        current = (
            (value - current) * multiplier
        ) + current

    return current


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

    average_gain = (
        sum(gains[:period]) /
        period
    )

    average_loss = (
        sum(losses[:period]) /
        period
    )

    for i in range(
        period,
        len(gains)
    ):

        average_gain = (
            (
                average_gain *
                (period - 1)
            ) +
            gains[i]
        ) / period

        average_loss = (
            (
                average_loss *
                (period - 1)
            ) +
            losses[i]
        ) / period

    if average_loss == 0:
        return 100.0

    rs = (
        average_gain /
        average_loss
    )

    return 100 - (
        100 / (1 + rs)
    )


def macd(values):

    if len(values) < 35:
        return None, None

    macd_values = []

    for i in range(
        26,
        len(values)
    ):

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
