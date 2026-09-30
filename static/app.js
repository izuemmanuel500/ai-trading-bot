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

        const reasons =
            Array.isArray(data.reasons)
                ? data.reasons
                : [];

        const reasonHTML =
            reasons.length > 0
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


/* ================================
   REFRESH PAPER PORTFOLIO
================================ */

async function refreshDashboard() {

    try {

        const response =
            await fetch(
                "/api/portfolio",
                {
                    cache: "no-store"
                }
            );

        if (!response.ok) {
            throw new Error(
                "Portfolio request failed"
            );
        }

        const data =
            await response.json();

        const cash =
            data.cash !== undefined
                ? data.cash
                : data.balance;

        updateElement(
            "#cashValue",
            formatMoney(cash)
        );

        const totalValue =
            data.total_value !== undefined
                ? data.total_value
                : data.balance;

        updateElement(
            "#portfolioValue",
            formatMoney(totalValue)
        );

        const positions =
            Array.isArray(data.positions)
                ? data.positions
                : [];

        updateElement(
            "#positionsCount",
            positions.length
        );

        renderPositions(positions);

    } catch (error) {

        console.log(
            "Portfolio refresh skipped:",
            error.message
        );
    }
}


/* ================================
   RENDER OPEN POSITIONS
================================ */

function renderPositions(positions) {

    const container =
        document.querySelector(
            "#positionsContainer"
        );

    if (!container) {
        return;
    }

    if (!positions.length) {

        container.innerHTML = `
            <div class="empty-state">

                <div class="empty-icon">
                    📊
                </div>

                <h4>
                    No open positions
                </h4>

                <p>
                    Your paper portfolio is
                    currently holding no positions.
                </p>

            </div>
        `;

        return;
    }

    const positionHTML =
        positions.map(function (position) {

            const pnl =
                Number(
                    position.pnl || 0
                );

            const pnlClass =
                pnl > 0
                    ? "profit"
                    : pnl < 0
                        ? "loss"
                        : "";

            return `
                <div class="position-card">

                    <div>

                        <strong>
                            ${escapeHTML(
                                position.market ||
                                "UNKNOWN"
                            )}
                        </strong>

                        <span>
                            Qty:
                            ${Number(
                                position.quantity || 0
                            ).toFixed(8)}
                        </span>

                    </div>

                    <div>

                        <span>
                            Entry:
                            $${Number(
                                position.avg_price || 0
                            ).toFixed(4)}
                        </span>

                        <span>
                            Current:
                            $${Number(
                                position.current_price || 0
                            ).toFixed(4)}
                        </span>

                    </div>

                    <div>

                        <strong>
                            Value:
                            $${Number(
                                position.value || 0
                            ).toFixed(2)}
                        </strong>

                        <span class="${pnlClass}">
                            P/L:
                            $${pnl.toFixed(2)}
                        </span>

                    </div>

                </div>
            `;

        }).join("");

    container.innerHTML = `
        <div class="positions">
            ${positionHTML}
        </div>
    `;
}


/* ================================
   SECURITY
================================ */

function escapeHTML(value) {

    return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}


/* ================================
   UPDATE ELEMENT
================================ */

function updateElement(
    selector,
    value
) {

    const element =
        document.querySelector(selector);

    if (element) {
        element.textContent = value;
    }
}


/* ================================
   MONEY FORMAT
================================ */

function formatMoney(value) {

    const number =
        Number(value || 0);

    return "$" +
        number.toFixed(2);
}


/* ================================
   CHANGE MARKET
================================ */

function changeMarket() {

    const marketSelect =
        document.querySelector(
            "#marketSelect"
        );

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


/* ================================
   SELECT MARKET
================================ */

function selectMarket(symbol) {

    const market =
        encodeURIComponent(symbol);

    window.location.href =
        "/?market=" + market;
}


/* ================================
   INITIAL LOAD
================================ */

document.addEventListener(
    "DOMContentLoaded",
    function () {

        refreshDashboard();

    }
);
