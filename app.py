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
        .select("market,quantity,avg_price")
        .eq("market", market)
        .limit(1)
        .execute()
    )
    row = _single(result)
    return dict(row) if row else None


def get_all_positions():
    result = (
        supabase.table("positions")
        .select("market,quantity,avg_price")
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

    sl = (
        max(0.0, price - a * STOP_LOSS_ATR_MULTIPLIER)
        if a > 0 else 0.0
    )

    tp = (
        price + a * TAKE_PROFIT_ATR_MULTIPLIER
        if a > 0 else 0.0
    )

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

def paper_buy(market, price, signal="BUY"):
    if get_position(market):
        return {
            "executed": False,
            "reason": "POSITION_ALREADY_OPEN",
            "message": f"{market} already has an open paper position."
        }

    balance = get_balance()

    if balance < TRADE_AMOUNT:
        return {
            "executed": False,
            "reason": "INSUFFICIENT_BALANCE",
            "message": (
                f"Paper balance is ${balance:.2f}; "
                f"${TRADE_AMOUNT:.2f} is required."
            )
        }

    quantity = TRADE_AMOUNT / price

    set_balance(balance - TRADE_AMOUNT)

    supabase.table("positions").insert({
        "market": market,
        "quantity": quantity,
        "avg_price": price
    }).execute()

    insert_trade(
        market,
        "BUY",
        quantity,
        price,
        TRADE_AMOUNT,
        0.0,
        signal
    )

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
            "reason": "NO_OPEN_POSITION",
            "message": f"No open paper position exists for {market}."
        }

    quantity = float(position["quantity"])
    avg = float(position["avg_price"])

    value = quantity * price
    pnl = (price - avg) * quantity

    set_balance(get_balance() + value)

    supabase.table("positions").delete().eq(
        "market", market
    ).execute()

    insert_trade(
        market,
        "SELL",
        quantity,
        price,
        value,
        pnl,
        signal
    )

    return {
        "executed": True,
        "side": "SELL",
        "market": market,
        "quantity": quantity,
        "price": price,
        "value": value,
        "pnl": pnl
    }


def apply_risk_rules(market, analysis):
    if not get_position(market):
        return {"triggered": False}

    price = float(analysis["price"])
    sl = float(analysis["stop_loss"])
    tp = float(analysis["take_profit"])

    if sl > 0 and price <= sl:
        result = paper_sell(market, price, "STOP_LOSS")
        return {
            "triggered": True,
            "reason": "STOP LOSS",
            "result": result
        }

    if tp > 0 and price >= tp:
        result = paper_sell(market, price, "TAKE_PROFIT")
        return {
            "triggered": True,
            "reason": "TAKE PROFIT",
            "result": result
        }

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


def execute_trade_decision(market, analysis):
    signal = analysis["signal"]
    price = float(analysis["price"])

    with TRADE_EXECUTION_LOCK:
        protection = check_trade_protection(market)

        if not protection["allowed"]:
            return {
                "executed": False,
                "reason": protection["reason"],
                "remaining_seconds": protection["remaining_seconds"],
                "message": (
                    "Trade protection is active. "
                    "Please wait before running again."
                )
            }

        LAST_TRADE_EXECUTION[market] = time.time()

        risk = apply_risk_rules(market, analysis)

        if risk.get("triggered"):
            return {
                "executed": risk["result"].get("executed", False),
                "risk_action": risk
            }

        if signal == "BUY":
            return paper_buy(market, price, "BUY")

        if signal == "SELL":
            return paper_sell(market, price, "SELL")

        return {
            "executed": False,
            "reason": "HOLD",
            "message": (
                "AI signal is HOLD. "
                "No paper order was executed."
            )
        }


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

        try:
            current = get_market_price(market)
            value = qty * current
            pnl = (current - avg) * qty

            pv += value
            pnl_total += pnl

            positions.append({
                "market": market,
                "quantity": qty,
                "avg_price": avg,
                "current_price": current,
                "value": value,
                "unrealized_pnl": pnl,
                "pnl": pnl,
                "error": None
            })

        except Exception as exc:
            positions.append({
                "market": market,
                "quantity": qty,
                "avg_price": avg,
                "current_price": None,
                "value": 0.0,
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
        trade = execute_trade_decision(market, analysis)

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
        return jsonify({
            "success": True,
            "paper_only": True,
            "real_orders_disabled": True,
            "portfolio": get_portfolio()
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
        "version": "TradeMind V5.3",
        "storage": "Supabase",
        "paper_only": True,
        "real_orders_disabled": True,
        "markets": list(MARKETS.keys()),
        "trade_cooldown_seconds": TRADE_COOLDOWN_SECONDS,
        "duplicate_protection": True,
        "analysis_history": True,
        "combined_history": True,
        "persistent_storage": True
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
print("TradeMind V5.3 started")
print("PAPER TRADING ONLY")
print("REAL ORDERS DISABLED")
print("STORAGE: SUPABASE")
print("Markets:", ", ".join(MARKETS.keys()))
print("=" * 60)

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=False
        )
                 
