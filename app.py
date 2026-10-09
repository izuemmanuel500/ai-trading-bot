import os
import threading
import time
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template, request
from supabase import create_client, Client

app = Flask(__name__)

STARTING_BALANCE = 1000.00
TRADE_AMOUNT = 50.00
RISK_PER_TRADE = 0.05
MAX_OPEN_POSITIONS = 3
AUTOPILOT_MIN_CONFIDENCE = 70
AUTOPILOT_INTERVAL_SECONDS = 300  # 5 minutes to reduce market-data API pressure
AUTOPILOT_ENABLED = os.getenv("TRADEMIND_AUTOPILOT", "true").strip().lower() == "true"
STOP_LOSS_ATR_MULTIPLIER = 1.5
TAKE_PROFIT_ATR_MULTIPLIER = 2.5
TRADE_COOLDOWN_SECONDS = 8

TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "").strip()
TWELVE_DATA_URL = "https://api.twelvedata.com"

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError(
        "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured on Render."
    )

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

TRADE_EXECUTION_LOCK = threading.Lock()
LAST_TRADE_EXECUTION = {}

MARKET_DATA_CACHE = {}
MARKET_PRICE_CACHE = {}
DATA_CACHE_TTL_SECONDS = 60
PRICE_CACHE_TTL_SECONDS = 30

MARKETS = {
    "BTC/USD": {"name": "Bitcoin", "symbol": "BTC/USD", "asset_type": "crypto"},
    "ETH/USD": {"name": "Ethereum", "symbol": "ETH/USD", "asset_type": "crypto"},
    "EUR/USD": {"name": "Euro / US Dollar", "symbol": "EUR/USD", "asset_type": "forex"},
    "GBP/USD": {"name": "British Pound / US Dollar", "symbol": "GBP/USD", "asset_type": "forex"},
    "USD/JPY": {"name": "US Dollar / Japanese Yen", "symbol": "USD/JPY", "asset_type": "forex"},
    "GBP/JPY": {"name": "British Pound / Japanese Yen", "symbol": "GBP/JPY", "asset_type": "forex"},
    "XAU/USD": {"name": "Gold", "symbol": "XAU/USD", "asset_type": "commodity"},
    "XAG/USD": {"name": "Silver", "symbol": "XAG/USD", "asset_type": "commodity"},
    "WTI/USD": {"name": "WTI Crude Oil", "symbol": "WTI/USD", "asset_type": "commodity"},
    "USD/CAD": {"name": "US Dollar / Canadian Dollar", "symbol": "USD/CAD", "asset_type": "forex"},
    "AUD/USD": {"name": "Australian Dollar / US Dollar", "symbol": "AUD/USD", "asset_type": "forex"},
    "NZD/USD": {"name": "New Zealand Dollar / US Dollar", "symbol": "NZD/USD", "asset_type": "forex"},
}


# ============================================================
# SUPABASE PERSISTENT STORAGE
# ============================================================

def _single(result):
    data = result.data
    return data[0] if data else None


def initialize_database():
    result = supabase.table("account").select("id").eq("id", 1).limit(1).execute()
    if not result.data:
        supabase.table("account").insert({
            "id": 1,
            "balance": STARTING_BALANCE
        }).execute()


def get_balance():
    result = (
        supabase.table("account")
        .select("balance")
        .eq("id", 1)
        .limit(1)
        .execute()
    )
    row = _single(result)
    return float(row["balance"]) if row else STARTING_BALANCE


def set_balance(value):
    supabase.table("account").update({
        "balance": float(value)
    }).eq("id", 1).execute()


def get_position(market):
    result = (
        supabase.table("positions")
        .select(
            "market,quantity,avg_price,side,stop_loss,take_profit,allocated_capital"
        )
        .eq("market", market)
        .limit(1)
        .execute()
    )
    row = _single(result)
    return dict(row) if row else None


def get_all_positions():
    result = (
        supabase.table("positions")
        .select(
            "market,quantity,avg_price,side,stop_loss,take_profit,allocated_capital"
        )
        .order("market")
        .execute()
    )
    return [dict(r) for r in (result.data or [])]


def get_trade_history(limit=100):
    limit = max(1, min(int(limit), 500))
    result = (
        supabase.table("trades")
        .select("id,market,side,quantity,price,value,pnl,signal,created_at")
        .order("id", desc=True)
        .limit(limit)
        .execute()
    )
    return [dict(r) for r in (result.data or [])]


