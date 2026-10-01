/* =========================================
   TRADEMIND V4 FRONTEND
   PAPER TRADING ONLY
========================================= */


/* =========================================
   RUN AI TRADER
========================================= */

async function runBot() {

    const button =
        document.querySelector("#runBotButton");

    const marketSelect =
        document.querySelector("#marketSelect");

    const analysisArea =
        document.querySelector("#analysisResult");

    const market =
        marketSelect
            ? marketSelect.value
            : "BTC/USD";


    if (button) {
        button.disabled = true;
        button.textContent = "🤖 Analyzing...";
    }


    if (analysisArea) {

        analysisArea.innerHTML = `
            <div class="analysis-loading">
                <div class="loading-icon">🤖</div>
                <h3>TradeMind is analyzing ${escapeHTML(market)}</h3>
                <p>Checking market data and indicators...</p>
            </div>
        `;
    }


    try {

        const response =
            await fetch("/api/run", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    market: market
                }),
                cache: "no-store"
            });


        const data =
            await response.json();


        if (!response.ok || data.success === false) {

            throw new Error(
                data.error ||
                data.message ||
                "Market analysis failed."
            );
        }


        /*
         * V4 BACKEND STRUCTURE
         *
         * data.analysis
         * data.trade
         * data.trade_action
         * data.trade_executed
         * data.trade_message
         */

        const analysis =
            data.analysis || {};


        const signal =
            data.signal ||
            analysis.signal ||
            "HOLD";


        const price =
            Number(
                data.price ||
                analysis.price ||
                0
            );


        const confidence =
            Number(
                data.confidence ||
                analysis.confidence ||
                0
            );


        const marketStrength =
            data.market_strength ||
            analysis.market_strength ||
            "NEUTRAL";


        const riskLevel =
            data.risk_level ||
            analysis.risk_level ||
            "LOW";


        const reasons =
            Array.isArray(
                data.reasons
            )
                ? data.reasons
                : Array.isArray(
                    analysis.reasons
                )
                    ? analysis.reasons
                    : [];


        const explanation =
            data.message ||
            data.explanation ||
            analysis.message ||
            analysis.explanation ||
            "TradeMind completed the market analysis.";


        /*
         * BUILD REASONS
         */

        const reasonHTML =
            reasons.length > 0

                ? reasons
                    .map(function (reason) {

                        return `
                            <li>
                                ${escapeHTML(reason)}
                            </li>
                        `;

                    })
                    .join("")

                : `
                    <li>
                        No additional reasons were returned.
                    </li>
                `;


        /*
         * TRADE RESULT
         */

        const tradeExecuted =
            data.trade_executed === true;


        const tradeAction =
            String(
                data.trade_action ||
                "NONE"
            ).toUpperCase();


        const tradeMessage =
            data.trade_message ||
            "No paper trade was executed.";


        let tradeStatusHTML = "";


        if (tradeExecuted) {

            tradeStatusHTML = `
                <div class="trade-executed">
                    <strong>
                        ✅ PAPER TRADE EXECUTED
                    </strong>

                    <p>
                        ${escapeHTML(tradeMessage)}
                    </p>

                    <span>
                        Action:
                        ${escapeHTML(tradeAction)}
                    </span>
                </div>
            `;

        } else if (
            signal === "SELL" &&
            tradeAction === "SIGNAL_ONLY"
        ) {

            tradeStatusHTML = `
                <div class="trade-signal-only">
                    <strong>
                        ℹ️ SIGNAL ONLY
                    </strong>

                    <p>
                        ${escapeHTML(tradeMessage)}
                    </p>
                </div>
            `;

        } else {

            tradeStatusHTML = `
                <div class="trade-no-action">
                    <strong>
                        ⏸️ NO PAPER TRADE
                    </strong>

                    <p>
                        ${escapeHTML(tradeMessage)}
                    </p>
                </div>
            `;
        }


        /*
         * RISK INFORMATION
         */

        const stopLoss =
            Number(
                data.stop_loss ||
                analysis.stop_loss ||
                0
            );


        const takeProfit =
            Number(
                data.take_profit ||
                analysis.take_profit ||
                0
            );


        /*
         * RENDER RESULT
         */

        if (analysisArea) {

            analysisArea.innerHTML = `

                <div class="analysis-result">

                    <div class="analysis-header">

                        <div>
                            <span class="ai-title">
                                🤖 TradeMind AI
                            </span>

                            <small>
                                ${escapeHTML(market)}
                            </small>
                        </div>

                        <span class="live-badge">
                            LIVE
                        </span>

                    </div>


                    <div class="signal-box">

                        <div class="signal-label">
                            AI SIGNAL
                        </div>

                        <div class="signal-value signal-${signal.toLowerCase()}">
                            ${escapeHTML(signal)}
                        </div>

                    </div>


                    <div class="analysis-grid">


                        <div class="analysis-item">

                            <span>
                                Market
                            </span>

                            <strong>
                                ${escapeHTML(
                                    data.market ||
                                    analysis.market ||
                                    market
                                )}
                            </strong>

                        </div>


                        <div class="analysis-item">

                            <span>
                                Price
                            </span>

                            <strong>
                                $${price.toFixed(4)}
                            </strong>

                        </div>


                        <div class="analysis-item">

                            <span>
                                Confidence
                            </span>

                            <strong>
                                ${confidence}%
                            </strong>

                        </div>


                        <div class="analysis-item">

                            <span>
                                Market Strength
                            </span>

                            <strong>
                                ${escapeHTML(
                                    marketStrength
                                )}
                            </strong>

                        </div>


                        <div class="analysis-item">

                            <span>
                                Risk Level
                            </span>

                            <strong>
                                ${escapeHTML(
                                    riskLevel
                                )}
                            </strong>

                        </div>


                        ${
                            stopLoss > 0
                                ? `
                                    <div class="analysis-item">

                                        <span>
                                            Stop Loss
                                        </span>

                                        <strong>
                                            $${stopLoss.toFixed(4)}
                                        </strong>

                                    </div>
                                `
                                : ""
                        }


                        ${
                            takeProfit > 0
                                ? `
                                    <div class="analysis-item">

                                        <span>
                                            Take Profit
                                        </span>

                                        <strong>
                                            $${takeProfit.toFixed(4)}
                                        </strong>

                                    </div>
                                `
                                : ""
                        }


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
                                explanation
                            )}
                        </p>

                    </div>


                    ${tradeStatusHTML}


                    <div class="paper-warning">

                        ⚠️ PAPER TRADING ONLY

                        <span>
                            Real-money orders are disabled.
                        </span>

                    </div>

                </div>
            `;
        }


        /*
         * REFRESH DASHBOARD AFTER TRADE
         */

        await refreshDashboard();


    } catch (error) {

        console.error(
            "TradeMind error:",
            error
        );


        if (analysisArea) {

            analysisArea.innerHTML = `

                <div class="error-result">

                    <div class="error-icon">
                        ⚠️
                    </div>

                    <h3>
                        TradeMind Error
                    </h3>

                    <p>
                        ${escapeHTML(
                            error.message
                        )}
                    </p>

                </div>

            `;
        }

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


/* =========================================
   REFRESH DASHBOARD
========================================= */

async function refreshDashboard() {

    await refreshPortfolio();

    await refreshTradeHistory();
}


/* =========================================
   REFRESH PAPER PORTFOLIO
========================================= */

async function refreshPortfolio() {

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
            Array.isArray(
                data.positions
            )
                ? data.positions
                : [];


        updateElement(
            "#positionsCount",
            positions.length
        );


        renderPositions(
            positions
        );


    } catch (error) {

        console.log(
            "Portfolio refresh skipped:",
            error.message
        );
    }
}


