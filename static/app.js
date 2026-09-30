async function runBot() {
    const button = document.querySelector("#runBotButton");
    const marketSelect = document.querySelector("#marketSelect");

    const market = marketSelect
        ? marketSelect.value
        : "BTC/USD";

    if (button) {
        button.disabled = true;
        button.textContent = "🤖 Analyzing...";
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

        const analysisArea =
            document.querySelector("#analysisResult");

        const reasons = Array.isArray(data.reasons)
            ? data.reasons
            : [];

        const reasonHTML = reasons.length > 0
            ? reasons.map(function (reason) {
                return `<li>${escapeHTML(reason)}</li>`;
            }).join("")
            : "<li>No reasons returned.</li>";

        if (analysisArea) {

            analysisArea.innerHTML = `
                <div class="analysis-result">

                    <div class="analysis-header">
                        <span>🤖 TradeMind AI</span>
                        <span class="live-badge">LIVE</span>
                    </div>

                    <div class="signal-box">

                        <div class="signal-label">
                            SIGNAL
                        </div>

                        <div class="signal-value">
                            ${escapeHTML(
                                data.signal || "HOLD"
                            )}
                        </div>

                    </div>

                    <div class="analysis-grid">

                        <div class="analysis-item">
                            <span>Market</span>
                            <strong>
                                ${escapeHTML(
                                    data.market || market
                                )}
                            </strong>
                        </div>

                        <div class="analysis-item">
                            <span>Price</span>
                            <strong>
                                $${Number(
                                    data.price || 0
                                ).toFixed(4)}
                            </strong>
                        </div>

                        <div class="analysis-item">
                            <span>Confidence</span>
                            <strong>
                                ${Number(
                                    data.confidence || 0
                                )}%
                            </strong>
                        </div>

                        <div class="analysis-item">
                            <span>Market Strength</span>
                            <strong>
                                ${escapeHTML(
                                    data.market_strength ||
                                    "NEUTRAL"
                                )}
                            </strong>
                        </div>

                        <div class="analysis-item">
                            <span>Risk Level</span>
                            <strong>
                                ${escapeHTML(
                                    data.risk_level ||
                                    "LOW"
                                )}
                            </strong>
                        </div>

                    </div>

                    <div class="reasons-section">

                        <h3>
                            📊 Why TradeMind Chose This
                        </h3>

                        <ul>
                            ${reasonHTML}
                        </ul>

                    </div>

                    <div class="message-section">

                        <h3>
                            💬 AI Explanation
                        </h3>

                        <p>
                            ${escapeHTML(
                                data.message ||
                                data.explanation ||
                                "No explanation available."
                            )}
                        </p>

                    </div>

                    ${
                        data.paper_only
                            ? `
                            <div class="paper-warning">
                                ⚠️ PAPER TRADING ONLY
                            </div>
                            `
                            : ""
                    }

                </div>
            `;

        } else {

            alert(
                "🤖 TradeMind AI Result\n\n" +
                "Market: " +
                (data.market || market) +
                "\n" +
                "Signal: " +
                (data.signal || "HOLD") +
                "\n" +
                "Price: $" +
                Number(data.price || 0).toFixed(4) +
                "\n" +
                "Confidence: " +
                (data.confidence || 0) +
                "%\n" +
                "Market Strength: " +
                (data.market_strength || "NEUTRAL") +
                "\n" +
                "Risk Level: " +
                (data.risk_level || "LOW") +
                "\n\n" +
                "📊 REASONS\n" +
                reasons.map(function (reason) {
                    return "• " + reason;
                }).join("\n") +
                "\n\n💬 " +
                (data.message || "")
            );
        }

        await refreshDashboard();

    } catch (error) {

        console.error(
            "TradeMind error:",
            error
        );

        alert(
            "❌ TradeMind error:\n\n" +
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


/* Safely display server text */
function escapeHTML(value) {

    return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}


/* Refresh portfolio information */
async function refreshDashboard() {

    try {

        const response =
            await fetch("/api/portfolio");

        if (!response.ok) {
            return;
        }

        const data =
            await response.json();

        updateElement(
            "#cashValue",
            formatMoney(
                data.cash !== undefined
                    ? data.cash
                    : data.balance
            )
        );

        updateElement(
            "#portfolioValue",
            formatMoney(
                data.total_value !== undefined
                    ? data.total_value
                    : data.balance
            )
        );

        const positions =
            Array.isArray(data.positions)
                ? data.positions
                : [];

        updateElement(
            "#positionsCount",
            positions.length
        );

    } catch (error) {

        console.log(
            "Portfolio refresh skipped:",
            error.message
        );
    }
}


/* Update an element */
function updateElement(selector, value) {

    const element =
        document.querySelector(selector);

    if (element) {
        element.textContent = value;
    }
}


/* Format money */
function formatMoney(value) {

    const number =
        Number(value || 0);

    return "$" + number.toFixed(2);
}


/* Change selected market */
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


/* Select a market */
function selectMarket(symbol) {

    const market =
        encodeURIComponent(symbol);

    window.location.href =
        "/?market=" + market;
            }