def insert_trade(market, side, quantity, price, value, pnl=0.0, signal=""):
    supabase.table("trades").insert({
        "market": market,
        "side": side,
        "quantity": float(quantity),
        "price": float(price),
        "value": float(value),
        "pnl": float(pnl),
        "signal": signal,
        "created_at": datetime.now(timezone.utc).isoformat()
    }).execute()


def insert_analysis_history(
    market,
    signal,
    price=None,
    confidence=None,
    risk="",
    execution_status="ANALYZED",
    message="",
    data_interval=""
):
    supabase.table("analysis_history").insert({
        "market": market,
        "signal": signal,
        "price": price,
        "confidence": confidence,
        "risk": risk,
        "execution_status": execution_status,
        "message": message,
        "data_interval": data_interval,
        "created_at": datetime.now(timezone.utc).isoformat()
    }).execute()


def get_analysis_history(limit=100):
    limit = max(1, min(int(limit), 500))
    result = (
        supabase.table("analysis_history")
        .select(
            "id,market,signal,price,confidence,risk,"
            "execution_status,message,data_interval,created_at"
        )
        .order("id", desc=True)
        .limit(limit)
        .execute()
    )
    return [dict(r) for r in (result.data or [])]


def get_combined_history(limit=100):
    limit = max(1, min(int(limit), 500))

    trades = get_trade_history(limit)
    analyses = get_analysis_history(limit)

    items = []

    for r in trades:
        x = dict(r)
        x.update({
            "record_type": "TRADE",
            "status": "PAPER TRADE",
            "action": x.get("side"),
            "confidence": None,
            "risk": "",
            "execution_status": "PAPER_TRADE_EXECUTED",
            "message": x.get("signal") or ""
        })
        items.append(x)

    for r in analyses:
        x = dict(r)
        x.update({
            "record_type": "ANALYSIS",
            "status": x.get("execution_status") or "ANALYZED",
            "action": x.get("signal"),
            "side": None,
            "quantity": None,
            "value": None,
            "pnl": 0.0
        })
        items.append(x)

    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return items[:limit]


def reset_paper_account():
    supabase.table("positions").delete().neq("market", "").execute()
    supabase.table("trades").delete().gt("id", 0).execute()
    supabase.table("analysis_history").delete().gt("id", 0).execute()

    result = (
        supabase.table("account")
        .update({"balance": STARTING_BALANCE})
        .eq("id", 1)
        .execute()
    )

    if not result.data:
        supabase.table("account").upsert({
            "id": 1,
            "balance": STARTING_BALANCE
        }).execute()

    LAST_TRADE_EXECUTION.clear()

    return {
        "success": True,
        "balance": STARTING_BALANCE,
        "positions_cleared": True,
        "trades_cleared": True,
        "analysis_history_cleared": True,
        "message": "Paper account reset to $1,000.00."
    }


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
        current = ((price - current) * multiplier) + current

    return current


def rsi(values, period=14):
    if len(values) <= period:
        return 50.0

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    average_gain = sum(gains[-period:]) / period
    average_loss = sum(losses[-period:]) / period

    if average_loss == 0:
        return 100.0

    rs = average_gain / average_loss
    return 100 - (100 / (1 + rs))


def macd(values):
    if len(values) < 35:
        return 0.0, 0.0

    value = ema(values, 12) - ema(values, 26)
    series = []

    for i in range(26, len(values) + 1):
        subset = values[:i]
        series.append(ema(subset, 12) - ema(subset, 26))

    return value, ema(series, 9)


def atr(candles, period=14):
    if len(candles) < period + 1:
        return 0.0

    ranges = []

    for i in range(1, len(candles)):
        c = candles[i]
        p = candles[i - 1]

        high = float(c["high"])
        low = float(c["low"])
        prev = float(p["close"])

        ranges.append(
            max(
                high - low,
                abs(high - prev),
                abs(low - prev)
            )
        )

    return sum(ranges[-period:]) / period if len(ranges) >= period else 0.0


# ============================================================
# TWELVE DATA
# ============================================================

def twelve_data_error(
    data,
    fallback="Twelve Data returned an unknown error."
):
    if not isinstance(data, dict):
        return fallback

    parts = []

    if data.get("code"):
        parts.append(f"code {data['code']}")

    if data.get("message"):
        parts.append(str(data["message"]))

    if data.get("status") and data.get("status") != "ok":
        parts.append(f"status {data['status']}")

    return "Twelve Data: " + " — ".join(parts) if parts else fallback


