async function runBot() {
    try {
        const button = document.querySelector("button");

        if (button) {
            button.disabled = true;
            button.textContent = "Running AI Trader...";
        }

        const response = await fetch("/api/run", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            }
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Bot request failed");
        }

        alert(
            "AI Trader Result\n\n" +
            "Signal: " + (data.signal || "N/A") + "\n" +
            "Price: $" + (data.price || "N/A") + "\n" +
            "Message: " + (data.message || "Completed")
        );

        location.reload();

    } catch (error) {
        alert("Bot error: " + error.message);
    } finally {
        const button = document.querySelector("button");

        if (button) {
            button.disabled = false;
            button.textContent = "Run AI Trader";
        }
    }
}
