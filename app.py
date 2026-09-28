import os
import sqlite3
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template, request


# =========================================================
# TRADEMIND
# Clean V3 Paper-Trading Backend
# =========================================================

app = Flask(__name__)


# =========================================================
# CONFIGURATION
# =========================================================

DATABASE = "trading.db"

STARTING_BALANCE = 1000.00
TRADE_AMOUNT = 50.00

TWELVE_DATA_API_KEY = os.getenv(
    "TWELVE_DATA_API_KEY"
)

TWELVE_DATA_URL = (
    "https://api.twelvedata.com/time_series"
)

BACKTEST_MAX_CANDLES = 300

MARKETS = [
    "BTC/USD",
    "ETH/USD",
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "GBP/JPY",
    "XAU/USD",
    "XAG/USD",
    "WTI/USD"
]


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

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS account (
            id INTEGER PRIMARY KEY,
            balance REAL NOT NULL
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT UNIQUE NOT NULL,
            quantity REAL NOT NULL,
            entry_price REAL NOT NULL,
            invested REAL NOT NULL,
            opened_at TEXT NOT NULL
        )
        """
    )

    connection.execute(
        """
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
        """
    )

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
# TWELVE DATA
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

        outputsize = int(outputsize)

        outputsize = max(
            20,
            min(outputsize, 5000)
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

        if "status" in data:
            if data["status"] == "error":
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
                TypeError,
                ValueError,
                KeyError
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
            "error": (
                "Market data request failed: "
                + str(error)
            )
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

    candles = result["candles"]

    return candles[-1]["close"]


# =========================================================
# TECHNICAL INDICATORS
# =========================================================

def ema(values, period):

    if not values:
        return None

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

    for index in range(1, len(values)):

        change = (
            values[index] -
            values[index - 1]
        )

        if change >= 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    average_gain = (
        sum(gains[:period]) / period
    )

    average_loss = (
        sum(losses[:period]) / period
    )

    for index in range(
        period,
        len(gains)
    ):

        average_gain = (
            (
                average_gain *
                (period - 1)
            )
            + gains[index]
        ) / period

        average_loss = (
            (
                average_loss *
                (period - 1)
            )
            + losses[index]
        ) / period

    if average_loss == 0:
        return 100.0

    relative_strength = (
        average_gain /
        average_loss
    )

    return (
        100 -
        (100 / (1 + relative_strength))
    )


def macd(values):

    if len(values) < 35:
        return None, None

    fast = ema(values, 12)
    slow = ema(values, 26)

    if fast is None or slow is None:
        return None, None

    macd_value = fast - slow

    macd_values = []

    for index in range(
        26,
        len(values)
    ):

        fast_part = ema(
            values[:index + 1],
            12
        )

        slow_part = ema(
            values[:index + 1],
            26
        )

        if (
            fast_part is not None
            and slow_part is not None
        ):
            macd_values.append(
                fast_part - slow_part
            )

    signal = ema(
        macd_values,
        9
    )

    return macd_value, signal


def atr(candles, period=14):

    if len(candles) <= period:
        return None

    true_ranges = []

    for index in range(
        1,
        len(candles)
    ):

        high = candles[index]["high"]
        low = candles[index]["low"]

        previous_close = (
            candles[index - 1]["close"]
        )

        true_range = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close)
        )

        true_ranges.append(true_range)

    if len(true_ranges) < period:
        return None

    return (
        sum(true_ranges[-period:]) /
        period
    )


# =========================================================
# MARKET ANALYSIS
# =========================================================

def analyze_candles(candles):

    closes = [
        candle["close"]
        for candle in candles
    ]

    current_price = closes[-1]

    ema20 = ema(
        closes,
        20
    )

    ema50 = ema(
        closes,
        50
    )

    rsi_value = rsi(
        closes,
        14
    )

    macd_value, macd_signal = macd(
        closes
    )

    atr_value = atr(
        candles,
        14
    )

    score = 0
    max_score = 5

    reasons = []

    # EMA trend
    if (
        ema20 is not None
        and ema50 is not None
    ):

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

    # RSI
    if rsi_value is not None:

        if rsi_value < 35:

            score += 2

            reasons.append(
                "RSI indicates potentially oversold conditions."
            )

        elif rsi_value > 70:

            score -= 2

            reasons.append(
                "RSI indicates potentially overbought conditions."
            )

        elif rsi_value >= 50:

            score += 1

            reasons.append(
                "RSI is above the neutral level."
            )

        else:

            reasons.append(
                "RSI is below the neutral level."
            )

    # MACD
    if (
        macd_value is not None
        and macd_signal is not None
    ):

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

    # Normalize score
    score = max(
        -max_score,
        min(score, max_score)
    )

    if score >= 2:

        signal = "BUY"

    elif score <= -2:

        signal = "SELL"

    else:

        signal = "HOLD"

    if atr_value:

        stop_loss = (
            current_price -
            (atr_value * 1.5)
        )

        take_profit = (
            current_price +
            (atr_value * 3)
        )

    else:

        stop_loss = (
            current_price * 0.98
        )

        take_profit = (
            current_price * 1.04
        )

    if atr_value:

        risk_percent = (
            atr_value /
            current_price
        ) * 100

    else:

        risk_percent = 2.0

    return {
        "success": True,
        "signal": signal,
        "price": current_price,
        "score": score,
        "max_score": max_score,
        "rsi": rsi_value,
        "ema20": ema20,
        "ema50": ema50,
        "macd": macd_value,
        "macd_signal": macd_signal,
        "atr": atr_value,
        "risk": risk_percent,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "reasons": reasons
    }


def analyze_market(market):

    result = get_market_candles(
        market,
        interval="1h",
        outputsize=100
    )

    if not result["success"]:
        return result

    analysis = analyze_candles(
        result["candles"]
    )

    return analysis


# =========================================================
# PAPER TRADING
# =========================================================

def get_balance():

    connection = get_db()

    row = connection.execute(
        """
        SELECT balance
        FROM account
        WHERE id = 1
        """
    ).fetchone()

    connection.close()

    if row is None:
        return STARTING_BALANCE

    return float(row["balance"])


def update_balance(amount):

    connection = get_db()

    connection.execute(
        """
        UPDATE account
        SET balance = balance + ?
        WHERE id = 1
        """,
        (amount,)
    )

    connection.commit()
    connection.close()


def get_position(market):

    connection = get_db()

    row = connection.execute(
        """
        SELECT *
        FROM positions
        WHERE market = ?
        """,
        (market,)
    ).fetchone()

    connection.close()

    if row is None:
        return None

    return dict(row)


def paper_buy(market, price):

    existing = get_position(market)

    if existing:
        return {
            "success": False,
            "message": "A paper position already exists."
        }

    balance = get_balance()

    if balance < TRADE_AMOUNT:
        return {
            "success": False,
            "message": "Insufficient paper balance."
        }

    quantity = (
        TRADE_AMOUNT /
        price
    )

    now = datetime.now(
        timezone.utc
    ).isoformat()

    connection = get_db()

    connection.execute(
        """
        UPDATE account
        SET balance = balance - ?
        WHERE id = 1
        """,
        (TRADE_AMOUNT,)
    )

    connection.execute(
        """
        INSERT INTO positions
        (
            market,
            quantity,
            entry_price,
            invested,
            opened_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            market,
            quantity,
            price,
            TRADE_AMOUNT,
            now
        )
    )

    connection.execute(
        """
        INSERT INTO trades
        (
            market,
            side,
            quantity,
            price,
            value,
            pnl,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            market,
            "BUY",
            quantity,
            price,
            TRADE_AMOUNT,
            0,
            now
        )
    )

    connection.commit()
    connection.close()

    return {
        "success": True,
        "message": "Paper BUY opened.",
        "quantity": quantity
    }


def paper_sell(market, price):

    position = get_position(market)

    if not position:
        return {
            "success": False,
            "message": "No paper position to sell."
        }

    quantity = float(
        position["quantity"]
    )

    invested = float(
        position["invested"]
    )

    sale_value = (
        quantity *
        price
    )

    pnl = (
        sale_value -
        invested
    )

    now = datetime.now(
        timezone.utc
    ).isoformat()

    connection = get_db()

    connection.execute(
        """
        UPDATE account
        SET balance = balance + ?
        WHERE id = 1
        """,
        (sale_value,)
    )

    connection.execute(
        """
        DELETE FROM positions
        WHERE market = ?
        """,
        (market,)
    )

    connection.execute(
        """
        INSERT INTO trades
        (
            market,
            side,
            quantity,
            price,
            value,
            pnl,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            market,
            "SELL",
            quantity,
            price,
            sale_value,
            pnl,
            now
        )
    )

    connection.commit()
    connection.close()

    return {
        "success": True,
        "message": "Paper SELL completed.",
        "pnl": pnl
    }


