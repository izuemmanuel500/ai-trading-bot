import os
import sqlite3
from datetime import datetime

import requests
from flask import Flask, render_template, jsonify, request


app = Flask(__name__)

# ============================================================
# TRADEMIND V3
# MULTI-MARKET ANALYSIS + BACKTESTING ENGINE
#
# PAPER TRADING ONLY
# REAL ORDERS: DISABLED
# ============================================================

DATABASE = "trading.db"

STARTING_BALANCE = 1000.00
TRADE_AMOUNT = 50.00

STOP_LOSS_ATR = 1.5
TAKE_PROFIT_ATR = 3.0

TWELVE_DATA_API_KEY = os.getenv(
    "TWELVE_DATA_API_KEY"
)

BACKTEST_START_BALANCE = 1000.00
BACKTEST_TRADE_AMOUNT = 50.00
BACKTEST_MAX_CANDLES = 500


# ============================================================
# MARKETS
# ============================================================

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


# ============================================================
# DATABASE
# ============================================================

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

    cursor.execute("""
        SELECT COUNT(*)
        FROM portfolio
    """)

    count = cursor.fetchone()[0]

    if count == 0:

        cursor.execute(
            """
            INSERT INTO portfolio
            (id, usdt, btc)
            VALUES (1, ?, ?)
            """,
            (
                STARTING_BALANCE,
                0.0
            )
        )

    connection.commit()
    connection.close()


# ============================================================
# MARKET DATA
# ============================================================

def get_market_candles(
    market,
    interval="1h",
    outputsize=100
):

    if not TWELVE_DATA_API_KEY:

        return None, "TWELVE_DATA_API_KEY is missing."

    if market not in MARKETS:

        return None, "Unsupported market."

    symbol = MARKETS[market]["symbol"]

    try:

        response = requests.get(
            "https://api.twelvedata.com/time_series",
            params={
                "symbol": symbol,
                "interval": interval,
                "outputsize": outputsize,
                "order": "asc",
                "apikey": TWELVE_DATA_API_KEY
            },
            timeout=15
        )

        response.raise_for_status()

        data = response.json()

        if "values" not in data:

            print(
                "Twelve Data response:",
                data
            )

            return None, data.get(
                "message",
                "Market data unavailable."
            )

        candles = []

        for item in data["values"]:

            try:

                candles.append({
                    "datetime": item["datetime"],
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"])
                })

            except (
                KeyError,
                ValueError,
                TypeError
            ):

                continue

        if len(candles) == 0:

            return None, "No market candles returned."

        return candles, None

    except Exception as error:

        print(
            "Market data error:",
            error
        )

        return None, str(error)


def get_market_price(market):

    candles, error = get_market_candles(
        market,
        interval="1h",
        outputsize=5
    )

    if not candles:

        return None

    return candles[-1]["close"]


# ============================================================
# INDICATORS
# ============================================================

def ema(values, period):

    if len(values) < period:

        return None

    multiplier = 2 / (period + 1)

    current = (
        sum(values[:period])
        / period
    )

    for price in values[period:]:

        current = (
            (price - current)
            * multiplier
        ) + current

    return current


def rsi(values, period=14):

    if len(values) < period + 1:

        return None

    gains = []
    losses = []

    for i in range(1, len(values)):

        change = (
            values[i]
            - values[i - 1]
        )

        if change >= 0:

            gains.append(change)
            losses.append(0)

        else:

            gains.append(0)
            losses.append(
                abs(change)
            )

    avg_gain = (
        sum(gains[:period])
        / period
    )

    avg_loss = (
        sum(losses[:period])
        / period
    )

    for i in range(
        period,
        len(gains)
    ):

        avg_gain = (
            (
                avg_gain
                * (period - 1)
            )
            + gains[i]
        ) / period

        avg_loss = (
            (
                avg_loss
                * (period - 1)
            )
            + losses[i]
        ) / period

    if avg_loss == 0:

        return 100.0

    relative_strength = (
        avg_gain / avg_loss
    )

    return 100 - (
        100
        / (1 + relative_strength)
    )