def twelve_data_request(endpoint, params):
    if not TWELVE_DATA_API_KEY:
        raise RuntimeError(
            "TWELVE_DATA_API_KEY is not configured on Render."
        )

    p = dict(params)
    p["apikey"] = TWELVE_DATA_API_KEY

    try:
        response = requests.get(
            f"{TWELVE_DATA_URL}/{endpoint}",
            params=p,
            timeout=20
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
        raise RuntimeError(twelve_data_error(data))

    if "code" in data and data.get("code") not in (None, 200):
        raise RuntimeError(twelve_data_error(data))

    return data


def get_candles(market, interval="1h", outputsize=100):
    if market not in MARKETS:
        raise ValueError("Unsupported market.")

    cache_key = (market, interval, int(outputsize))
    cached = MARKET_DATA_CACHE.get(cache_key)

    if cached and (time.time() - cached["time"]) < DATA_CACHE_TTL_SECONDS:
        return cached["candles"], cached["interval"]

    info = MARKETS[market]
    symbol = info["symbol"]
    asset_type = info["asset_type"]

    try:
        data = twelve_data_request(
            "time_series",
            {
                "symbol": symbol,
                "interval": interval,
                "outputsize": outputsize,
                "format": "JSON"
            }
        )

        values = data.get("values")

        if values and len(values) >= 2:
            candles = list(reversed(values))

            MARKET_DATA_CACHE[cache_key] = {
                "time": time.time(),
                "candles": candles,
                "interval": interval
            }

            return candles, interval

        raise RuntimeError("Twelve Data returned no candle values.")

    except RuntimeError as exc:
        message = str(exc)

        if "429" in message or "Too Many Requests" in message:
            raise RuntimeError(
                f"{market} data is temporarily rate-limited by Twelve Data. "
                "Wait about 60 seconds and try again. The dashboard now caches "
                "recent market data to reduce API usage."
            ) from exc

        if asset_type == "commodity":
            raise RuntimeError(
                f"{market} commodity data is not available to the current "
                f"Twelve Data API key/plan. {message}"
            ) from exc

        raise RuntimeError(
            f"{market} market data could not be retrieved: {message}"
        ) from exc


def get_market_price(market):
    if market not in MARKETS:
        raise ValueError("Unsupported market.")

    cached = MARKET_PRICE_CACHE.get(market)

    if cached and (time.time() - cached["time"]) < PRICE_CACHE_TTL_SECONDS:
        return float(cached["price"])

    info = MARKETS[market]
    symbol = info["symbol"]
    asset_type = info["asset_type"]

    try:
        if asset_type == "forex":
            data = twelve_data_request(
                "exchange_rate",
                {"symbol": symbol}
            )

            if "rate" in data:
                price = float(data["rate"])
            else:
                raise RuntimeError(
                    "Twelve Data did not return a forex rate."
                )

        else:
            data = twelve_data_request(
                "price",
                {"symbol": symbol}
            )

            if "price" in data:
                price = float(data["price"])
            else:
                raise RuntimeError(
                    "Twelve Data did not return a price."
                )

        MARKET_PRICE_CACHE[market] = {
            "time": time.time(),
            "price": price
        }

        return price

    except RuntimeError as price_error:
        message = str(price_error)

        if "429" in message or "Too Many Requests" in message:
            raise RuntimeError(
                f"{market} price is temporarily rate-limited by Twelve Data. "
                "Wait about 60 seconds and try again."
            ) from price_error

        try:
            candles, _ = get_candles(market, "1h", 60)

            if candles:
                price = float(candles[-1]["close"])

                MARKET_PRICE_CACHE[market] = {
                    "time": time.time(),
                    "price": price
                }

                return price

        except Exception as candle_error:
            raise RuntimeError(
                f"Unable to get current price for {market}. "
                f"Price endpoint: {message}. "
                f"Candle fallback: {candle_error}"
            ) from candle_error

        raise RuntimeError(
            f"Unable to get current price for {market}: {message}"
        )


# ============================================================
# MARKET ANALYSIS
# ============================================================

def analyze_market(market):
    candles, data_interval = get_candles(market, "1h", 60)

    closes = [float(c["close"]) for c in candles]

    if len(closes) < 60:
        raise RuntimeError(
            f"Not enough candle data for {market}. "
            f"Received {len(closes)} candles."
        )

    price = closes[-1]
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    r = rsi(closes, 14)
    mv, ms = macd(closes)
    a = atr(candles, 14)

    score = 0
    reasons = []

    if e20 > e50:
        score += 1
        reasons.append("EMA20 is above EMA50.")
    else:
        score -= 1
        reasons.append("EMA20 is below EMA50.")

    if r > 55:
        score += 1
        reasons.append("RSI is above the neutral midpoint.")
    elif r < 45:
        score -= 1
        reasons.append("RSI is below the neutral midpoint.")
    else:
        reasons.append("RSI is near the neutral midpoint.")

    if mv > ms:
        score += 1
        reasons.append("MACD is above its signal line.")
    else:
        score -= 1
        reasons.append("MACD is below its signal line.")

    if price > e20:
        score += 1
        reasons.append("Price is above EMA20.")
    else:
        score -= 1
        reasons.append("Price is below EMA20.")

    signal = (
        "BUY" if score >= 3
        else "SELL" if score <= -3
        else "HOLD"
    )

    confidence = int(
        min(95, max(50, 50 + abs(score) * 10))
    )

    strength = (
        "BULLISH" if e20 > e50
        else "BEARISH" if e20 < e50
        else "NEUTRAL"
    )

    if a <= 0 or price <= 0:
        risk = "UNKNOWN"
    else:
        ap = (a / price) * 100
        risk = (
            "LOW" if ap < 1
            else "MEDIUM" if ap < 2.5
            else "HIGH"
        )

    long_sl = (
        max(0.0, price - a * STOP_LOSS_ATR_MULTIPLIER)
        if a > 0 else 0.0
    )
    long_tp = (
        price + a * TAKE_PROFIT_ATR_MULTIPLIER
        if a > 0 else 0.0
    )
    short_sl = (
        price + a * STOP_LOSS_ATR_MULTIPLIER
        if a > 0 else 0.0
    )
    short_tp = (
        max(0.0, price - a * TAKE_PROFIT_ATR_MULTIPLIER)
        if a > 0 else 0.0
    )

    if signal == "SELL":
        sl, tp = short_sl, short_tp
    else:
        sl, tp = long_sl, long_tp

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
        "ema20": e20,
        "ema50": e50,
        "rsi14": r,
        "macd": mv,
        "macd_signal": ms,
        "atr14": a,
        "atr": a,
        "stop_loss": sl,
        "take_profit": tp,
        "long_stop_loss": long_sl,
        "long_take_profit": long_tp,
        "short_stop_loss": short_sl,
        "short_take_profit": short_tp,
        "data_interval": data_interval,
        "reasons": reasons,
        "explanation": (
            f"{MARKETS[market]['name']} is currently "
            f"{strength.lower()} with a {signal} signal based on "
            "EMA, RSI, MACD and price structure."
        )
    }


