async function runBot() {
    const button = document.querySelector("#runBotButton");
    const marketSelect = document.querySelector("#marketSelect");

    const market = marketSelect
        ? marketSelect.value
        : "BTC/USD";

    if (button) {
        button.disabled = true;
        button.textContent = "Analyzing...";
    }

    try {
        const response = await fetch("/api/run", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                market: market
            })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(
                data.error || "Market analysis failed."
            );
        }

        const price = Number(data.price);

        const emaFast = Number(data.ema_fast);
        const emaSlow = Number(data.ema_slow);
        const rsi = Number(data.rsi);
        const macd = Number(data.macd);
        const macdSignal = Number(data.macd_signal);
        const atr = Number(data.atr);

        /*
         * Build the explanation directly from
         * the actual indicator values.
         */
        const reasons = [];

        if (
            Number.isFinite(emaFast) &&
            Number.isFinite(emaSlow)
        ) {
            if (emaFast > emaSlow) {
                reasons.push(
                    "EMA trend is bullish"
                );
            } else if (emaFast < emaSlow) {
                reasons.push(
                    "EMA trend is bearish"
                );
            } else {
                reasons.push(
                    "EMA trend is flat"
                );
            }
        }

        if (Number.isFinite(rsi)) {
            if (rsi < 30) {
                reasons.push(
                    "RSI is oversold"
                );
            } else if (rsi > 70) {
                reasons.push(
                    "RSI is overbought"
                );
            } else {
                reasons.push(
                    "RSI is neutral"
                );
            }
        }

        if (
            Number.isFinite(macd) &&
            Number.isFinite(macdSignal)
        ) {
            if (macd > macdSignal) {
                reasons.push(
                    "MACD momentum is bullish"
                );
            } else if (macd < macdSignal) {
                reasons.push(
                    "MACD momentum is bearish"
                );
            } else {
                reasons.push(
                    "MACD momentum is neutral"
                );
            }
        }

        let reasonText;

        if (reasons.length > 0) {
            reasonText =
                reasons
                    .map(function(reason) {
                        return "• " + reason;
                    })
                    .join("\n");
        } else {
            reasonText =
                "Indicator data was not available.";
        }

        const confidence =
            data.confidence != null
                ? Number(data.confidence).toFixed(0) + "%"
                : "N/A";

        const stopLoss =
            data.stop_loss != null
                ? Number(data.stop_loss).toFixed(4)
                : "N/A";

        const takeProfit =
            data.take_profit != null
                ? Number(data.take_profit).toFixed(4)
                : "N/A";

        const tradeAction =
            data.trade &&
            data.trade.action
                ? data.trade.action
                : "NONE";

        const tradeMessage =
            data.trade &&
            data.trade.message
                ? data.trade.message
                : "No trade executed.";

        alert(
            "🤖 TradeMind AI V2\n\n" +

            "Market: " +
            data.market +
            "\n" +

            "Signal: " +
            data.signal +
            "\n" +

            "Price: $" +
            price.toFixed(4) +
            "\n\n" +

            "Confidence: " +
            confidence +
            "\n" +

            "Market Strength: " +
            (data.market_strength || "N/A") +
            "\n" +

            "Risk Level: " +
            (data.risk_level || "N/A") +
            "\n\n" +

            "EMA Fast: " +
            (Number.isFinite(emaFast)
                ? emaFast.toFixed(4)
                : "N/A") +
            "\n" +

            "EMA Slow: " +
            (Number.isFinite(emaSlow)
                ? emaSlow.toFixed(4)
                : "N/A") +
            "\n" +

            "RSI: " +
            (Number.isFinite(rsi)
                ? rsi.toFixed(2)
                : "N/A") +
            "\n" +

            "MACD: " +
            (Number.isFinite(macd)
                ? macd.toFixed(6)
                : "N/A") +
            "\n" +

            "MACD Signal: " +
            (Number.isFinite(macdSignal)
                ? macdSignal.toFixed(6)
                : "N/A") +
            "\n" +

            "ATR: " +
            (Number.isFinite(atr)
                ? atr.toFixed(4)
                : "N/A") +
            "\n\n" +

            "Stop Loss: " +
            stopLoss +
            "\n" +

            "Take Profit: " +
            takeProfit +
            "\n\n" +

            "Trade Action: " +
            tradeAction +
            "\n" +

            tradeMessage +
            "\n\n" +

            "REASON:\n" +
            reasonText
        );

        window.location.reload();

    } catch (error) {
        alert(
            "❌ Bot error:\n\n" +
            error.message
        );
    } finally {
        if (button) {
            button.disabled = false;
            button.textContent =
                "🤖 Run AI Trader";
        }
    }
}


function changeMarket() {
    const marketSelect =
        document.querySelector("#marketSelect");

    if (!marketSelect) {
        return;
    }

    const market =
        encodeURIComponent(
            marketSelect.value
        );

    window.location.href =
        "/?market=" + market;
}


function selectMarket(symbol) {
    const market =
        encodeURIComponent(symbol);

    window.location.href =
        "/?market=" + market;
            }