def macd(values):

    if len(values) < 35:

        return None, None

    fast_period = 12
    slow_period = 26
    signal_period = 9

    macd_values = []

    for i in range(
        slow_period,
        len(values) + 1
    ):

        subset = values[:i]

        fast_ema = ema(
            subset,
            fast_period
        )

        slow_ema = ema(
            subset,
            slow_period
        )

        if (
            fast_ema is not None
            and slow_ema is not None
        ):

            macd_values.append(
                fast_ema
                - slow_ema
            )

    if len(macd_values) < signal_period:

        return None, None

    macd_line = macd_values[-1]

    signal_line = ema(
        macd_values,
        signal_period
    )

    return (
        macd_line,
        signal_line
    )


def atr(candles, period=14):

    if len(candles) < period + 1:

        return None

    true_ranges = []

    for i in range(
        1,
        len(candles)
    ):

        current = candles[i]
        previous = candles[i - 1]

        high_low = (
            current["high"]
            - current["low"]
        )

        high_previous_close = abs(
            current["high"]
            - previous["close"]
        )

        low_previous_close = abs(
            current["low"]
            - previous["close"]
        )

        true_range = max(
            high_low,
            high_previous_close,
            low_previous_close
        )

        true_ranges.append(
            true_range
        )

    return (
        sum(
            true_ranges[-period:]
        )
        / period
    )


# ============================================================
# ANALYSIS ENGINE
# ============================================================

def calculate_analysis_from_candles(
    candles
):

    if len(candles) < 60:

        return {
            "success": False,
            "error":
                "Insufficient historical data."
        }

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

    rsi14 = rsi(
        closes,
        14
    )

    macd_line, macd_signal = macd(
        closes
    )

    atr14 = atr(
        candles,
        14
    )

    values = [
        ema20,
        ema50,
        rsi14,
        macd_line,
        macd_signal,
        atr14
    ]

    if any(
        value is None
        for value in values
    ):

        return {
            "success": False,
            "error":
                "Insufficient indicator data."
        }

    score = 0

    reasons = []

    if current_price > ema20:

        score += 1

        reasons.append(
            "Price is above EMA 20."
        )

    else:

        score -= 1

        reasons.append(
            "Price is below EMA 20."
        )

    if ema20 > ema50:

        score += 1

        reasons.append(
            "EMA 20 is above EMA 50."
        )

    else:

        score -= 1

        reasons.append(
            "EMA 20 is below EMA 50."
        )

    if 50 <= rsi14 < 70:

        score += 1

        reasons.append(
            "RSI supports bullish momentum."
        )

    elif 30 < rsi14 < 50:

        score -= 1

        reasons.append(
            "RSI supports bearish momentum."
        )

    elif rsi14 >= 70:

        reasons.append(
            "RSI is overbought."
        )

    elif rsi14 <= 30:

        reasons.append(
            "RSI is oversold."
        )

    if macd_line > macd_signal:

        score += 1

        reasons.append(
            "MACD is above its signal."
        )

    else:

        score -= 1

        reasons.append(
            "MACD is below its signal."
        )

    if score >= 3:

        signal = "BUY"

    elif score <= -3:

        signal = "SELL"

    else:

        signal = "HOLD"

    atr_percent = (
        atr14
        / current_price
    ) * 100

    if atr_percent < 1:

        risk = "LOW"

    elif atr_percent < 2.5:

        risk = "MEDIUM"

    else:

        risk = "HIGH"

    if signal == "BUY":

        stop_loss = (
            current_price
            - (
                atr14
                * STOP_LOSS_ATR
            )
        )

        take_profit = (
            current_price
            + (
                atr14
                * TAKE_PROFIT_ATR
            )
        )

    elif signal == "SELL":

        stop_loss = (
            current_price
            + (
                atr14
                * STOP_LOSS_ATR
            )
        )

        take_profit = (
            current_price
            - (
                atr14
                * TAKE_PROFIT_ATR
            )
        )

    else:

        stop_loss = None
        take_profit = None

    return {

        "success": True,

        "price": current_price,

        "signal": signal,

        "score": score,

        "max_score": 4,

        "rsi": round(
            rsi14,
            2
        ),

        "ema20": round(
            ema20,
            6
        ),

        "ema50": round(
            ema50,
            6
        ),

        "macd": round(
            macd_line,
            6
        ),

        "macd_signal": round(
            macd_signal,
            6
        ),

        "atr": round(
            atr14,
            6
        ),

        "volatility_percent": round(
            atr_percent,
            2
        ),

        "risk": risk,

        "stop_loss": (
            round(
                stop_loss,
                6
            )
            if stop_loss is not None
            else None
        ),

        "take_profit": (
            round(
                take_profit,
                6
            )
            if take_profit is not None
            else None
        ),

        "reasons": reasons
    }