# ============================================================
# PAPER TRADING
# ============================================================


def calculate_trade_amount():
    """Allocate at most 5% of equity to a single simulated position."""
    portfolio = get_portfolio()
    equity = max(0.0, float(portfolio.get("portfolio_value", get_balance())))
    available_cash = max(0.0, get_balance())
    return round(min(equity * RISK_PER_TRADE, available_cash), 2)


def paper_open(market, price, side, stop_loss, take_profit, signal):
    if get_position(market):
        return {
            "executed": False,
            "reason": "POSITION_ALREADY_OPEN",
            "message": f"{market} already has an open paper position."
        }

    if len(get_all_positions()) >= MAX_OPEN_POSITIONS:
        return {
            "executed": False,
            "reason": "MAX_OPEN_POSITIONS",
            "message": f"Maximum of {MAX_OPEN_POSITIONS} open paper positions reached."
        }

    balance = get_balance()
    amount = min(calculate_trade_amount(), balance)

    if amount < 10:
        return {
            "executed": False,
            "reason": "INSUFFICIENT_BALANCE",
            "message": f"Paper balance is ${balance:.2f}; not enough free cash."
        }

    quantity = amount / price

    # Both long and short paper positions reserve the same simulated capital.
    set_balance(balance - amount)

    supabase.table("positions").insert({
        "market": market,
        "quantity": quantity,
        "avg_price": price,
        "side": side,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "allocated_capital": amount
    }).execute()

    insert_trade(
        market,
        "BUY" if side == "LONG" else "SELL",
        quantity,
        price,
        amount,
        0.0,
        signal
    )

    return {
        "executed": True,
        "side": side,
        "market": market,
        "quantity": quantity,
        "price": price,
        "value": amount,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "message": f"Paper {side} opened automatically."
    }


