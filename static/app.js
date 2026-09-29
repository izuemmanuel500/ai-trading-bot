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

        const confidence =
            data.confidence !== undefined &&
            data.confidence !== null
                ? Number(data.confidence).toFixed(0) + "%"
                : "N/A";

        const rsi =
            data.rsi !== undefined &&
            data.rsi !== null
                ? Number(data.rsi).toFixed(2)
                : "N/A";

        const emaFast =
            data.ema_fast !== undefined &&
            data.ema_fast !== null
                ? Number(data.ema_fast).toFixed(4)
                : "N/A";

        const emaSlow =
            data.ema_slow !== undefined &&
            data.ema_slow !== null
                ? Number(data.ema_slow).toFixed(4)
                : "N/A";

        const macd =
            data.macd !== undefined &&
            data.macd !== null
                ? Number(data.macd).toFixed(6)
                : "N/A";

        const macdSignal =
            data.macd_signal !== undefined &&
            data.macd_signal !== null
                ? Number(data.macd_signal).toFixed(6)
                : "N/A";

        const atr =
            data.atr !== undefined &&
            data.atr !== null
                ? Number(data.atr).toFixed(4)
                : "N/A";

        const stopLoss =
            data.stop_loss !== undefined &&
            data.stop_loss !== null
                ? Number(data.stop_loss).toFixed(4)
                : "N/A";

        const takeProfit =
            data.take_profit !== undefined &&
            data.take_profit !== null
                ? Number(data.take_profit).toFixed(4)
                : "N/A";

        const message =
            data.message ||
            "No additional analysis message.";

        const tradeAction =
            data.trade &&
            data.trade.action
                ? data.trade.action
                : "NONE";

        const tradeMessage =
            data.trade &&
            data.trade.message
                ? data.trade.message
                : "";

        alert(
            "🤖 TradeMind AI V2\n\n" +

            "Market: " + data.market + "\n" +
            "Signal: " + data.signal + "\n" +
            "Price: $" + price.toFixed(4) + "\n\n" +

            "Confidence: " + confidence + "\n" +
            "Market Strength: " +
                (data.market_strength || "N/A") + "\n" +
            "Risk Level: " +
                (data.risk_level || "N/A") + "\n\n" +

            "EMA Fast: " + emaFast + "\n" +
            "EMA Slow: " + emaSlow + "\n" +
            "RSI: " + rsi + "\n" +
            "MACD: " + macd + "\n" +
            "MACD Signal: " + macdSignal + "\n" +
            "ATR: " + atr + "\n\n" +

            "Stop Loss: " + stopLoss + "\n" +
            "Take Profit: " + takeProfit + "\n\n" +

            "Trade Action: " + tradeAction + "\n" +
            (tradeMessage
                ? tradeMessage + "\n\n"
                : "\n") +

            "Reason:\n" +
            message
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
            button.textContent = "🤖 Run AI Trader";
        }
    }
}


function changeMarket() {
    const marketSelect =
        document.querySelector("#marketSelect");

    if (!marketSelect) {
        return;
    }

    const market = encodeURIComponent(
        marketSelect.value
    );

    window.location.href =
        "/?market=" + market;
}


function selectMarket(symbol) {
    const market = encodeURIComponent(symbol);

    window.location.href =
        "/?market=" + market;
            }