def analyze_market(market):

    candles, error = get_market_candles(
        market,
        interval="1h",
        outputsize=100
    )

    if not candles:

        return {
            "success": False,
            "error": error
        }

    analysis = calculate_analysis_from_candles(
        candles
    )

    if not analysis["success"]:

        return analysis

    analysis["market"] = market

    analysis["timestamp"] = (
        datetime.utcnow().isoformat()
    )

    return analysis
    # ============================================================
# BACKTEST ENGINE
# ============================================================

def backtest_market(
    market,
    interval="1h",
    outputsize=500
):

    candles, error = get_market_candles(
        market,
        interval=interval,
        outputsize=outputsize
    )

    if not candles:

        return {
            "success": False,
            "error": error
        }

    if len(candles) < 100:

        return {
            "success": False,
            "error":
                "Not enough candles for backtesting."
        }

    balance = BACKTEST_START_BALANCE

    peak_equity = balance
    max_drawdown = 0.0

    trades = []

    wins = 0
    losses = 0

    position = None

    start_index = 60

    for i in range(
        start_index,
        len(candles)
    ):

        historical_candles = candles[
            :i + 1
        ]

        analysis = calculate_analysis_from_candles(
            historical_candles
        )

        if not analysis["success"]:
            continue

        candle = candles[i]

        current_price = candle["close"]

        signal = analysis["signal"]

        stop_loss = analysis["stop_loss"]
        take_profit = analysis["take_profit"]

        # ====================================================
        # OPEN PAPER LONG POSITION
        # ====================================================

        if position is None:

            if signal == "BUY":

                amount = min(
                    BACKTEST_TRADE_AMOUNT,
                    balance
                )

                if amount > 0:

                    quantity = (
                        amount
                        / current_price
                    )

                    position = {

                        "side": "LONG",

                        "entry_price":
                            current_price,

                        "quantity":
                            quantity,

                        "amount":
                            amount,

                        "stop_loss":
                            stop_loss,

                        "take_profit":
                            take_profit,

                        "entry_time":
                            candle["datetime"]
                    }

                    balance -= amount

            # V3 does not open leveraged
            # short positions.
            #
            # SELL signals are used to
            # exit existing long positions.

        # ====================================================
        # MANAGE OPEN POSITION
        # ====================================================

        if position is not None:

            exit_reason = None
            exit_price = None

            # Stop loss
            if (
                candle["low"]
                <= position["stop_loss"]
            ):

                exit_price = (
                    position["stop_loss"]
                )

                exit_reason = "STOP_LOSS"

            # Take profit
            elif (
                candle["high"]
                >= position["take_profit"]
            ):

                exit_price = (
                    position["take_profit"]
                )

                exit_reason = "TAKE_PROFIT"

            # Strategy exit
            elif signal == "SELL":

                exit_price = current_price

                exit_reason = "SELL_SIGNAL"

            if exit_price is not None:

                exit_value = (
                    position["quantity"]
                    * exit_price
                )

                profit_loss = (
                    exit_value
                    - position["amount"]
                )

                balance += exit_value

                if profit_loss > 0:

                    wins += 1

                elif profit_loss < 0:

                    losses += 1

                trades.append({

                    "entry_time":
                        position["entry_time"],

                    "exit_time":
                        candle["datetime"],

                    "entry_price":
                        position["entry_price"],

                    "exit_price":
                        exit_price,

                    "amount":
                        position["amount"],

                    "profit_loss":
                        round(
                            profit_loss,
                            2
                        ),

                    "return_percent":
                        round(
                            (
                                profit_loss
                                / position["amount"]
                            ) * 100,
                            2
                        ),

                    "reason":
                        exit_reason
                })

                position = None

        # ====================================================
        # EQUITY / DRAWDOWN
        # ====================================================

        if position is not None:

            unrealized_value = (
                position["quantity"]
                * current_price
            )

            equity = (
                balance
                + unrealized_value
            )

        else:

            equity = balance

        if equity > peak_equity:

            peak_equity = equity

        if peak_equity > 0:

            drawdown = (
                (
                    peak_equity
                    - equity
                )
                / peak_equity
            ) * 100

            max_drawdown = max(
                max_drawdown,
                drawdown
            )

    # ========================================================
    # CLOSE ANY REMAINING POSITION
    # ========================================================

    if position is not None:

        final_price = candles[-1]["close"]

        exit_value = (
            position["quantity"]
            * final_price
        )

        profit_loss = (
            exit_value
            - position["amount"]
        )

        balance += exit_value

        if profit_loss > 0:

            wins += 1

        elif profit_loss < 0:

            losses += 1

        trades.append({

            "entry_time":
                position["entry_time"],

            "exit_time":
                candles[-1]["datetime"],

            "entry_price":
                position["entry_price"],

            "exit_price":
                final_price,

            "amount":
                position["amount"],

            "profit_loss":
                round(
                    profit_loss,
                    2
                ),

            "return_percent":
                round(
                    (
                        profit_loss
                        / position["amount"]
                    ) * 100,
                    2
                ),

            "reason":
                "BACKTEST_END"
        })

        position = None

    # ========================================================
    # RESULTS
    # ========================================================

    final_balance = balance

    total_profit = (
        final_balance
        - BACKTEST_START_BALANCE
    )

    total_trades = len(trades)

    if total_trades > 0:

        win_rate = (
            wins
            / total_trades
        ) * 100

        average_trade = (
            total_profit
            / total_trades
        )

    else:

        win_rate = 0
        average_trade = 0

    return {

        "success": True,

        "market": market,

        "interval": interval,

        "candles_tested":
            len(candles),

        "starting_balance":
            round(
                BACKTEST_START_BALANCE,
                2
            ),

        "final_balance":
            round(
                final_balance,
                2
            ),

        "profit":
            round(
                total_profit,
                2
            ),

        "return_percent":
            round(
                (
                    total_profit
                    / BACKTEST_START_BALANCE
                ) * 100,
                2
            ),

        "total_trades":
            total_trades,

        "winning_trades":
            wins,

        "losing_trades":
            losses,

        "win_rate":
            round(
                win_rate,
                2
            ),

        "average_trade":
            round(
                average_trade,
                2
            ),

        "max_drawdown_percent":
            round(
                max_drawdown,
                2
            ),

        "trades":
            trades
    }


# ============================================================
# PORTFOLIO
# ============================================================

def get_portfolio():

    connection = get_db()

    portfolio = connection.execute(
        """
        SELECT *
        FROM portfolio
        WHERE id = 1
        """
    ).fetchone()

    connection.close()

    return dict(portfolio)


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    selected_market = request.args.get(
        "market",
        "BTC/USD"
    )

    if selected_market not in MARKETS:

        selected_market = "BTC/USD"

    analysis = analyze_market(
        selected_market
    )

    portfolio = get_portfolio()

    if analysis["success"]:

        price = analysis["price"]
        signal = analysis["signal"]

    else:

        price = None
        signal = "NO DATA"

    return render_template(
        "dashboard.html",

        price=price,

        usdt=round(
            portfolio["usdt"],
            2
        ),

        btc=round(
            portfolio["btc"],
            8
        ),

        portfolio=portfolio,

        signal=signal,

        market=selected_market,

        markets=MARKETS,

        stop_loss=STOP_LOSS_ATR,

        take_profit=TAKE_PROFIT_ATR,

        analysis=analysis
    )


# ============================================================
# AI TRADER / ANALYSIS
# ============================================================

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

    if market