def paper_close(market, price, signal):
    position = get_position(market)

    if not position:
        return {
            "executed": False,
            "signal_only": True,
            "reason": "NO_OPEN_POSITION",
            "message": f"No open paper position exists for {market}."
        }

    quantity = float(position["quantity"])
    avg = float(position["avg_price"])
    side = str(position.get("side") or "LONG").upper()
    allocated = float(position.get("allocated_capital") or TRADE_AMOUNT)

    if side == "SHORT":
        pnl = (avg - price) * quantity
        return_value = allocated + pnl
        close_side = "BUY"
    else:
        pnl = (price - avg) * quantity
        return_value = quantity * price
        close_side = "SELL"

    set_balance(get_balance() + return_value)

    supabase.table("positions").delete().eq(
        "market", market
    ).execute()

    insert_trade(
        market,
        close_side,
        quantity,
        price,
        return_value,
        pnl,
        signal
    )

    return {
        "executed": True,
        "side": close_side,
        "closed_position_side": side,
        "market": market,
        "quantity": quantity,
        "price": price,
        "value": return_value,
        "pnl": pnl,
        "message": f"Paper {side} position closed."
    }


def apply_risk_rules(market, analysis):
    position = get_position(market)
    if not position:
        return {"triggered": False}

    price = float(analysis["price"])
    side = str(position.get("side") or "LONG").upper()
    sl = float(position.get("stop_loss") or 0)
    tp = float(position.get("take_profit") or 0)

    # Legacy V5.3 positions did not have stored SL/TP. Set them once here.
    if sl <= 0 or tp <= 0:
        if side == "SHORT":
            sl = float(analysis.get("short_stop_loss") or 0)
            tp = float(analysis.get("short_take_profit") or 0)
        else:
            sl = float(analysis.get("long_stop_loss") or 0)
            tp = float(analysis.get("long_take_profit") or 0)

        supabase.table("positions").update({
            "stop_loss": sl,
            "take_profit": tp
        }).eq("market", market).execute()

    if side == "SHORT":
        if sl > 0 and price >= sl:
            result = paper_close(market, price, "STOP_LOSS")
            return {"triggered": True, "reason": "STOP LOSS", "result": result}
        if tp > 0 and price <= tp:
            result = paper_close(market, price, "TAKE_PROFIT")
            return {"triggered": True, "reason": "TAKE PROFIT", "result": result}
    else:
        if sl > 0 and price <= sl:
            result = paper_close(market, price, "STOP_LOSS")
            return {"triggered": True, "reason": "STOP LOSS", "result": result}
        if tp > 0 and price >= tp:
            result = paper_close(market, price, "TAKE_PROFIT")
            return {"triggered": True, "reason": "TAKE PROFIT", "result": result}

    return {"triggered": False}


def check_trade_protection(market):
    remaining = (
        TRADE_COOLDOWN_SECONDS
        - (time.time() - LAST_TRADE_EXECUTION.get(market, 0))
    )

    if remaining > 0:
        return {
            "allowed": False,
            "reason": "COOLDOWN",
            "remaining_seconds": round(remaining, 2)
        }

    return {"allowed": True}


def execute_trade_decision(market, analysis, autonomous=False):
    signal = analysis["signal"]
    price = float(analysis["price"])

    with TRADE_EXECUTION_LOCK:
        protection = check_trade_protection(market)

        if not protection["allowed"] and not autonomous:
            return {
                "executed": False,
                "reason": protection["reason"],
                "remaining_seconds": protection["remaining_seconds"],
                "message": "Trade protection is active."
            }

        LAST_TRADE_EXECUTION[market] = time.time()

        # Always protect an existing position before considering a new trade.
        risk = apply_risk_rules(market, analysis)
        if risk.get("triggered"):
            return {
                "executed": risk["result"].get("executed", False),
                "risk_action": risk
            }

        position = get_position(market)

        # No position: open in the direction of a strong signal.
        if not position:
            if signal == "BUY":
                return paper_open(
                    market,
                    price,
                    "LONG",
                    float(analysis.get("long_stop_loss") or analysis["stop_loss"]),
                    float(analysis.get("long_take_profit") or analysis["take_profit"]),
                    "BUY"
                )

            if signal == "SELL":
                return paper_open(
                    market,
                    price,
                    "SHORT",
                    float(analysis.get("short_stop_loss") or analysis["stop_loss"]),
                    float(analysis.get("short_take_profit") or analysis["take_profit"]),
                    "SELL"
                )

            return {
                "executed": False,
                "reason": "HOLD",
                "message": "AI signal is HOLD. No paper order was executed."
            }

        side = str(position.get("side") or "LONG").upper()

        # Existing long + bearish signal -> close the long.
        if side == "LONG" and signal == "SELL":
            return paper_close(market, price, "AI_EXIT")

        # Existing short + bullish signal -> close the short.
        if side == "SHORT" and signal == "BUY":
            return paper_close(market, price, "AI_EXIT")

        return {
            "executed": False,
            "reason": "POSITION_HELD",
            "message": f"{side} position kept open; signal={signal}."
        }


