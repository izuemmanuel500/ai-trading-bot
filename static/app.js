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
                data.error || "Market analysis failed"
            );
        }

        const reasons = Array.isArray(data.reasons)
            ? data.reasons
            : [];

        const reasonText = reasons.length > 0
            ? reasons.map(reason => "• " + reason).join("\n")
            : "No reasons returned.";

        alert(
            "🤖 TradeMind AI Result\n\n" +
            "Market: " + data.market + "\n" +
            "Signal: " + data.signal + "\n" +
            "Price: $" + Number(data.price).toFixed(4) + "\n" +
            "Confidence: " + data.confidence + "%\n" +
            "Market Strength: " + data.market_strength + "\n" +
            "Risk Level: " + data.risk_level + "\n\n" +
            "📊 REASONS\n" +
            reasonText + "\n\n" +
            "💬 " + data.message
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

    if (!marketSelect) return;

    const market =
        encodeURIComponent(marketSelect.value);

    window.location.href =
        "/?market=" + market;
}


function selectMarket(symbol) {
    const market =
        encodeURIComponent(symbol);

    window.location.href =
        "/?market=" + market;
            }
