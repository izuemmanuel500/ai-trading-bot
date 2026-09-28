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
        const controller = new AbortController();

        // Stop waiting forever if the server takes too long
        const timeout = setTimeout(() => {
            controller.abort();
        }, 30000);

        const response = await fetch("/api/run", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                market: market
            }),
            signal: controller.signal
        });

        clearTimeout(timeout);

        const data = await response.json();

        if (!response.ok) {
            throw new Error(
                data.error || "Market analysis failed"
            );
        }

        alert(
            "🤖 TradeMind AI Result\n\n" +
            "Market: " + data.market + "\n" +
            "Signal: " + data.signal + "\n" +
            "Price: $" + Number(data.price).toFixed(4) + "\n\n" +
            data.message
        );

        // Refresh dashboard after successful analysis
        window.location.reload();

    } catch (error) {

        if (error.name === "AbortError") {
            alert(
                "⏱️ TradeMind took too long to respond.\n\n" +
                "The server may still be processing the analysis. " +
                "Please try again in a moment."
            );
        } else {
            alert(
                "❌ Bot error:\n\n" +
                error.message
            );
        }

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