AUTONOMOUS_SCAN_LOCK = threading.Lock()

def autonomous_scan():
    """Scan every configured market and manage paper positions automatically."""
    results = []
    started = datetime.now(timezone.utc).isoformat()

    # Protect/manage positions that were already open when this scan began.
    initially_open = {p["market"] for p in get_all_positions()}
    for pos in get_all_positions():
        market = pos["market"]
        try:
            analysis = analyze_market(market)
            trade = execute_trade_decision(market, analysis, autonomous=True)
            action = "PAPER_TRADE_EXECUTED" if trade.get("executed") else trade.get("reason", "MONITOR")
            if trade.get("risk_action", {}).get("triggered"):
                action = "RISK_EXIT"
            results.append({
                "market": market,
                "signal": analysis["signal"],
                "confidence": analysis["confidence"],
                "action": action,
                "trade": trade
            })
        except Exception as exc:
            results.append({
                "market": market,
                "action": "ERROR",
                "error": str(exc)
            })

    # Then scan all configured markets for new opportunities.
    for market in MARKETS:
        try:
            analysis = analyze_market(market)

            existing = get_position(market)
            if existing or market in initially_open:
                # Do not reopen/reverse a position in the same scan cycle.
                continue

            if (
                analysis["signal"] in ("BUY", "SELL")
                and int(analysis["confidence"]) >= AUTOPILOT_MIN_CONFIDENCE
            ):
                trade = execute_trade_decision(
                    market,
                    analysis,
                    autonomous=True
                )
            else:
                trade = {
                    "executed": False,
                    "reason": "FILTERED",
                    "message": "No strong enough setup."
                }

            status = "PAPER_TRADE_EXECUTED" if trade.get("executed") else trade.get("reason", "NO_TRADE")

            insert_analysis_history(
                market,
                analysis.get("signal", "UNKNOWN"),
                analysis.get("price"),
                analysis.get("confidence"),
                analysis.get("risk", ""),
                status,
                trade.get("message", ""),
                analysis.get("data_interval", "")
            )

            results.append({
                "market": market,
                "signal": analysis["signal"],
                "confidence": analysis["confidence"],
                "risk": analysis["risk"],
                "action": status,
                "trade": trade
            })

        except Exception as exc:
            msg = str(exc)
            try:
                insert_analysis_history(
                    market, "ERROR", None, None, "",
                    "DATA_ERROR", msg, ""
                )
            except Exception:
                pass
            results.append({
                "market": market,
                "action": "DATA_ERROR",
                "error": msg
            })

    return {
        "success": True,
        "paper_only": True,
        "real_orders_disabled": True,
        "started_at": started,
        "markets_scanned": len(MARKETS),
        "max_open_positions": MAX_OPEN_POSITIONS,
        "risk_per_trade": RISK_PER_TRADE,
        "results": results,
        "portfolio": get_portfolio()
    }


def autopilot_worker():
    print("TradeMind V7 autopilot worker started (single-process mode).")
    while True:
        try:
            if AUTONOMOUS_SCAN_LOCK.acquire(blocking=False):
                try:
                    autonomous_scan()
                finally:
                    AUTONOMOUS_SCAN_LOCK.release()
            else:
                print("AUTOPILOT: scan already running; skipping duplicate cycle.")
        except Exception as exc:
            print("AUTOPILOT ERROR:", exc)
        time.sleep(AUTOPILOT_INTERVAL_SECONDS)

# ============================================================
# PORTFOLIO
# ============================================================