# =========================================================
# PORTFOLIO
# =========================================================

def get_portfolio():

    balance = get_balance()

    connection = get_db()

    rows = connection.execute(
        """
        SELECT *
        FROM positions
        ORDER BY id DESC
        """
    ).fetchall()

    connection.close()

    positions = []

    total_position_value = 0.0

    for row in rows:

        market = row["market"]

        price = get_market_price(
            market
        )

        if price is None:
            price = row["entry_price"]

        quantity = float(
            row["quantity"]
        )

        entry_price = float(
            row["entry_price"]
        )

        current_value = (
            quantity *
            price
        )

        unrealized_pnl = (
            current_value -
            float(row["invested"])
        )

        total_position_value += (
            current_value
        )

        positions.append({
            "market": market,
            "quantity": quantity,
            "entry_price": entry_price,
            "current_price": price,
            "value": current_value,
            "pnl": unrealized_pnl,
            "opened_at": row["opened_at"]
        })

    total_equity = (
        balance +
        total_position_value
    )

    return {
        "balance": balance,
        "position_value": total_position_value,
        "equity": total_equity,
        "starting_balance": STARTING_BALANCE,
        "profit": (
            total_equity -
            STARTING_BALANCE
        ),
        "positions": positions
    }


# =========================================================
# BACKTEST
# =========================================================