/* =========================================
   REFRESH TRADE HISTORY
========================================= */

async function refreshTradeHistory() {

    try {

        const response =
            await fetch(
                "/api/trades",
                {
                    cache: "no-store"
                }
            );


        if (!response.ok) {

            throw new Error(
                "Trade history request failed"
            );
        }


        const trades =
            await response.json();


        const tradeList =
            Array.isArray(trades)
                ? trades
                : [];


        let totalTrades =
            tradeList.length;


        let winningTrades = 0;

        let losingTrades = 0;

        let realizedPnL = 0;


        tradeList.forEach(
            function (trade) {

                const pnl =
                    Number(
                        trade.pnl || 0
                    );


                const side =
                    String(
                        trade.side ||
                        trade.action ||
                        ""
                    ).toUpperCase();


                if (side === "SELL") {

                    realizedPnL += pnl;


                    if (pnl > 0) {
                        winningTrades++;
                    }


                    if (pnl < 0) {
                        losingTrades++;
                    }
                }

            }
        );


        updateElement(
            "#totalTrades",
            totalTrades
        );


        updateElement(
            "#winningTrades",
            winningTrades
        );


        updateElement(
            "#losingTrades",
            losingTrades
        );


        updateElement(
            "#realizedPnL",
            formatPnL(realizedPnL)
        );


        renderTradeHistory(
            tradeList
        );


    } catch (error) {

        console.log(
            "Trade history refresh skipped:",
            error.message
        );
    }
}


/* =========================================
   RENDER TRADE HISTORY
========================================= */