def get_portfolio():
    balance = get_balance()
    positions = []
    pv = 0.0
    pnl_total = 0.0

    for pos in get_all_positions():
        market = pos["market"]
        qty = float(pos["quantity"])
        avg = float(pos["avg_price"])
        side = str(pos.get("side") or "LONG").upper()
        allocated = float(pos.get("allocated_capital") or TRADE_AMOUNT)

        try:
            current = get_market_price(market)

            if side == "SHORT":
                value = allocated + ((avg - current) * qty)
                pnl = (avg - current) * qty
            else:
                value = qty * current
                pnl = (current - avg) * qty

            pv += value
            pnl_total += pnl

            positions.append({
                "market": market,
                "side": side,
                "quantity": qty,
                "avg_price": avg,
                "current_price": current,
                "value": value,
                "allocated_capital": allocated,
                "stop_loss": pos.get("stop_loss"),
                "take_profit": pos.get("take_profit"),
                "unrealized_pnl": pnl,
                "pnl": pnl,
                "error": None
            })

        except Exception as exc:
            # Preserve the reserved capital estimate when live price data is unavailable.
            pv += allocated
            positions.append({
                "market": market,
                "side": side,
                "quantity": qty,
                "avg_price": avg,
                "current_price": None,
                "value": allocated,
                "allocated_capital": allocated,
                "stop_loss": pos.get("stop_loss"),
                "take_profit": pos.get("take_profit"),
                "unrealized_pnl": 0.0,
                "pnl": 0.0,
                "error": str(exc)
            })

    total = balance + pv

    return {
        "balance": balance,
        "cash": balance,
        "positions_value": pv,
        "portfolio_value": total,
        "total_value": total,
        "unrealized_pnl": pnl_total,
        "open_positions": len(positions),
        "positions": positions
    }


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def dashboard():
    return render_template(
        "index.html",
        markets=MARKETS,
        selected_market="BTC/USD",
        price=0.0,
        portfolio=get_portfolio()
    )


@app.route("/api/run", methods=["POST"])
def api_run():
    payload = request.get_json(silent=True) or {}
    market = str(payload.get("market", "BTC/USD")).upper()

    if market not in MARKETS:
        return jsonify({
            "success": False,
            "error": "Unsupported market.",
            "market": market,
            "available_markets": list(MARKETS.keys())
        }), 400

    try:
        analysis = analyze_market(market)
        trade = execute_trade_decision(market, analysis, autonomous=False)

        status = "NO_TRADE"

        if trade.get("executed"):
            status = "PAPER_TRADE_EXECUTED"
        elif trade.get("reason") in (
            "HOLD",
            "COOLDOWN",
            "POSITION_ALREADY_OPEN"
        ):
            status = trade["reason"]
        elif trade.get("signal_only"):
            status = "SIGNAL_ONLY"
        elif trade.get("reason"):
            status = str(trade["reason"])

        insert_analysis_history(
            market,
            analysis.get("signal", "UNKNOWN"),
            analysis.get("price"),
            analysis.get("confidence"),
            analysis.get("risk", ""),
            status,
            trade.get("message", ""),
            analysis.get("data_interval", "")
        )

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
        msg = str(exc)

        insert_analysis_history(
            market,
            "ERROR",
            None,
            None,
            "",
            "DATA_ERROR",
            msg,
            ""
        )

        return jsonify({
            "success": False,
            "paper_only": True,
            "real_orders_disabled": True,
            "market": market,
            "error": msg,
            "error_type": type(exc).__name__
        }), 502


@app.route("/api/autonomous-run", methods=["POST"])
def api_autonomous_run():
    try:
        if not AUTONOMOUS_SCAN_LOCK.acquire(blocking=False):
            return jsonify({"success": False, "paper_only": True, "real_orders_disabled": True, "error": "A market scan is already running."}), 409
        try:
            result = autonomous_scan()
        finally:
            AUTONOMOUS_SCAN_LOCK.release()
        return jsonify(result)
    except Exception as exc:
        return jsonify({
            "success": False,
            "paper_only": True,
            "real_orders_disabled": True,
            "error": str(exc)
        }), 500


@app.route("/api/autopilot-status")
def api_autopilot_status():
    return jsonify({
        "success": True,
        "enabled": AUTOPILOT_ENABLED,
        "interval_seconds": AUTOPILOT_INTERVAL_SECONDS,
        "min_confidence": AUTOPILOT_MIN_CONFIDENCE,
        "max_open_positions": MAX_OPEN_POSITIONS,
        "risk_per_trade": RISK_PER_TRADE,
        "paper_only": True,
        "real_orders_disabled": True
    })


@app.route("/api/price")
def api_price():
    market = str(
        request.args.get("market", "BTC/USD")
    ).upper()

    if market not in MARKETS:
        return jsonify({
            "success": False,
            "error": "Unsupported market."
        }), 400

    try:
        return jsonify({
            "success": True,
            "market": market,
            "price": get_market_price(market),
            "paper_only": True
        })

    except Exception as exc:
        return jsonify({
            "success": False,
            "market": market,
            "error": str(exc)
        }), 502


@app.route("/api/analysis")
def api_analysis():
    market = str(
        request.args.get("market", "BTC/USD")
    ).upper()

    if market not in MARKETS:
        return jsonify({
            "success": False,
            "error": "Unsupported market."
        }), 400

    try:
        return jsonify({
            "success": True,
            "analysis": analyze_market(market)
        })

    except Exception as exc:
        return jsonify({
            "success": False,
            "market": market,
            "error": str(exc)
        }), 502