def backtest_market(
    market,
    interval="1h",
    outputsize=200
):

    result = get_market_candles(
        market,
        interval=interval,
        outputsize=outputsize
    )

    if not result["success"]:
        return result

    candles = result["candles"]

    if len(candles) < 60:
        return {
            "success": False,
            "error": "Not enough candles for backtesting."
        }

    cash = STARTING_BALANCE

    position = None

    trades = []

    equity_curve = []

    for index in range(
        50,
        len(candles)
    ):

        history = candles[
            :index + 1
        ]

        analysis = analyze_candles(
            history
        )

        if not analysis["success"]:
            continue

        price = candles[index]["close"]

        signal = analysis["signal"]

        if (
            signal == "BUY"
            and position is None
            and cash >= TRADE_AMOUNT
        ):

            quantity = (
                TRADE_AMOUNT /
                price
            )

            cash -= TRADE_AMOUNT

            position = {
                "quantity": quantity,
                "entry": price
            }

            trades.append({
                "side": "BUY",
                "price": price
            })

        elif (
            signal == "SELL"
            and position is not None
        ):

            sale_value = (
                position["quantity"] *
                price
            )

            pnl = (
                sale_value -
                TRADE_AMOUNT
            )

            cash += sale_value

            trades.append({
                "side": "SELL",
                "price": price,
                "pnl": pnl
            })

            position = None

        position_value = 0

        if position is not None:

            position_value = (
                position["quantity"] *
                price
            )

        equity = (
            cash +
            position_value
        )

        equity_curve.append(
            equity
        )

    final_price = candles[-1]["close"]

    if position is not None:

        final_value = (
            position["quantity"] *
            final_price
        )

        cash += final_value

        pnl = (
            final_value -
            TRADE_AMOUNT
        )

        trades.append({
            "side": "FINAL_SELL",
            "price": final_price,
            "pnl": pnl
        })

    final_balance = cash

    total_return = (
        (
            final_balance -
            STARTING_BALANCE
        )
        /
        STARTING_BALANCE
    ) * 100

    winning_trades = 0
    losing_trades = 0

    for trade in trades:

        if "pnl" not in trade:
            continue

        if trade["pnl"] > 0:
            winning_trades += 1

        elif trade["pnl"] < 0:
            losing_trades += 1

    closed_trades = (
        winning_trades +
        losing_trades
    )

    if closed_trades > 0:

        win_rate = (
            winning_trades /
            closed_trades
        ) * 100

    else:

        win_rate = 0

    return {
        "success": True,
        "market": market,
        "interval": interval,
        "candles": len(candles),
        "starting_balance": STARTING_BALANCE,
        "ending_balance": final_balance,
        "return_percent": total_return,
        "trades": len(trades),
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate": win_rate,
        "note": (
            "Paper backtest only. "
            "Fees, spread and slippage "
            "are not included."
        )
    }


# =========================================================
# WEB PAGE
# =========================================================

@app.route("/")
def home():

    market = request.args.get(
        "market",
        "BTC/USD"
    )

    if market not in MARKETS:
        market = "BTC/USD"

    portfolio = get_portfolio()

    return render_template(
        "dashboard.html",
        market=market,
        portfolio=portfolio
    )