function renderTradeHistory(trades) {

    const container =
        document.querySelector(
            "#tradeHistoryContainer"
        );


    if (!container) {
        return;
    }


    if (!trades.length) {

        container.innerHTML = `

            <div class="empty-state">

                <div class="empty-icon">
                    📜
                </div>

                <h4>
                    No trades yet
                </h4>

                <p>
                    Your paper trades will
                    appear here.
                </p>

            </div>

        `;

        return;
    }


    const historyHTML =
        trades
            .map(function (trade) {

                const side =
                    String(
                        trade.side ||
                        trade.action ||
                        ""
                    ).toUpperCase();


                const pnl =
                    Number(
                        trade.pnl || 0
                    );


                const pnlClass =
                    pnl > 0
                        ? "profit"
                        : pnl < 0
                            ? "loss"
                            : "";


                const date =
                    formatTradeDate(
                        trade.created_at ||
                        trade.timestamp
                    );


                return `

                    <div class="trade-history-item">

                        <div class="trade-main">

                            <strong>
                                ${escapeHTML(
                                    trade.market ||
                                    "UNKNOWN"
                                )}
                            </strong>

                            <span class="trade-side ${side.toLowerCase()}">
                                ${escapeHTML(side)}
                            </span>

                        </div>


                        <div class="trade-details">

                            <span>
                                Price:
                                $${Number(
                                    trade.price || 0
                                ).toFixed(4)}
                            </span>


                            <span>
                                Qty:
                                ${Number(
                                    trade.quantity || 0
                                ).toFixed(8)}
                            </span>


                            <span>
                                Value:
                                $${Number(
                                    trade.value ||
                                    trade.amount ||
                                    0
                                ).toFixed(2)}
                            </span>

                        </div>


                        <div class="trade-result">

                            <span>
                                P/L:
                            </span>

                            <strong class="${pnlClass}">
                                ${formatPnL(pnl)}
                            </strong>

                        </div>


                        <div class="trade-date">
                            ${escapeHTML(date)}
                        </div>

                    </div>

                `;

            })
            .join("");


    container.innerHTML = `

        <div class="trade-history-list">

            ${historyHTML}

        </div>

    `;
}


/* =========================================
   FORMAT TRADE DATE
========================================= */

function formatTradeDate(value) {

    if (!value) {
        return "Unknown time";
    }


    try {

        const date =
            new Date(value);


        if (
            Number.isNaN(
                date.getTime()
            )
        ) {

            return String(value);
        }


        return date.toLocaleString();


    } catch (error) {

        return String(value);
    }
}


/* =========================================
   FORMAT P/L
========================================= */

function formatPnL(value) {

    const number =
        Number(value || 0);


    if (number > 0) {

        return (
            "+$" +
            number.toFixed(2)
        );
    }


    if (number < 0) {

        return (
            "-$" +
            Math.abs(number).toFixed(2)
        );
    }


    return "$0.00";
}


/* =========================================
   RENDER OPEN POSITIONS
========================================= */

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
        positions
            .map(function (position) {

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
                                ${formatPnL(pnl)}
                            </span>

                        </div>

                    </div>

                `;

            })
            .join("");


    container.innerHTML = `

        <div class="positions">

            ${positionHTML}

        </div>

    `;
}


/* =========================================
   SECURITY
========================================= */

function escapeHTML(value) {

    return String(value)

        .replace(
            /&/g,
            "&amp;"
        )

        .replace(
            /</g,
            "&lt;"
        )

        .replace(
            />/g,
            "&gt;"
        )

        .replace(
            /"/g,
            "&quot;"
        )

        .replace(
            /'/g,
            "&#039;"
        );
}


/* =========================================
   UPDATE ELEMENT
========================================= */

function updateElement(
    selector,
    value
) {

    const element =
        document.querySelector(
            selector
        );


    if (element) {

        element.textContent =
            value;
    }
}


/* =========================================
   MONEY FORMAT
========================================= */

function formatMoney(value) {

    const number =
        Number(value || 0);


    return (
        "$" +
        number.toFixed(2)
    );
}


/* =========================================
   CHANGE MARKET
========================================= */

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


/* =========================================
   SELECT MARKET
========================================= */

function selectMarket(symbol) {

    const market =
        encodeURIComponent(
            symbol
        );


    window.location.href =
        "/?market=" + market;
}


/* =========================================
   INITIAL LOAD
========================================= */

document.addEventListener(
    "DOMContentLoaded",
    function () {

        const runButton =
            document.querySelector(
                "#runBotButton"
            );


        if (runButton) {

            runButton.addEventListener(
                "click",
                runBot
            );
        }


        refreshDashboard();

    }
);
