async function runBot() {
    const button = document.querySelector("#runBotButton");
    const marketSelect = document.querySelector("#marketSelect");

    const market = marketSelect
        ? marketSelect.value
        : "BTC/USD";

    try {
        if (button) {
            button.disabled = true;
            button.textContent = "Running AI Trader...";
        }

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

        alert(
            "AI Trader Result\n\n" +
            "Market: " + data.market + "\n" +
            "Signal: " + data.signal + "\n" +
            "Price: $" + Number(data.price).toFixed(4) + "\n\n" +
            data.message
        );

        window.location.reload();

    } catch (error) {

        alert(
            "Bot error: " + error.message
        );

    } finally {

        if (button) {
            button.disabled = false;
            button.textContent = "Run AI Trader";
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
