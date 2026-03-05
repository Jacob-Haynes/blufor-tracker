(function () {
    "use strict";

    var BFT = window.BFT = window.BFT || {};

    var QUICK_QUERIES = [
        { label: "SITREP Summary", query: "Summarise the current tactical situation" },
        { label: "Threat Assessment", query: "Assess threat based on recent contact reports and messages" },
        { label: "Mesh Health", query: "Evaluate mesh network health and suggest relay positions" },
        { label: "Unit Dispersion", query: "Check unit dispersion and suggest adjustments" },
        { label: "Movement Analysis", query: "Analyse movement patterns of all callsigns" },
    ];

    var advisorOpen = false;
    var generating = false;

    function buildPanel() {
        var panel = document.getElementById("advisor-panel");
        if (!panel) return;

        panel.innerHTML =
            '<div class="advisor-header">' +
                '<strong>Tactical Advisor</strong>' +
                '<span id="advisor-status" class="advisor-status">Checking...</span>' +
                '<button id="advisor-close" class="advisor-close">&times;</button>' +
            '</div>' +
            '<div class="advisor-quick-buttons" id="advisor-quick-btns"></div>' +
            '<div class="advisor-input-area">' +
                '<input type="text" id="advisor-input" placeholder="Ask the advisor..." />' +
                '<button id="advisor-send" class="advisor-send-btn">Ask</button>' +
            '</div>' +
            '<div class="advisor-responses" id="advisor-responses">' +
                '<div class="advisor-empty">No queries yet. Use a quick query or type your own.</div>' +
            '</div>';

        // Quick buttons
        var quickContainer = document.getElementById("advisor-quick-btns");
        QUICK_QUERIES.forEach(function (q) {
            var btn = document.createElement("button");
            btn.className = "advisor-quick-btn";
            btn.textContent = q.label;
            btn.addEventListener("click", function () { submitQuery(q.query); });
            quickContainer.appendChild(btn);
        });

        // Close button
        document.getElementById("advisor-close").addEventListener("click", function () {
            if (BFT._onAdvisorClose) BFT._onAdvisorClose();
        });

        // Send button + enter key
        document.getElementById("advisor-send").addEventListener("click", function () {
            var input = document.getElementById("advisor-input");
            var query = input.value.trim();
            if (query) {
                submitQuery(query);
                input.value = "";
            }
        });

        document.getElementById("advisor-input").addEventListener("keydown", function (e) {
            if (e.key === "Enter") {
                var query = this.value.trim();
                if (query) {
                    submitQuery(query);
                    this.value = "";
                }
            }
        });

        // Check LLM status
        checkStatus();
    }

    function checkStatus() {
        var statusEl = document.getElementById("advisor-status");
        if (!statusEl) return;

        fetch("/api/llm/status")
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.loaded) {
                    statusEl.textContent = "LLM ready" + (data.model ? " (" + data.model + ")" : "");
                    statusEl.className = "advisor-status advisor-status-ready";
                } else {
                    statusEl.textContent = "No model loaded";
                    statusEl.className = "advisor-status advisor-status-offline";
                }
            })
            .catch(function () {
                statusEl.textContent = "Status unavailable";
                statusEl.className = "advisor-status advisor-status-offline";
            });
    }

    function submitQuery(query) {
        if (generating) return;

        var responsesEl = document.getElementById("advisor-responses");
        if (!responsesEl) return;

        // Remove empty placeholder
        var empty = responsesEl.querySelector(".advisor-empty");
        if (empty) empty.remove();

        // Add query bubble
        var queryDiv = document.createElement("div");
        queryDiv.className = "advisor-query";
        queryDiv.textContent = query;
        responsesEl.appendChild(queryDiv);

        // Add generating indicator
        var genDiv = document.createElement("div");
        genDiv.className = "advisor-response advisor-generating";
        genDiv.innerHTML = '<span class="advisor-spinner"></span> Generating...';
        responsesEl.appendChild(genDiv);
        responsesEl.scrollTop = responsesEl.scrollHeight;

        generating = true;
        updateButtonStates();

        fetch("/api/llm/suggest", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ query: query }),
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                genDiv.remove();
                var responseDiv = document.createElement("div");
                responseDiv.className = "advisor-response";

                var text = data.suggestion || data.error || "No response";
                // Escape HTML entities first to prevent XSS
                var escaped = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
                // Basic markdown-ish formatting: bold **text**, newlines
                escaped = escaped.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
                escaped = escaped.replace(/\n/g, "<br>");
                responseDiv.innerHTML = escaped;

                var timeStamp = document.createElement("div");
                timeStamp.className = "advisor-timestamp";
                timeStamp.textContent = new Date().toLocaleTimeString();
                responseDiv.appendChild(timeStamp);

                responsesEl.appendChild(responseDiv);
                responsesEl.scrollTop = responsesEl.scrollHeight;
            })
            .catch(function (err) {
                genDiv.remove();
                var errDiv = document.createElement("div");
                errDiv.className = "advisor-response advisor-error";
                errDiv.textContent = "Request failed: " + err.message;
                responsesEl.appendChild(errDiv);
            })
            .finally(function () {
                generating = false;
                updateButtonStates();
            });
    }

    function updateButtonStates() {
        var btns = document.querySelectorAll(".advisor-quick-btn, .advisor-send-btn");
        for (var i = 0; i < btns.length; i++) {
            btns[i].disabled = generating;
        }
        var input = document.getElementById("advisor-input");
        if (input) input.disabled = generating;
    }

    BFT.toggleAdvisorPanel = function () {
        var panel = document.getElementById("advisor-panel");
        if (!panel) return false;
        advisorOpen = !advisorOpen;
        panel.classList.toggle("hidden", !advisorOpen);
        if (advisorOpen) {
            buildPanel();
        }
        return advisorOpen;
    };
})();