# =========================================================
# API: RUN TRADEMIND
# =========================================================

@app.route(
    "/api/run",
    methods=["POST"]
)
def run_bot():

    data = request.get_json(
        silent=True
    ) or {}

    market = data.get(
        "market",
        "BTC/USD"
    )

    if market not in MARKETS:

        return jsonify({
            "success": False,
            "error": "Unsupported market."
        }), 400

    analysis = analyze_market(
        market
    )

    if not analysis["success"]:

        return jsonify({
            "success": False,
            "error": analysis.get(
                "error",
                "Market analysis failed."
            )
        }), 503

    return jsonify({
        "success": True,
        "market": market,
        "signal": analysis["signal"],
        "price": analysis["price"],
        "score": analysis["score"],
        "max_score": analysis["max_score"],
        "rsi": analysis["rsi"],
        "ema20": analysis["ema20"],
        "ema50": analysis["ema50"],
        "macd": analysis["macd"],
        "macd_signal": analysis["macd_signal"],
        "atr": analysis["atr"],
        "risk": analysis["risk"],
        "stop_loss": analysis["stop_loss"],
        "take_profit": analysis["take_profit"],
        "reasons": analysis["reasons"],
        "message": (
            "TradeMind analysis completed. "
            "Paper trading only. "
            "No real order was placed."
        )
    })


# =========================================================
# API: BACKTEST
# =========================================================

@app.route(
    "/api/backtest",
    methods=["GET", "POST"]
)
def run_backtest():

    if request.method == "POST":

        data = request.get_json(
            silent=True
        ) or {}

    else:

        data = request.args

    market = data.get(
        "market",
        "BTC/USD"
    )

    interval = data.get(
        "interval",
        "1h"
    )

    try:

        outputsize = int(
            data.get(
                "outputsize",
                200
            )
        )

    except (
        TypeError,
        ValueError
    ):

        outputsize = 200

    outputsize = max(
        100,
        min(
            outputsize,
            BACKTEST_MAX_CANDLES
        )
    )

    if market not in MARKETS:

        return jsonify({
            "success": False,
            "error": "Unsupported market."
        }), 400

    result = backtest_market(
        market,
        interval,
        outputsize
    )

    if not result["success"]:

        return jsonify(result), 503

    return jsonify(result)


# =========================================================
# API: MARKETS
# =========================================================

@app.route("/api/markets")
def markets_api():

    return jsonify({
        "success": True,
        "markets": MARKETS
    })


# =========================================================
# API: PRICE
# =========================================================

@app.route("/api/price")
def price_api():

    market = request.args.get(
        "market",
        "BTC/USD"
    )

    if market not in MARKETS:

        return jsonify({
            "success": False,
            "error": "Unsupported market."
        }), 400

    price = get_market_price(
        market
    )

    if price is None:

        return jsonify({
            "success": False,
            "error": "Market price unavailable."
        }), 503

    return jsonify({
        "success": True,
        "market": market,
        "price": price
    })


# =========================================================
# API: ANALYSIS
# =========================================================

@app.route("/api/analysis")
def analysis_api():

    market = request.args.get(
        "market",
        "BTC/USD"
    )

    if market not in MARKETS:

        return jsonify({
            "success": False,
            "error": "Unsupported market."
        }), 400

    analysis = analyze_market(
        market
    )

    if not analysis["success"]:

        return jsonify(
            analysis
        ), 503

    return jsonify(
        analysis
    )


# =========================================================
# API: PORTFOLIO
# =========================================================

@app.route("/api/portfolio")
def portfolio_api():

    return jsonify({
        "success": True,
        "portfolio": get_portfolio()
    })


# =========================================================
# API: TRADES
# =========================================================

@app.route("/api/trades")
def trades_api():

    connection = get_db()

    rows = connection.execute(
        """
        SELECT *
        FROM trades
        ORDER BY id DESC
        LIMIT 100
        """
    ).fetchall()

    connection.close()

    return jsonify({
        "success": True,
        "trades": [
            dict(row)
            for row in rows
        ]
    })


# =========================================================
# HEALTH CHECK
# =========================================================

@app.route("/health")
def health():

    return jsonify({
        "status": "ok",
        "app": "TradeMind",
        "mode": "PAPER",
        "version": "3.0"
    })


# =========================================================
# STARTUP
# =========================================================

initialize_database()


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "5000"
            )
        ),
        debug=False
    )