@app.route("/api/portfolio")
def api_portfolio():
    try:
        portfolio = get_portfolio()
        return jsonify({
            "success": True,
            "paper_only": True,
            "real_orders_disabled": True,
            "portfolio": portfolio,
            **portfolio
        })

    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc)
        }), 500


@app.route("/api/trades")
def api_trades():
    try:
        limit = max(
            1,
            min(int(request.args.get("limit", 100)), 500)
        )

        return jsonify({
            "success": True,
            "trades": get_trade_history(limit)
        })

    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc)
        }), 500


@app.route("/api/analysis-history")
def api_analysis_history():
    try:
        limit = max(
            1,
            min(int(request.args.get("limit", 100)), 500)
        )

        return jsonify({
            "success": True,
            "analysis_history": get_analysis_history(limit)
        })

    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc)
        }), 500


@app.route("/api/history")
def api_history():
    try:
        limit = max(
            1,
            min(int(request.args.get("limit", 100)), 500)
        )

        return jsonify({
            "success": True,
            "history": get_combined_history(limit)
        })

    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc)
        }), 500


@app.route("/api/paper-account")
def api_paper_account():
    try:
        return jsonify({
            "success": True,
            "paper_only": True,
            "real_orders_disabled": True,
            "starting_balance": STARTING_BALANCE,
            "trade_amount": TRADE_AMOUNT,
            "account": get_portfolio()
        })

    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc)
        }), 500


@app.route("/api/reset-paper", methods=["POST"])
def api_reset_paper():
    try:
        return jsonify(reset_paper_account())

    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc)
        }), 500


@app.route("/api/market-status")
def api_market_status():
    results = []

    for market, info in MARKETS.items():
        try:
            candles, interval = get_candles(
                market,
                "1h",
                5
            )

            results.append({
                "market": market,
                "name": info["name"],
                "asset_type": info["asset_type"],
                "available": True,
                "data_interval": interval,
                "price": (
                    float(candles[-1]["close"])
                    if candles else None
                ),
                "error": None
            })

        except Exception as exc:
            results.append({
                "market": market,
                "name": info["name"],
                "asset_type": info["asset_type"],
                "available": False,
                "data_interval": None,
                "price": None,
                "error": str(exc)
            })

    return jsonify({
        "success": True,
        "markets": results,
        "paper_only": True,
        "real_orders_disabled": True
    })


@app.route("/api/broker-status")
def api_broker_status():
    # Integration placeholder only. No broker credentials or order execution are enabled.
    return jsonify({
        "success": True,
        "connected": False,
        "mode": "PAPER_ONLY",
        "real_orders_enabled": False,
        "message": "No broker is connected. Real-money execution is deliberately disabled."
    })


@app.route("/api/markets")
def api_markets():
    return jsonify({
        "success": True,
        "markets": MARKETS
    })


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "version": "TradeMind V7 Autonomous",
        "storage": "Supabase",
        "paper_only": True,
        "real_orders_disabled": True,
        "markets": list(MARKETS.keys()),
        "trade_cooldown_seconds": TRADE_COOLDOWN_SECONDS,
        "duplicate_protection": True,
        "analysis_history": True,
        "combined_history": True,
        "persistent_storage": True,
        "autopilot_enabled": AUTOPILOT_ENABLED,
        "autopilot_interval_seconds": AUTOPILOT_INTERVAL_SECONDS,
        "max_open_positions": MAX_OPEN_POSITIONS,
        "risk_per_trade": RISK_PER_TRADE
    })


@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "success": False,
        "error": "Route not found."
    }), 404


@app.errorhandler(500)
def server_error(error):
    return jsonify({
        "success": False,
        "error": "Internal server error."
    }), 500


# ============================================================
# STARTUP
# ============================================================

initialize_database()

if os.getenv(
    "TRADEMIND_RESET_PAPER_ACCOUNT",
    ""
).strip().lower() == "true":
    reset_paper_account()

print("=" * 60)
print("TradeMind V7 Autonomous started")
print("PAPER TRADING ONLY")
print("REAL ORDERS DISABLED")
print("STORAGE: SUPABASE")
print("Autopilot:", AUTOPILOT_ENABLED)
print("Markets:", ", ".join(MARKETS.keys()))
print("=" * 60)

if AUTOPILOT_ENABLED:
    worker = threading.Thread(
        target=autopilot_worker,
        daemon=True,
        name="trademind-autopilot"
    )
    worker.start()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=False
)
    
