/**
 * Structured Reports — UK/NATO formats
 * 9-Liner MEDEVAC + MIST, SITREP, Contact Report, METHANE, CPERS, Siting Report
 */
(function () {
    "use strict";

    var BFT = window.BFT = window.BFT || {};

    // --- Report field definitions (UK/NATO formats) ---
    var REPORT_DEFS = {
        "9liner": {
            label: "9-Liner MEDEVAC",
            fields: [
                { key: "line1", label: "1. Location (grid ref of pickup site)" },
                { key: "line2", label: "2. Callsign & frequency" },
                { key: "line3", label: "3. Precedence", options: ["A–Urgent", "B–Urgent Surgical", "C–Priority", "D–Routine", "E–Convenience"] },
                { key: "line4", label: "4. Special equipment", options: ["A–None", "B–Hoist", "C–Extraction", "D–Ventilator"] },
                { key: "line5", label: "5. Number of casualties (A–Stretcher, B–Walking)" },
                { key: "line6", label: "6. Security at pickup", options: ["N–No enemy", "P–Possible", "E–Enemy present", "X–Armed escort required"] },
                { key: "line7", label: "7. Marking", options: ["A–Panels", "B–Pyro", "C–Smoke", "D–None", "E–Other"] },
                { key: "line8", label: "8. Nationality & status", options: ["A–UK/Coalition Mil", "B–UK/Coalition Civ", "C–Non-coalition", "D–Civ", "E–EPW"] },
                { key: "line9", label: "9. CBRN contamination / terrain" },
            ],
            mist: [
                { key: "mist_m", label: "M — Mechanism of injury", options: ["IED", "GSW", "Blast", "RTC", "Fall", "Crush", "Burn", "Other"] },
                { key: "mist_i", label: "I — Injuries sustained" },
                { key: "mist_s", label: "S — Signs & symptoms (C-ABCDE: catastrophic haemorrhage, airway, breathing, circulation, disability, exposure)" },
                { key: "mist_t", label: "T — Treatment given (tourniquet, haemostatic, chest seal, pelvic binder, fluids, etc.)" },
            ]
        },
        "mist": {
            label: "MIST (Casualty Report)",
            fields: [
                { key: "callsign", label: "Callsign / zap number of casualty" },
                { key: "location", label: "Location (grid)" },
                { key: "mist_m", label: "M — Mechanism of injury", options: ["IED", "GSW", "Blast", "RTC", "Fall", "Crush", "Burn", "Other"] },
                { key: "mist_i", label: "I — Injuries sustained" },
                { key: "mist_s", label: "S — Signs & symptoms (C-ABCDE assessment)" },
                { key: "mist_t", label: "T — Treatment given" },
                { key: "priority", label: "Priority", options: ["T1–Immediate", "T2–Urgent", "T3–Delayed", "T4–Expectant", "Dead"] },
            ]
        },
        "sitrep": {
            label: "SITREP",
            fields: [
                { key: "dtg", label: "1. DTG (date-time group)" },
                { key: "unit", label: "2. Unit/callsign" },
                { key: "location", label: "3. Location (grid)" },
                { key: "activity", label: "4. Activity — what happened" },
                { key: "enemy", label: "5. Enemy situation" },
                { key: "friendly", label: "6. Friendly situation — own forces disposition" },
                { key: "casualties", label: "7. Casualties — own & enemy (KIA/WIA/MIA)" },
                { key: "ammo", label: "8. Ammunition & supplies (Green/Amber/Red/Black)" },
                { key: "actions", label: "9. Intended actions / next move" },
                { key: "remarks", label: "10. Requests/remarks" },
            ]
        },
        "contact": {
            label: "Contact Report",
            fields: [
                { key: "dtg", label: "DTG of contact" },
                { key: "grid", label: "Grid of contact" },
                { key: "size", label: "Size — number & type of enemy" },
                { key: "activity", label: "Activity — what enemy is doing (attacking, defending, withdrawing, patrolling)" },
                { key: "weapon", label: "Weapons / equipment seen" },
                { key: "direction", label: "Direction of enemy movement" },
                { key: "own_action", label: "Own action taken (returning fire, taking cover, flanking)" },
                { key: "own_cas", label: "Own casualties" },
                { key: "request", label: "Request (fire support, QRF, CASEVAC, none)" },
            ]
        },
        "siting": {
            label: "Siting Report",
            fields: [
                { key: "dtg", label: "DTG of sighting" },
                { key: "grid", label: "Grid reference" },
                { key: "what", label: "What was seen (description)" },
                { key: "size", label: "Size / number" },
                { key: "activity", label: "Activity (stationary, moving, digging in)" },
                { key: "direction", label: "Direction of movement (bearing / from-to)" },
                { key: "weapons", label: "Weapons / equipment" },
                { key: "remarks", label: "Remarks (threat assessment, identification)" },
            ]
        },
        "cpers": {
            label: "CPERS (Captured Persons)",
            fields: [
                { key: "dtg", label: "DTG of capture" },
                { key: "grid", label: "Grid of capture" },
                { key: "number", label: "Number of CPERS" },
                { key: "nationality", label: "Nationality / affiliation" },
                { key: "rank", label: "Rank / status (if known)" },
                { key: "weapons", label: "Weapons / equipment / docs seized" },
                { key: "condition", label: "Physical condition (injuries, MIST if wounded)" },
                { key: "circumstances", label: "Circumstances of capture" },
                { key: "handling", label: "Handling arrangements (location held, escort)" },
            ]
        },
    };

    BFT.REPORT_DEFS = REPORT_DEFS;

    // --- Report panel rendering ---
    var reportPanel = null;
    var reportPanelOpen = false;
    var allReports = [];

    BFT.initReports = function (panelEl, ws, formatCoord, lastClickLatLon) {
        reportPanel = panelEl;
        BFT._ws = ws;
        BFT._formatCoord = formatCoord;
        BFT._lastClickLatLon = lastClickLatLon;
        loadReports();
    };

    function loadReports() {
        fetch("/api/reports")
            .then(function (r) { return r.json(); })
            .then(function (reports) {
                allReports = reports;
                if (reportPanelOpen) renderReceivedTab();
            });
    }

    BFT.toggleReportPanel = function () {
        reportPanelOpen = !reportPanelOpen;
        if (reportPanelOpen) {
            renderReportPanel();
            reportPanel.classList.remove("hidden");
        } else {
            reportPanel.classList.add("hidden");
        }
        return reportPanelOpen;
    };

    BFT.handleIncomingReport = function (report) {
        // Update or add
        var found = false;
        for (var i = 0; i < allReports.length; i++) {
            if (allReports[i].id === report.id) {
                allReports[i] = report;
                found = true;
                break;
            }
        }
        if (!found) {
            allReports.push(report);
            BFT._showToast("Incoming " + (REPORT_DEFS[report.report_type] ? REPORT_DEFS[report.report_type].label : report.report_type) + " from " + report.sender, "warning");
        }
        if (reportPanelOpen) renderReceivedTab();
    };

    function renderReportPanel() {
        var html = '<div class="report-panel-header">';
        html += '<strong>Reports</strong>';
        html += '<button id="report-close-btn" class="report-close">&times;</button>';
        html += '</div>';
        html += '<div class="report-tabs">';
        html += '<button class="report-tab active" data-tab="new">New</button>';
        html += '<button class="report-tab" data-tab="received">Received</button>';
        html += '</div>';
        html += '<div id="report-tab-content" class="report-tab-content"></div>';
        reportPanel.innerHTML = html;

        // Tab switching
        reportPanel.querySelectorAll(".report-tab").forEach(function (btn) {
            btn.addEventListener("click", function () {
                reportPanel.querySelectorAll(".report-tab").forEach(function (b) { b.classList.remove("active"); });
                btn.classList.add("active");
                if (btn.dataset.tab === "new") renderNewTab();
                else renderReceivedTab();
            });
        });

        document.getElementById("report-close-btn").addEventListener("click", function () {
            if (BFT._onReportClose) BFT._onReportClose();
            else BFT.toggleReportPanel();
        });

        renderNewTab();
    }

    function renderNewTab() {
        var content = document.getElementById("report-tab-content");
        var html = '<div class="report-form-wrap">';
        html += '<label>Report Type:</label>';
        html += '<select id="report-type-select" class="report-select">';
        Object.keys(REPORT_DEFS).forEach(function (key) {
            html += '<option value="' + key + '">' + REPORT_DEFS[key].label + '</option>';
        });
        html += '</select>';
        html += '<div id="report-form-fields"></div>';
        html += '<button id="report-submit-btn" class="report-submit">Submit Report</button>';
        html += '</div>';
        content.innerHTML = html;

        var select = document.getElementById("report-type-select");
        select.addEventListener("change", function () {
            renderFormFields(select.value);
        });
        renderFormFields(select.value);

        document.getElementById("report-submit-btn").addEventListener("click", submitReport);
    }

    function renderFormFields(reportType) {
        var container = document.getElementById("report-form-fields");
        var def = REPORT_DEFS[reportType];
        if (!def) return;

        var html = '';
        def.fields.forEach(function (field) {
            html += '<div class="report-field">';
            html += '<label>' + field.label + '</label>';
            if (field.options) {
                html += '<select data-key="' + field.key + '" class="report-input">';
                field.options.forEach(function (opt) {
                    html += '<option value="' + opt + '">' + opt + '</option>';
                });
                html += '</select>';
            } else {
                var val = '';
                // Auto-fill location fields
                if (field.key === "line1" || field.key === "location" || field.key === "exact") {
                    var ll = BFT._lastClickLatLon;
                    if (ll) val = BFT._formatCoord(ll.lat, ll.lon);
                }
                // Auto-fill DTG
                if (field.key === "dtg") {
                    val = formatDTG(new Date());
                }
                // Auto-fill callsign
                if (field.key === "unit" || field.key === "line2") {
                    val = "HQ";
                }
                // Auto-fill time
                if (field.key === "time" || field.key === "at_t") {
                    val = new Date().toISOString().substring(11, 16) + "Z";
                }
                html += '<input type="text" data-key="' + field.key + '" class="report-input" value="' + escapeAttr(val) + '" />';
            }
            html += '</div>';
        });

        // MIST section for 9-liner
        if (def.mist) {
            html += '<div class="report-atmist-header">MIST (Casualty Handover)</div>';
            def.mist.forEach(function (field) {
                html += '<div class="report-field">';
                html += '<label>' + field.label + '</label>';
                if (field.options) {
                    html += '<select data-key="' + field.key + '" class="report-input">';
                    field.options.forEach(function (opt) {
                        html += '<option value="' + opt + '">' + opt + '</option>';
                    });
                    html += '</select>';
                } else {
                    var val = '';
                    if (field.key === "at_t") val = new Date().toISOString().substring(11, 16) + "Z";
                    html += '<input type="text" data-key="' + field.key + '" class="report-input" value="' + escapeAttr(val) + '" />';
                }
                html += '</div>';
            });
        }

        container.innerHTML = html;
    }

    function submitReport() {
        var reportType = document.getElementById("report-type-select").value;
        var fields = {};
        reportPanel.querySelectorAll("[data-key]").forEach(function (el) {
            fields[el.dataset.key] = el.value || "";
        });

        fetch("/api/reports", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                report_type: reportType,
                sender: "HQ",
                fields: fields,
            }),
        }).then(function (r) { return r.json(); })
          .then(function (report) {
              allReports.push(report);
              BFT._showToast("Report submitted: " + REPORT_DEFS[reportType].label, "info");
              // Switch to received tab
              reportPanel.querySelectorAll(".report-tab").forEach(function (b) { b.classList.remove("active"); });
              reportPanel.querySelector('[data-tab="received"]').classList.add("active");
              renderReceivedTab();
          });
    }

    function renderReceivedTab() {
        var content = document.getElementById("report-tab-content");
        if (!content) return;

        if (allReports.length === 0) {
            content.innerHTML = '<div class="report-empty">No reports received</div>';
            return;
        }

        var html = '';
        var sorted = allReports.slice().sort(function (a, b) { return b.timestamp - a.timestamp; });
        sorted.forEach(function (report) {
            var def = REPORT_DEFS[report.report_type];
            var label = def ? def.label : report.report_type;
            var colorClass = "report-type-" + report.report_type;
            var statusIcon = report.status === "acknowledged" ? '<span class="report-ack-icon">&#10004;</span>' : '';

            html += '<div class="report-item ' + colorClass + '">';
            html += '<div class="report-item-header" data-id="' + report.id + '">';
            html += '<span class="report-item-type">' + label + '</span>';
            html += '<span class="report-item-sender">' + escapeHTML(report.sender) + '</span>';
            html += '<span class="report-item-time">' + formatTimeShort(report.timestamp) + '</span>';
            html += statusIcon;
            html += '</div>';
            html += '<div class="report-item-body hidden" id="report-body-' + report.id + '">';

            // Render fields
            Object.keys(report.fields).forEach(function (key) {
                var fieldLabel = key;
                if (def) {
                    var allFields = (def.fields || []).concat(def.mist || []);
                    var match = allFields.find(function (f) { return f.key === key; });
                    if (match) fieldLabel = match.label;
                }
                html += '<div class="report-field-display"><span class="report-field-label">' + fieldLabel + ':</span> ' + escapeHTML(report.fields[key]) + '</div>';
            });

            if (report.status !== "acknowledged") {
                html += '<button class="report-ack-btn" data-report-id="' + report.id + '">Acknowledge</button>';
            }
            html += '</div></div>';
        });

        content.innerHTML = html;

        // Toggle expand
        content.querySelectorAll(".report-item-header").forEach(function (header) {
            header.addEventListener("click", function () {
                var body = document.getElementById("report-body-" + header.dataset.id);
                if (body) body.classList.toggle("hidden");
            });
        });

        // Acknowledge buttons
        content.querySelectorAll(".report-ack-btn").forEach(function (btn) {
            btn.addEventListener("click", function (e) {
                e.stopPropagation();
                var id = btn.dataset.reportId;
                fetch("/api/reports/" + id + "/acknowledge", { method: "POST" })
                    .then(function (r) { return r.json(); })
                    .then(function (updated) {
                        BFT.handleIncomingReport(updated);
                    });
            });
        });
    }

    // Helpers
    function formatDTG(d) {
        var day = String(d.getUTCDate()).padStart(2, "0");
        var hours = String(d.getUTCHours()).padStart(2, "0");
        var mins = String(d.getUTCMinutes()).padStart(2, "0");
        var months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
        var mon = months[d.getUTCMonth()];
        var yr = String(d.getUTCFullYear()).substring(2);
        return day + hours + mins + "Z " + mon + " " + yr;
    }

    function formatTimeShort(ts) {
        var d = new Date(ts * 1000);
        return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }

    function escapeHTML(str) {
        var div = document.createElement("div");
        div.appendChild(document.createTextNode(str || ""));
        return div.innerHTML;
    }

    function escapeAttr(str) {
        return (str || "").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

})();
