(function () {
    "use strict";

    var BFT = window.BFT = window.BFT || {};

    var BLUE = "#2196F3";
    var GREY = "#9E9E9E";
    var RED = "#f44336";
    var MARKER_RADIUS = 8;

    var WAYPOINT_ICONS = {
        rv: "🔵",
        objective: "🎯",
        danger: "⚠️",
        checkpoint: "✓",
        rally: "🔷",
        trp: "🎯",
    };

    // --- Map setup ---
    var map = L.map("map", { zoomControl: false }).setView([51.5225, -0.0865], 15);
    L.control.zoom({ position: "bottomleft" }).addTo(map);

    var satellite = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
        maxZoom: 19,
        attribution: "&copy; Esri, Maxar, Earthstar Geographics",
    });
    var satelliteLabels = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}", {
        maxZoom: 19,
    });
    var satelliteGroup = L.layerGroup([satellite, satelliteLabels]).addTo(map);

    var topo = L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
        maxZoom: 17,
        attribution: '&copy; <a href="https://opentopomap.org">OpenTopoMap</a> &copy; OpenStreetMap contributors',
    });

    var esriTopo = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}", {
        maxZoom: 19,
        attribution: "&copy; Esri, HERE, Garmin, OpenStreetMap contributors",
    });

    var darkStreets = L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
        maxZoom: 20,
        attribution: '&copy; OpenStreetMap &copy; CARTO',
    });

    L.control.layers({
        "Satellite": satelliteGroup,
        "Topographic": topo,
        "Esri Topo": esriTopo,
        "Dark Streets": darkStreets,
    }, null, { position: "bottomleft" }).addTo(map);

    // --- Layer groups ---
    var trailLayer = L.layerGroup().addTo(map);
    var waypointLayer = L.layerGroup().addTo(map);
    var geofenceLayer = L.layerGroup().addTo(map);
    var annotationLayer = L.layerGroup().addTo(map);
    var measureLayer = L.layerGroup().addTo(map);
    var routeLayer = L.layerGroup().addTo(map);
    var controlMeasureLayer = L.layerGroup().addTo(map);
    var fireMissionLayer = L.layerGroup().addTo(map);

    // --- State ---
    var markers = {};
    var labels = {};
    var positions = {};
    var hasFitBounds = false;

    var allMessages = [];
    var currentChannel = "ALL";
    var unreadCount = 0;
    var chatOpen = false;
    var historyLoaded = false;
    var knownCallsigns = new Set();

    var waypointMarkers = {};
    var waypointPlaceMode = false;

    var activeSOSAlerts = {};
    var sosMarkerOverlays = {};

    var trailPolylines = {};
    var trailsEnabled = true;

    var healthOpen = false;

    var measureMode = false;
    var measurePoints = [];
    var measureMarkers = [];
    var measureLine = null;

    var geofenceLayers = {};
    var geofenceDrawMode = false;
    var geofenceDrawControl = null;

    var annotationLayers = {};
    var annotateDrawMode = false;
    var annotateDrawControl = null;

    var isRecording = false;

    var replayActive = false;
    var replayEvents = [];
    var replayIndex = 0;
    var replayTimer = null;
    var replayPaused = false;

    // New state
    var routeMode = false;
    var routePoints = [];
    var routePreviewMarkers = [];
    var routePreviewLine = null;
    var routeLayers = {}; // route id -> layer group

    var controlMode = false;
    var controlPoints = [];
    var controlPreviewMarkers = [];
    var controlPreviewLine = null;
    var controlMeasureLayers = {}; // cm id -> layer group
    var controlDrawControl = null;

    var fireMissionMode = false;
    var fireMissionClickTarget = "target"; // "target" or "observer"

    var lastClickLatLon = null;

    var weatherVisible = false;
    var weatherTimer = null;

    var offlineQueue = [];

    // --- DOM refs ---
    var connStatusEl = document.getElementById("conn-status");
    var nodeCountEl = document.getElementById("node-count");
    var msgToggleBtn = document.getElementById("msg-toggle");
    var unreadBadge = document.getElementById("unread-badge");
    var chatPanel = document.getElementById("chat-panel");
    var channelSelect = document.getElementById("channel-select");
    var chatMessagesEl = document.getElementById("chat-messages");
    var chatInput = document.getElementById("chat-input");
    var chatSendBtn = document.getElementById("chat-send");
    var chatReqAck = document.getElementById("chat-req-ack");
    var waypointBtn = document.getElementById("waypoint-btn");
    var trailsBtn = document.getElementById("trails-btn");
    var healthBtn = document.getElementById("health-btn");
    var measureBtn = document.getElementById("measure-btn");
    var geofenceBtn = document.getElementById("geofence-btn");
    var annotateBtn = document.getElementById("annotate-btn");
    var routeBtn = document.getElementById("route-btn");
    var controlBtn = document.getElementById("control-btn");
    var fireMissionBtn = document.getElementById("firemission-btn");
    var reportsBtn = document.getElementById("reports-btn");
    var quickMsgBtn = document.getElementById("quickmsg-btn");
    var wxBtn = document.getElementById("wx-btn");
    var meshBtn = document.getElementById("mesh-btn");
    var advisorBtn = document.getElementById("advisor-btn");
    var recordBtn = document.getElementById("record-btn");
    var replayBtn = document.getElementById("replay-btn");
    var healthPanel = document.getElementById("health-panel");
    var healthClose = document.getElementById("health-close");
    var healthTbody = document.getElementById("health-tbody");
    var sosBanner = document.getElementById("sos-banner");
    var sosText = document.getElementById("sos-text");
    var sosAckBtn = document.getElementById("sos-ack-btn");
    var toastContainer = document.getElementById("toast-container");
    var replayControls = document.getElementById("replay-controls");
    var replayPlayBtn = document.getElementById("replay-play");
    var replayPauseBtn = document.getElementById("replay-pause");
    var replayStopBtn = document.getElementById("replay-stop");
    var replayScrubber = document.getElementById("replay-scrubber");
    var replayTimeEl = document.getElementById("replay-time");
    var reportPanel = document.getElementById("report-panel");
    var quickMsgPanel = document.getElementById("quickmsg-panel");
    var fireMissionPanel = document.getElementById("firemission-panel");
    var weatherWidget = document.getElementById("weather-widget");
    var coordFormatSelect = document.getElementById("coord-format-select");

    // --- Helpers ---
    function escapeHtml(str) {
        var div = document.createElement("div");
        div.appendChild(document.createTextNode(str));
        return div.innerHTML;
    }

    function setConnected(connected) {
        if (!navigator.onLine) {
            connStatusEl.textContent = "OFFLINE — cached data";
            connStatusEl.className = "offline";
        } else {
            connStatusEl.textContent = connected ? "CONNECTED" : "DISCONNECTED";
            connStatusEl.className = connected ? "connected" : "disconnected";
        }
    }

    function updateNodeCount() {
        var count = Object.keys(positions).length;
        nodeCountEl.textContent = count + (count === 1 ? " node" : " nodes");
    }

    function relativeTime(ts) {
        var diff = Math.floor(Date.now() / 1000 - ts);
        if (diff < 5) return "just now";
        if (diff < 60) return diff + "s ago";
        if (diff < 3600) return Math.floor(diff / 60) + "m ago";
        return Math.floor(diff / 3600) + "h ago";
    }

    function formatTime(ts) {
        var d = new Date(ts * 1000);
        return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }

    // ===== NOTIFICATION LOG =====
    var notifLog = [];
    var notifIdCounter = 0;
    var notifLogBtn = document.getElementById("notif-log-btn");
    var notifLogPanel = document.getElementById("notif-log");
    var notifLogBadge = document.getElementById("notif-log-badge");
    var notifLogList = document.getElementById("notif-log-list");
    var notifLogOpen = false;

    function addNotifLogEntry(text, type, onclick) {
        notifLog.push({
            id: ++notifIdCounter,
            text: text,
            type: type || "info",
            onclick: onclick,
            timestamp: new Date()
        });
        updateNotifBadge();
        if (notifLogOpen) renderNotifLog();
    }

    function updateNotifBadge() {
        if (notifLog.length > 0) {
            notifLogBadge.textContent = notifLog.length;
            notifLogBadge.classList.remove("hidden");
        } else {
            notifLogBadge.classList.add("hidden");
        }
    }

    function renderNotifLog() {
        notifLogList.innerHTML = "";
        if (notifLog.length === 0) {
            var empty = document.createElement("div");
            empty.className = "notif-log-empty";
            empty.textContent = "No notifications";
            notifLogList.appendChild(empty);
            return;
        }
        for (var i = notifLog.length - 1; i >= 0; i--) {
            (function (entry) {
                var item = document.createElement("div");
                item.className = "notif-log-item notif-" + entry.type;
                var time = document.createElement("span");
                time.className = "notif-log-time";
                time.textContent = entry.timestamp.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
                var text = document.createElement("span");
                text.className = "notif-log-text";
                text.textContent = entry.text;
                item.appendChild(time);
                item.appendChild(text);
                item.addEventListener("click", function () {
                    if (entry.onclick) entry.onclick();
                    notifLog = notifLog.filter(function (e) { return e.id !== entry.id; });
                    updateNotifBadge();
                    renderNotifLog();
                });
                notifLogList.appendChild(item);
            })(notifLog[i]);
        }
    }

    notifLogBtn.addEventListener("click", function () {
        notifLogOpen = !notifLogOpen;
        if (notifLogOpen) {
            notifLogPanel.classList.remove("hidden");
            renderNotifLog();
        } else {
            notifLogPanel.classList.add("hidden");
        }
    });

    document.getElementById("notif-log-close").addEventListener("click", function () {
        notifLogOpen = false;
        notifLogPanel.classList.add("hidden");
    });

    document.getElementById("notif-log-clear").addEventListener("click", function () {
        notifLog = [];
        updateNotifBadge();
        renderNotifLog();
    });

    function showToast(message, type, onclick) {
        type = type || "info";
        var toast = document.createElement("div");
        toast.className = "toast toast-" + type;
        if (onclick) toast.classList.add("toast-clickable");

        var textSpan = document.createElement("span");
        textSpan.className = "toast-text";
        textSpan.textContent = message;
        toast.appendChild(textSpan);

        var dismissBtn = document.createElement("button");
        dismissBtn.className = "toast-dismiss";
        dismissBtn.innerHTML = "&times;";
        dismissBtn.addEventListener("click", function (e) {
            e.stopPropagation();
            if (toast.parentNode) toast.parentNode.removeChild(toast);
        });
        toast.appendChild(dismissBtn);

        if (onclick) {
            toast.addEventListener("click", function () {
                onclick();
                if (toast.parentNode) toast.parentNode.removeChild(toast);
            });
            addNotifLogEntry(message, type, onclick);
        }

        toastContainer.appendChild(toast);
        setTimeout(function () {
            if (toast.parentNode) toast.parentNode.removeChild(toast);
        }, 5000);
    }

    // Expose showToast to modules
    BFT._showToast = showToast;

    // ===== COLOUR PICKER =====

    var COLOUR_PRESETS = [
        { name: "Red", hex: "#ff0000" },
        { name: "Blue", hex: "#2196F3" },
        { name: "Green", hex: "#4caf50" },
        { name: "Yellow", hex: "#ffff00" },
        { name: "Orange", hex: "#ff9800" },
        { name: "Purple", hex: "#9c27b0" },
        { name: "Cyan", hex: "#00bcd4" },
        { name: "Black", hex: "#000000" },
        { name: "White", hex: "#ffffff" },
    ];

    function showColourPicker(callback, defaultColour) {
        defaultColour = defaultColour || "#ff0000";
        var overlay = document.createElement("div");
        overlay.className = "colour-picker-overlay";

        var picker = document.createElement("div");
        picker.className = "colour-picker";

        var title = document.createElement("div");
        title.className = "colour-picker-title";
        title.textContent = "Select colour";
        picker.appendChild(title);

        var swatches = document.createElement("div");
        swatches.className = "colour-picker-swatches";

        var selectedColour = defaultColour;

        COLOUR_PRESETS.forEach(function (preset) {
            var swatch = document.createElement("div");
            swatch.className = "colour-swatch";
            if (preset.hex === defaultColour) swatch.classList.add("selected");
            swatch.style.background = preset.hex;
            swatch.title = preset.name;
            swatch.addEventListener("click", function () {
                selectedColour = preset.hex;
                hexInput.value = preset.hex;
                swatches.querySelectorAll(".colour-swatch").forEach(function (s) { s.classList.remove("selected"); });
                swatch.classList.add("selected");
            });
            swatch.addEventListener("dblclick", function () {
                selectedColour = preset.hex;
                cleanup();
                callback(selectedColour);
            });
            swatches.appendChild(swatch);
        });
        picker.appendChild(swatches);

        var hexRow = document.createElement("div");
        hexRow.className = "colour-picker-hex";
        var hexInput = document.createElement("input");
        hexInput.type = "text";
        hexInput.value = defaultColour;
        hexInput.placeholder = "#hex";
        hexInput.addEventListener("input", function () {
            if (/^#[0-9a-fA-F]{6}$/.test(hexInput.value)) {
                selectedColour = hexInput.value;
                swatches.querySelectorAll(".colour-swatch").forEach(function (s) { s.classList.remove("selected"); });
            }
        });
        hexRow.appendChild(hexInput);

        var okBtn = document.createElement("button");
        okBtn.textContent = "OK";
        okBtn.addEventListener("click", function () {
            if (/^#[0-9a-fA-F]{3,6}$/.test(hexInput.value)) selectedColour = hexInput.value;
            cleanup();
            callback(selectedColour);
        });
        hexRow.appendChild(okBtn);

        var cancelBtn = document.createElement("button");
        cancelBtn.textContent = "Cancel";
        cancelBtn.style.background = "#555";
        cancelBtn.addEventListener("click", function () {
            cleanup();
            callback(null);
        });
        hexRow.appendChild(cancelBtn);

        picker.appendChild(hexRow);
        overlay.appendChild(picker);

        overlay.addEventListener("click", function (e) {
            if (e.target === overlay) {
                cleanup();
                callback(null);
            }
        });

        document.body.appendChild(overlay);

        function cleanup() {
            if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
        }
    }

    // --- Haversine distance & bearing ---
    function haversineDistance(lat1, lon1, lat2, lon2) {
        var R = 6371000;
        var phi1 = lat1 * Math.PI / 180;
        var phi2 = lat2 * Math.PI / 180;
        var dphi = (lat2 - lat1) * Math.PI / 180;
        var dlam = (lon2 - lon1) * Math.PI / 180;
        var a = Math.sin(dphi / 2) * Math.sin(dphi / 2) +
            Math.cos(phi1) * Math.cos(phi2) * Math.sin(dlam / 2) * Math.sin(dlam / 2);
        return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    }

    function compassBearing(lat1, lon1, lat2, lon2) {
        var phi1 = lat1 * Math.PI / 180;
        var phi2 = lat2 * Math.PI / 180;
        var dlam = (lon2 - lon1) * Math.PI / 180;
        var y = Math.sin(dlam) * Math.cos(phi2);
        var x = Math.cos(phi1) * Math.sin(phi2) - Math.sin(phi1) * Math.cos(phi2) * Math.cos(dlam);
        var brng = Math.atan2(y, x) * 180 / Math.PI;
        return (brng + 360) % 360;
    }

    // ===== COORDINATE FORMAT TOGGLE =====

    coordFormatSelect.addEventListener("change", function () {
        BFT.coordFormat = coordFormatSelect.value;
        // Refresh all open popups
        Object.keys(positions).forEach(function (cs) {
            if (markers[cs]) {
                markers[cs].setPopupContent(popupContent(positions[cs]));
            }
        });
        updateHealthTable();
    });

    // Mouse position control
    var MousePositionControl = L.Control.extend({
        options: { position: "bottomleft" },
        onAdd: function () {
            var div = L.DomUtil.create("div", "mouse-position-control");
            div.innerHTML = "MGRS: --";
            this._div = div;
            return div;
        },
        update: function (lat, lon) {
            var label = BFT.coordFormat.toUpperCase();
            this._div.innerHTML = label + ": " + BFT.formatCoord(lat, lon);
        }
    });
    var mousePosCtrl = new MousePositionControl();
    mousePosCtrl.addTo(map);

    map.on("mousemove", function (e) {
        mousePosCtrl.update(e.latlng.lat, e.latlng.lng);
    });

    // --- Popup content ---
    function popupContent(r) {
        var html = "<strong>" + escapeHtml(r.callsign) + "</strong>";
        if (r.stale) html += '<span class="stale-badge">STALE</span>';
        html += "<br>Last seen: " + relativeTime(r.timestamp);
        if (r.battery != null) html += "<br>Battery: " + r.battery.toFixed(0) + "%";
        if (r.altitude) html += "<br>Alt: " + r.altitude.toFixed(0) + " m";
        if (r.speed != null) html += "<br>Speed: " + r.speed.toFixed(1) + " km/h";
        if (r.heading != null) html += "<br>Heading: " + r.heading.toFixed(0) + "&deg;";
        html += "<br><small>" + BFT.formatCoord(r.lat, r.lon) + "</small>";
        return html;
    }

    // --- Upsert marker ---
    function upsertMarker(report) {
        positions[report.callsign] = report;
        registerCallsign(report.callsign);
        var latlng = [report.lat, report.lon];
        var isSOS = !!sosMarkerOverlays[report.callsign];
        var color = isSOS ? RED : (report.stale ? GREY : BLUE);

        if (markers[report.callsign]) {
            markers[report.callsign].setLatLng(latlng);
            markers[report.callsign].setStyle({ color: color, fillColor: color });
            markers[report.callsign].setPopupContent(popupContent(report));

            labels[report.callsign].setLatLng(latlng);
            var labelEl = labels[report.callsign].getElement();
            if (labelEl) {
                labelEl.className = "callsign-label" + (report.stale ? " stale" : "");
            }
        } else {
            var circle = L.circleMarker(latlng, {
                radius: MARKER_RADIUS,
                color: color,
                fillColor: color,
                fillOpacity: 0.7,
                weight: 2,
            }).addTo(map);
            circle.bindPopup(popupContent(report));
            markers[report.callsign] = circle;

            var label = L.marker(latlng, {
                icon: L.divIcon({
                    className: "callsign-label" + (report.stale ? " stale" : ""),
                    html: escapeHtml(report.callsign),
                    iconSize: null,
                    iconAnchor: [-12, 4],
                }),
                interactive: false,
            }).addTo(map);
            labels[report.callsign] = label;
        }

        // Move SOS ring with the node
        if (sosMarkerOverlays[report.callsign]) {
            sosMarkerOverlays[report.callsign].setLatLng(latlng);
        }

        if (trailsEnabled) {
            updateTrailForCallsign(report.callsign);
        }

        updateNodeCount();
    }

    // Expose for topology
    BFT._getPosition = function (callsign) {
        return positions[callsign] || null;
    };

    function fitBoundsToAll() {
        var coords = Object.values(positions).map(function (r) {
            return [r.lat, r.lon];
        });
        if (coords.length > 0) {
            map.fitBounds(coords, { padding: [40, 40], maxZoom: 16 });
        }
    }

    setInterval(function () {
        var now = Date.now() / 1000;
        Object.values(positions).forEach(function (r) {
            var wasStale = r.stale;
            r.stale = (now - r.timestamp) > 60;
            if (r.stale !== wasStale) {
                upsertMarker(r);
            }
        });
    }, 5000);

    // ===== WAYPOINTS =====

    function addWaypointMarker(wp) {
        if (waypointMarkers[wp.id]) {
            removeWaypointMarker(wp.id);
        }
        var icon = WAYPOINT_ICONS[wp.waypoint_type] || "✓";
        var marker = L.marker([wp.lat, wp.lon], {
            icon: L.divIcon({
                className: "waypoint-icon",
                html: icon,
                iconSize: [24, 24],
                iconAnchor: [12, 12],
            }),
        }).addTo(waypointLayer);

        var popupHtml = "<strong>" + escapeHtml(wp.name) + "</strong>";
        popupHtml += "<br>Type: " + escapeHtml(wp.waypoint_type);
        if (wp.description) popupHtml += "<br>" + escapeHtml(wp.description);
        popupHtml += "<br>" + BFT.formatCoord(wp.lat, wp.lon);
        popupHtml += "<br>By: " + escapeHtml(wp.created_by);
        popupHtml += '<br><button onclick="window._deleteWaypoint(\'' + wp.id + '\')" style="margin-top:4px;padding:2px 8px;background:#f44336;color:#fff;border:none;border-radius:3px;cursor:pointer;">Delete</button>';
        marker.bindPopup(popupHtml);

        var label = L.marker([wp.lat, wp.lon], {
            icon: L.divIcon({
                className: "waypoint-label",
                html: escapeHtml(wp.name),
                iconSize: null,
                iconAnchor: [-14, 4],
            }),
            interactive: false,
        }).addTo(waypointLayer);

        waypointMarkers[wp.id] = { marker: marker, label: label, data: wp };
    }

    function removeWaypointMarker(id) {
        var entry = waypointMarkers[id];
        if (entry) {
            waypointLayer.removeLayer(entry.marker);
            waypointLayer.removeLayer(entry.label);
            delete waypointMarkers[id];
        }
    }

    window._deleteWaypoint = function (id) {
        fetch("/api/waypoints/" + id, { method: "DELETE" });
        removeWaypointMarker(id);
    };

    function toggleWaypointMode() {
        waypointPlaceMode = !waypointPlaceMode;
        waypointBtn.classList.toggle("active", waypointPlaceMode);
        document.body.classList.toggle("waypoint-mode", waypointPlaceMode);
        if (waypointPlaceMode) {
            disableMeasureMode();
            disableRouteMode();
            disableControlMode();
            disableFireMissionMode();
        }
    }

    function handleWaypointPlacement(e) {
        if (!waypointPlaceMode) return;
        var lat = e.latlng.lat;
        var lon = e.latlng.lng;

        // Build a small popup form instead of multiple prompts
        var typeOptions = Object.keys(WAYPOINT_ICONS).map(function (t) {
            return '<option value="' + t + '"' + (t === "checkpoint" ? ' selected' : '') + '>' + WAYPOINT_ICONS[t] + ' ' + t + '</option>';
        }).join("");

        var formHtml = '<div style="min-width:180px">';
        formHtml += '<div style="margin-bottom:4px"><label style="font-size:11px;color:#aaa">Name:</label><br><input id="wp-name-input" type="text" style="width:100%;padding:3px 6px;background:#16213e;color:#e0e0e0;border:1px solid #444;border-radius:3px;font-size:12px" placeholder="Waypoint name" /></div>';
        formHtml += '<div style="margin-bottom:4px"><label style="font-size:11px;color:#aaa">Type:</label><br><select id="wp-type-select" style="width:100%;padding:3px 6px;background:#16213e;color:#e0e0e0;border:1px solid #444;border-radius:3px;font-size:12px">' + typeOptions + '</select></div>';
        formHtml += '<div style="margin-bottom:4px"><label style="font-size:11px;color:#aaa">Description:</label><br><input id="wp-desc-input" type="text" style="width:100%;padding:3px 6px;background:#16213e;color:#e0e0e0;border:1px solid #444;border-radius:3px;font-size:12px" placeholder="Optional" /></div>';
        formHtml += '<button id="wp-submit-btn" style="padding:4px 12px;background:#2196F3;color:#fff;border:none;border-radius:3px;cursor:pointer;font-size:12px;margin-top:2px">Create</button>';
        formHtml += '</div>';

        var popup = L.popup({ closeOnClick: false, autoClose: false })
            .setLatLng([lat, lon])
            .setContent(formHtml)
            .openOn(map);

        // Exit waypoint mode when popup is closed via × button
        popup.on("remove", function () {
            if (waypointPlaceMode) toggleWaypointMode();
        });

        // Defer event binding until popup is in DOM
        setTimeout(function () {
            var nameInput = document.getElementById("wp-name-input");
            var typeSelect = document.getElementById("wp-type-select");
            var descInput = document.getElementById("wp-desc-input");
            var submitBtn = document.getElementById("wp-submit-btn");
            if (nameInput) nameInput.focus();

            if (submitBtn) submitBtn.addEventListener("click", function () {
                var name = nameInput.value.trim();
                if (!name) { nameInput.style.borderColor = "#f44336"; return; }
                var typeChoice = typeSelect.value;
                var desc = descInput.value.trim();

                fetch("/api/waypoints", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        name: name,
                        lat: lat,
                        lon: lon,
                        waypoint_type: typeChoice,
                        icon: WAYPOINT_ICONS[typeChoice] || "\u2713",
                        description: desc || "",
                    }),
                });

                map.closePopup(popup);
            });
        }, 50);
    }

    function loadWaypoints() {
        fetch("/api/waypoints")
            .then(function (r) { return r.json(); })
            .then(function (wps) {
                wps.forEach(function (wp) { addWaypointMarker(wp); });
            });
    }

    // ===== SOS/PANIC ALERT =====

    var _sosAudioCtx = null;

    function playSOSBeep() {
        try {
            if (!_sosAudioCtx) _sosAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
            var ctx = _sosAudioCtx;
            for (var i = 0; i < 3; i++) {
                var osc = ctx.createOscillator();
                var gain = ctx.createGain();
                osc.connect(gain);
                gain.connect(ctx.destination);
                osc.frequency.value = 880;
                osc.type = "square";
                gain.gain.value = 0.3;
                var start = ctx.currentTime + i * 0.2;
                osc.start(start);
                osc.stop(start + 0.1);
            }
        } catch (e) { /* Audio not available */ }
    }

    function handleSOSAlert(alert) {
        activeSOSAlerts[alert.id] = alert;
        if (alert.acknowledged) {
            delete activeSOSAlerts[alert.id];
            if (sosMarkerOverlays[alert.callsign]) {
                map.removeLayer(sosMarkerOverlays[alert.callsign]);
                delete sosMarkerOverlays[alert.callsign];
            }
            if (markers[alert.callsign] && positions[alert.callsign]) {
                var pos = positions[alert.callsign];
                var color = pos.stale ? GREY : BLUE;
                markers[alert.callsign].setStyle({ color: color, fillColor: color });
            }
        } else {
            playSOSBeep();
            if (!sosMarkerOverlays[alert.callsign]) {
                // Use node's current position if available, fall back to alert position
                var pos = positions[alert.callsign];
                var ringLat = pos ? pos.lat : alert.lat;
                var ringLon = pos ? pos.lon : alert.lon;
                var ring = L.circleMarker([ringLat, ringLon], {
                    radius: 18, color: RED, fill: false, weight: 3, opacity: 0.9, className: "sos-ring",
                }).addTo(map);
                sosMarkerOverlays[alert.callsign] = ring;
            }
            if (markers[alert.callsign]) {
                markers[alert.callsign].setStyle({ color: RED, fillColor: RED });
            }
        }
        updateSOSBanner();
    }

    function updateSOSBanner() {
        var active = Object.values(activeSOSAlerts).filter(function (a) { return !a.acknowledged; });
        if (active.length === 0) {
            sosBanner.classList.add("hidden");
            document.body.classList.remove("sos-active");
            return;
        }
        var latest = active[active.length - 1];
        sosText.textContent = "SOS: " + latest.callsign + " — " + latest.message;
        sosAckBtn.onclick = function () {
            fetch("/api/sos/" + latest.id + "/acknowledge", { method: "POST" });
        };
        sosBanner.classList.remove("hidden");
        document.body.classList.add("sos-active");
    }

    // ===== BREADCRUMB TRAILS =====

    function updateTrailForCallsign(callsign) {
        fetch("/api/trails/" + encodeURIComponent(callsign) + "?max_age=300")
            .then(function (r) { return r.json(); })
            .then(function (trail) {
                if (trailPolylines[callsign]) {
                    trailLayer.removeLayer(trailPolylines[callsign]);
                }
                if (!trailsEnabled || trail.length < 2) return;
                var coords = trail.map(function (p) { return [p.lat, p.lon]; });
                var pos = positions[callsign];
                var color = (pos && pos.stale) ? GREY : BLUE;
                var line = L.polyline(coords, { color: color, weight: 2, opacity: 0.6 }).addTo(trailLayer);
                trailPolylines[callsign] = line;
            });
    }

    function loadAllTrails() {
        fetch("/api/trails?max_age=300")
            .then(function (r) { return r.json(); })
            .then(function (trails) {
                Object.keys(trails).forEach(function (cs) {
                    if (trails[cs].length < 2) return;
                    var coords = trails[cs].map(function (p) { return [p.lat, p.lon]; });
                    var pos = positions[cs];
                    var color = (pos && pos.stale) ? GREY : BLUE;
                    if (trailPolylines[cs]) trailLayer.removeLayer(trailPolylines[cs]);
                    var line = L.polyline(coords, { color: color, weight: 2, opacity: 0.6 }).addTo(trailLayer);
                    trailPolylines[cs] = line;
                });
            });
    }

    function toggleTrails() {
        trailsEnabled = !trailsEnabled;
        trailsBtn.classList.toggle("active", trailsEnabled);
        if (trailsEnabled) loadAllTrails();
        else { trailLayer.clearLayers(); trailPolylines = {}; }
    }

    // ===== NODE HEALTH DASHBOARD =====

    function toggleHealth() {
        healthOpen = !healthOpen;
        healthBtn.classList.toggle("active", healthOpen);
        if (healthOpen) {
            healthPanel.classList.remove("hidden");
            document.body.classList.add("health-open");
            updateHealthTable();
        } else {
            healthPanel.classList.add("hidden");
            document.body.classList.remove("health-open");
        }
        setTimeout(function () { map.invalidateSize(); }, 300);
    }

    function updateHealthTable() {
        if (!healthOpen) return;
        var now = Date.now() / 1000;
        var rows = "";
        var sorted = Object.values(positions).sort(function (a, b) {
            return a.callsign.localeCompare(b.callsign);
        });
        sorted.forEach(function (p) {
            var age = now - p.timestamp;
            var battClass = "health-good";
            var battVal = "N/A";
            if (p.battery != null) {
                battVal = p.battery.toFixed(0) + "%";
                if (p.battery < 20) battClass = "health-bad";
                else if (p.battery < 50) battClass = "health-warn";
            }
            var freshClass = "health-good";
            if (age > 120) freshClass = "health-bad";
            else if (age > 60) freshClass = "health-warn";

            // Signal column
            var sigHtml = "N/A";
            if (BFT.getBestSignalForNode) {
                var best = BFT.getBestSignalForNode(p.callsign);
                if (best) {
                    var sigClass = "health-good";
                    if (best.snr != null) {
                        if (best.snr < 0) sigClass = "health-bad";
                        else if (best.snr < 5) sigClass = "health-warn";
                        sigHtml = '<span class="' + sigClass + '">' + best.snr.toFixed(1) + ' dB</span>';
                    } else if (best.rssi != null) {
                        if (best.rssi < -110) sigClass = "health-bad";
                        else if (best.rssi < -90) sigClass = "health-warn";
                        sigHtml = '<span class="' + sigClass + '">' + best.rssi + ' dBm</span>';
                    }
                }
            }

            rows += "<tr>";
            rows += "<td>" + escapeHtml(p.callsign) + "</td>";
            rows += '<td class="' + battClass + '">' + battVal + "</td>";
            rows += '<td class="' + freshClass + '">' + relativeTime(p.timestamp) + "</td>";
            rows += "<td>" + (p.speed != null ? p.speed.toFixed(1) + " km/h" : "N/A") + "</td>";
            rows += "<td>" + BFT.formatCoord(p.lat, p.lon) + "</td>";
            rows += "<td>" + sigHtml + "</td>";
            rows += "</tr>";
        });
        healthTbody.innerHTML = rows;
    }

    setInterval(updateHealthTable, 5000);

    // ===== DISTANCE/BEARING TOOL =====

    function toggleMeasureMode() {
        measureMode = !measureMode;
        measureBtn.classList.toggle("active", measureMode);
        document.body.classList.toggle("measure-mode", measureMode);
        if (measureMode) {
            disableWaypointMode();
            disableRouteMode();
            disableControlMode();
            disableFireMissionMode();
            clearMeasurement();
        } else {
            clearMeasurement();
        }
    }

    function disableMeasureMode() {
        if (measureMode) {
            measureMode = false;
            measureBtn.classList.remove("active");
            document.body.classList.remove("measure-mode");
            clearMeasurement();
        }
    }

    function disableWaypointMode() {
        if (waypointPlaceMode) {
            waypointPlaceMode = false;
            waypointBtn.classList.remove("active");
            document.body.classList.remove("waypoint-mode");
        }
    }

    function clearMeasurement() {
        measureLayer.clearLayers();
        measurePoints = [];
        measureMarkers = [];
        measureLine = null;
        lastElevationData = null;
    }

    function handleMeasureClick(e) {
        if (!measureMode) return;
        measurePoints.push(e.latlng);

        var dot = L.circleMarker(e.latlng, {
            radius: 5, color: "#ffd54f", fillColor: "#ffd54f", fillOpacity: 1, weight: 1,
        }).addTo(measureLayer);
        measureMarkers.push(dot);

        if (measurePoints.length === 2) {
            var p1 = measurePoints[0];
            var p2 = measurePoints[1];
            var dist = haversineDistance(p1.lat, p1.lng, p2.lat, p2.lng);
            var bearing = compassBearing(p1.lat, p1.lng, p2.lat, p2.lng);

            var distStr = dist >= 1000 ? (dist / 1000).toFixed(2) + " km" : dist.toFixed(0) + " m";
            var bearingMils = Math.round(BFT.degreesToMils(bearing));
            var label = distStr + " | " + bearing.toFixed(0) + "\u00b0 (" + bearingMils + " mils)";

            measureLine = L.polyline([p1, p2], {
                color: "#ffd54f", weight: 2, dashArray: "8, 6",
            }).addTo(measureLayer);

            var midLat = (p1.lat + p2.lat) / 2;
            var midLng = (p1.lng + p2.lng) / 2;
            var labelHtml = label + ' <button onclick="window._showElevProfile()" style="margin-left:4px;padding:1px 6px;background:#2196F3;color:#fff;border:none;border-radius:3px;cursor:pointer;font-size:10px;">Profile</button>';
            L.marker([midLat, midLng], {
                icon: L.divIcon({ className: "measure-label", html: labelHtml, iconSize: null }),
                interactive: true,
            }).addTo(measureLayer);

            // Terrain profile
            fetchElevationProfile(p1, p2, dist);

            measureMode = false;
            measureBtn.classList.remove("active");
            document.body.classList.remove("measure-mode");
        }
    }

    // ===== TERRAIN PROFILE (ELEVATION) =====

    function fetchElevationProfile(p1, p2, totalDist) {
        var numSamples = 20;
        var points = [];
        for (var i = 0; i <= numSamples; i++) {
            var frac = i / numSamples;
            points.push([
                p1.lat + (p2.lat - p1.lat) * frac,
                p1.lng + (p2.lng - p1.lng) * frac,
            ]);
        }

        fetch("/api/elevation", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ points: points }),
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.elevations && data.elevations.length > 0) {
                drawElevationProfile(data.elevations, totalDist, p1, p2);
            }
        })
        .catch(function () { /* elevation not available */ });
    }

    var lastElevationData = null; // store for re-opening profile

    function drawElevationProfile(elevations, totalDist, p1, p2) {
        lastElevationData = { elevations: elevations, totalDist: totalDist, p1: p1, p2: p2 };

        var canvas = document.createElement("canvas");
        canvas.width = 200;
        canvas.height = 80;
        canvas.className = "elevation-profile";

        var ctx = canvas.getContext("2d");
        var minElev = Math.min.apply(null, elevations);
        var maxElev = Math.max.apply(null, elevations);
        var range = maxElev - minElev || 1;
        var w = canvas.width;
        var h = canvas.height - 16;
        var padTop = 12;

        // Background
        ctx.fillStyle = "#16213e";
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        // Profile fill
        ctx.beginPath();
        ctx.moveTo(0, padTop + h);
        for (var i = 0; i < elevations.length; i++) {
            var x = (i / (elevations.length - 1)) * w;
            var y = padTop + h - ((elevations[i] - minElev) / range) * h;
            ctx.lineTo(x, y);
        }
        ctx.lineTo(w, padTop + h);
        ctx.closePath();
        ctx.fillStyle = "rgba(33, 150, 243, 0.3)";
        ctx.fill();

        // Profile line
        ctx.beginPath();
        for (var j = 0; j < elevations.length; j++) {
            var x2 = (j / (elevations.length - 1)) * w;
            var y2 = padTop + h - ((elevations[j] - minElev) / range) * h;
            if (j === 0) ctx.moveTo(x2, y2);
            else ctx.lineTo(x2, y2);
        }
        ctx.strokeStyle = "#2196F3";
        ctx.lineWidth = 1.5;
        ctx.stroke();

        // Labels
        ctx.fillStyle = "#aaa";
        ctx.font = "9px sans-serif";
        ctx.fillText(Math.round(maxElev) + "m", 2, padTop);
        ctx.fillText(Math.round(minElev) + "m", 2, padTop + h - 2);

        // Stats
        var totalAscent = 0, totalDescent = 0;
        for (var k = 1; k < elevations.length; k++) {
            var diff = elevations[k] - elevations[k - 1];
            if (diff > 0) totalAscent += diff;
            else totalDescent -= diff;
        }
        ctx.fillText("\u2191" + Math.round(totalAscent) + "m \u2193" + Math.round(totalDescent) + "m", w - 90, padTop);

        // Anchor popup at endpoint p2 instead of midpoint
        var popup = L.popup({
            closeOnClick: false,
            autoClose: false,
            className: "elevation-popup",
        })
        .setLatLng([p2.lat, p2.lng])
        .setContent(canvas)
        .addTo(measureLayer);
    }

    window._showElevProfile = function () {
        if (lastElevationData) {
            drawElevationProfile(
                lastElevationData.elevations,
                lastElevationData.totalDist,
                lastElevationData.p1,
                lastElevationData.p2
            );
        }
    };

    // ===== GEOFENCE ALERTS =====

    function addGeofenceLayer(gf) {
        var fillColor = gf.geofence_type === "inclusion" ? "#4caf50" : "#f44336";
        var layer;
        if (gf.shape === "circle" && gf.center_lat != null && gf.center_lon != null) {
            layer = L.circle([gf.center_lat, gf.center_lon], {
                radius: gf.radius_m || 100, color: fillColor, fillColor: fillColor, fillOpacity: 0.2, weight: 2,
            }).addTo(geofenceLayer);
        } else if (gf.shape === "polygon" && gf.polygon && gf.polygon.length >= 3) {
            layer = L.polygon(gf.polygon, {
                color: fillColor, fillColor: fillColor, fillOpacity: 0.2, weight: 2,
            }).addTo(geofenceLayer);
        } else {
            return;
        }

        var popupHtml = "<strong>" + escapeHtml(gf.name) + "</strong>";
        popupHtml += "<br>Type: " + escapeHtml(gf.geofence_type);
        popupHtml += '<br><button onclick="window._deleteGeofence(\'' + gf.id + '\')" style="margin-top:4px;padding:2px 8px;background:#f44336;color:#fff;border:none;border-radius:3px;cursor:pointer;">Delete</button>';
        layer.bindPopup(popupHtml);
        geofenceLayers[gf.id] = layer;
    }

    window._deleteGeofence = function (id) {
        fetch("/api/geofences/" + id, { method: "DELETE" });
        if (geofenceLayers[id]) {
            geofenceLayer.removeLayer(geofenceLayers[id]);
            delete geofenceLayers[id];
        }
    };

    function handleGeofenceAlert(alert) {
        var msg = alert.callsign + " " + alert.alert_type + " " + alert.geofence_name;
        var type = alert.alert_type === "violated" ? "danger" : "warning";
        var onclick = null;
        if (alert.lat != null && alert.lon != null) {
            onclick = (function (lat, lon) {
                return function () { map.setView([lat, lon], 16); };
            })(alert.lat, alert.lon);
        } else if (positions[alert.callsign]) {
            onclick = (function (cs) {
                return function () {
                    var p = positions[cs];
                    if (p) map.setView([p.lat, p.lon], 16);
                };
            })(alert.callsign);
        }
        showToast(msg, type, onclick);
    }

    function toggleGeofenceMode() {
        geofenceDrawMode = !geofenceDrawMode;
        geofenceBtn.classList.toggle("active", geofenceDrawMode);

        if (geofenceDrawMode) {
            disableAnnotateMode();
            disableControlMode();
            if (!geofenceDrawControl) {
                var drawnItems = new L.FeatureGroup();
                map.addLayer(drawnItems);
                geofenceDrawControl = new L.Control.Draw({
                    position: "topleft",
                    draw: {
                        polyline: false, rectangle: false, marker: false, circlemarker: false,
                        polygon: { allowIntersection: false, shapeOptions: { color: "#4caf50" } },
                        circle: { shapeOptions: { color: "#4caf50" } },
                    },
                    edit: false,
                });
            }
            map.addControl(geofenceDrawControl);
        } else {
            if (geofenceDrawControl) map.removeControl(geofenceDrawControl);
        }
    }

    function disableAnnotateMode() {
        if (annotateDrawMode) {
            annotateDrawMode = false;
            annotateBtn.classList.remove("active");
            if (annotateDrawControl) map.removeControl(annotateDrawControl);
        }
    }

    function loadGeofences() {
        fetch("/api/geofences")
            .then(function (r) { return r.json(); })
            .then(function (gfs) {
                gfs.forEach(function (gf) { addGeofenceLayer(gf); });
            });
    }

    // ===== MAP ANNOTATIONS =====

    function addAnnotationLayer(ann) {
        var layer;
        if (ann.annotation_type === "marker" && ann.lat != null && ann.lon != null) {
            layer = L.marker([ann.lat, ann.lon]).addTo(annotationLayer);
        } else if (ann.annotation_type === "circle" && ann.lat != null && ann.lon != null) {
            layer = L.circle([ann.lat, ann.lon], {
                radius: ann.radius_m || 50, color: ann.color, fillColor: ann.color, fillOpacity: 0.2, weight: 2,
            }).addTo(annotationLayer);
        } else if (ann.annotation_type === "line" && ann.coordinates && ann.coordinates.length >= 2) {
            layer = L.polyline(ann.coordinates, { color: ann.color, weight: 3 }).addTo(annotationLayer);
        } else if (ann.annotation_type === "polygon" && ann.coordinates && ann.coordinates.length >= 3) {
            layer = L.polygon(ann.coordinates, { color: ann.color, fillColor: ann.color, fillOpacity: 0.2, weight: 2 }).addTo(annotationLayer);
        } else {
            return;
        }

        var popupHtml = "";
        if (ann.label) popupHtml += "<strong>" + escapeHtml(ann.label) + "</strong><br>";
        popupHtml += '<button onclick="window._deleteAnnotation(\'' + ann.id + '\')" style="padding:2px 8px;background:#f44336;color:#fff;border:none;border-radius:3px;cursor:pointer;">Delete</button>';
        layer.bindPopup(popupHtml);
        annotationLayers[ann.id] = layer;
    }

    window._deleteAnnotation = function (id) {
        fetch("/api/annotations/" + id, { method: "DELETE" });
        if (annotationLayers[id]) {
            annotationLayer.removeLayer(annotationLayers[id]);
            delete annotationLayers[id];
        }
    };

    function toggleAnnotateMode() {
        annotateDrawMode = !annotateDrawMode;
        annotateBtn.classList.toggle("active", annotateDrawMode);

        if (annotateDrawMode) {
            if (geofenceDrawMode) {
                geofenceDrawMode = false;
                geofenceBtn.classList.remove("active");
                if (geofenceDrawControl) map.removeControl(geofenceDrawControl);
            }
            disableControlMode();

            if (!annotateDrawControl) {
                annotateDrawControl = new L.Control.Draw({
                    position: "topleft",
                    draw: {
                        rectangle: false, circlemarker: false,
                        polyline: { shapeOptions: { color: "#ff0000" } },
                        polygon: { allowIntersection: false, shapeOptions: { color: "#ff0000" } },
                        marker: true,
                        circle: { shapeOptions: { color: "#ff0000" } },
                    },
                    edit: false,
                });
            }
            map.addControl(annotateDrawControl);
        } else {
            if (annotateDrawControl) map.removeControl(annotateDrawControl);
        }
    }

    function loadAnnotations() {
        fetch("/api/annotations")
            .then(function (r) { return r.json(); })
            .then(function (anns) {
                anns.forEach(function (a) { addAnnotationLayer(a); });
            });
    }

    // Handle Leaflet.draw created events
    map.on(L.Draw.Event.CREATED, function (e) {
        var layer = e.layer;
        var layerType = e.layerType;

        if (geofenceDrawMode) {
            var name = prompt("Geofence name:");
            if (!name) return;
            var gfType = prompt("Type (inclusion/exclusion):", "inclusion") || "inclusion";

            var body = { name: name, geofence_type: gfType };
            if (layerType === "circle") {
                var center = layer.getLatLng();
                body.shape = "circle";
                body.center_lat = center.lat;
                body.center_lon = center.lng;
                body.radius_m = layer.getRadius();
            } else if (layerType === "polygon") {
                var latlngs = layer.getLatLngs()[0];
                body.shape = "polygon";
                body.polygon = latlngs.map(function (ll) { return [ll.lat, ll.lng]; });
            }

            fetch("/api/geofences", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            }).then(function (r) { return r.json(); })
              .then(function (gf) { addGeofenceLayer(gf); });

        } else if (annotateDrawMode) {
            var annLabel = prompt("Label (optional):", "") || "";

            showColourPicker(function (color) {
                if (!color) return;

                var annBody = { label: annLabel, color: color };
                if (layerType === "marker") {
                    var ll = layer.getLatLng();
                    annBody.annotation_type = "marker";
                    annBody.lat = ll.lat;
                    annBody.lon = ll.lng;
                } else if (layerType === "circle") {
                    var c = layer.getLatLng();
                    annBody.annotation_type = "circle";
                    annBody.lat = c.lat;
                    annBody.lon = c.lng;
                    annBody.radius_m = layer.getRadius();
                } else if (layerType === "polyline") {
                    annBody.annotation_type = "line";
                    annBody.coordinates = layer.getLatLngs().map(function (ll2) { return [ll2.lat, ll2.lng]; });
                } else if (layerType === "polygon") {
                    annBody.annotation_type = "polygon";
                    annBody.coordinates = layer.getLatLngs()[0].map(function (ll2) { return [ll2.lat, ll2.lng]; });
                }

                fetch("/api/annotations", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(annBody),
                }).then(function (r) { return r.json(); })
                  .then(function (ann) { addAnnotationLayer(ann); });
            }, "#ff0000");
        }
    });

    // ===== SESSION RECORDING & REPLAY =====

    function toggleRecording() {
        if (isRecording) {
            fetch("/api/recording/stop", { method: "POST" })
                .then(function () {
                    isRecording = false;
                    recordBtn.classList.remove("recording");
                    recordBtn.textContent = "Record";
                });
        } else {
            var name = prompt("Session name:", "session-" + Date.now());
            if (!name) return;
            fetch("/api/recording/start", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name: name }),
            }).then(function () {
                isRecording = true;
                recordBtn.classList.add("recording");
                recordBtn.textContent = "Stop Rec";
            });
        }
    }

    function startReplay() {
        fetch("/api/sessions")
            .then(function (r) { return r.json(); })
            .then(function (sessions) {
                if (sessions.length === 0) { alert("No recorded sessions found."); return; }
                var choice = prompt("Available sessions:\n" + sessions.join("\n") + "\n\nEnter session name:");
                if (!choice) return;
                fetch("/api/sessions/" + encodeURIComponent(choice))
                    .then(function (r) { return r.json(); })
                    .then(function (events) {
                        if (events.length === 0) { alert("Session is empty."); return; }
                        replayEvents = events;
                        replayIndex = 0;
                        replayActive = true;
                        replayPaused = false;
                        replayScrubber.max = events.length - 1;
                        replayScrubber.value = 0;
                        replayControls.classList.remove("hidden");
                        runReplay();
                    });
            });
    }

    function runReplay() {
        if (!replayActive || replayPaused) return;
        if (replayIndex >= replayEvents.length) { stopReplay(); return; }
        var ev = replayEvents[replayIndex];
        processReplayEvent(ev);
        replayScrubber.value = replayIndex;
        replayTimeEl.textContent = (replayIndex + 1) + "/" + replayEvents.length;
        replayIndex++;
        replayTimer = setTimeout(runReplay, 100);
    }

    function processReplayEvent(ev) {
        if (ev.event_type === "position") upsertMarker(ev.data);
        else if (ev.event_type === "message") addMessage(ev.data);
        else if (ev.event_type === "waypoint") addWaypointMarker(ev.data);
        else if (ev.event_type === "sos") handleSOSAlert(ev.data);
        else if (ev.event_type === "report") BFT.handleIncomingReport(ev.data);
    }

    function stopReplay() {
        replayActive = false;
        replayPaused = false;
        if (replayTimer) clearTimeout(replayTimer);
        replayControls.classList.add("hidden");
    }

    // ===== ROUTE PLANNING =====

    function toggleRouteMode() {
        routeMode = !routeMode;
        routeBtn.classList.toggle("active", routeMode);
        document.body.classList.toggle("route-mode", routeMode);
        if (routeMode) {
            disableMeasureMode();
            disableWaypointMode();
            disableControlMode();
            disableFireMissionMode();
            clearRoutePreview();
            showToast("Click map to add route points. Double-click to finish.", "info");
        } else {
            clearRoutePreview();
        }
    }

    function disableRouteMode() {
        if (routeMode) {
            routeMode = false;
            routeBtn.classList.remove("active");
            document.body.classList.remove("route-mode");
            clearRoutePreview();
        }
    }

    function clearRoutePreview() {
        routePreviewMarkers.forEach(function (m) { map.removeLayer(m); });
        routePreviewMarkers = [];
        if (routePreviewLine) { map.removeLayer(routePreviewLine); routePreviewLine = null; }
        routePoints = [];
    }

    function handleRouteClick(e) {
        if (!routeMode) return;
        routePoints.push({ lat: e.latlng.lat, lon: e.latlng.lng, name: "WP" + (routePoints.length + 1), order: routePoints.length });

        var num = routePoints.length;
        var marker = L.marker(e.latlng, {
            icon: L.divIcon({
                className: "route-number-icon",
                html: String(num),
                iconSize: [22, 22],
                iconAnchor: [11, 11],
            }),
        }).addTo(map);
        routePreviewMarkers.push(marker);

        if (routePoints.length >= 2) {
            if (routePreviewLine) map.removeLayer(routePreviewLine);
            var coords = routePoints.map(function (p) { return [p.lat, p.lon]; });
            routePreviewLine = L.polyline(coords, { color: "#00ff00", weight: 3, dashArray: "8, 4" }).addTo(map);
        }
    }

    function finishRoute() {
        if (routePoints.length < 2) {
            showToast("Need at least 2 points for a route", "warning");
            return;
        }
        var name = prompt("Route name:", "Route " + (Object.keys(routeLayers).length + 1));
        if (!name) { clearRoutePreview(); disableRouteMode(); return; }

        var savedPoints = routePoints.slice();
        showColourPicker(function (color) {
            if (!color) { clearRoutePreview(); disableRouteMode(); return; }
            fetch("/api/routes", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name: name, waypoints: savedPoints, color: color }),
            }).then(function (r) { return r.json(); })
              .then(function (route) {
                  addRouteLayer(route);
                  clearRoutePreview();
                  disableRouteMode();
              });
        }, "#00ff00");
    }

    function addRouteLayer(route) {
        if (routeLayers[route.id]) {
            routeLayer.removeLayer(routeLayers[route.id]);
        }
        var group = L.layerGroup();
        var coords = route.waypoints.map(function (wp) { return [wp.lat, wp.lon]; });

        // Polyline
        var line = L.polyline(coords, { color: route.color, weight: 3 }).addTo(group);

        // Numbered markers
        route.waypoints.forEach(function (wp, i) {
            L.marker([wp.lat, wp.lon], {
                icon: L.divIcon({
                    className: "route-number-icon",
                    html: String(i + 1),
                    iconSize: [22, 22],
                    iconAnchor: [11, 11],
                }),
            }).addTo(group);
        });

        // Calculate distances
        var totalDist = 0;
        var legInfo = [];
        for (var i = 1; i < route.waypoints.length; i++) {
            var w0 = route.waypoints[i - 1], w1 = route.waypoints[i];
            var d = haversineDistance(w0.lat, w0.lon, w1.lat, w1.lon);
            totalDist += d;
            legInfo.push({ leg: i, dist: d, cumDist: totalDist });
        }

        var marchRates = { "Dismounted (4 km/h)": 4, "Mounted 15 km/h": 15, "Mounted 30 km/h": 30, "Mounted 60 km/h": 60 };
        var defaultRate = 4;
        var timeHrs = totalDist / 1000 / defaultRate;
        var timeMins = Math.round(timeHrs * 60);

        var popupHtml = "<strong>" + escapeHtml(route.name) + "</strong>";
        popupHtml += "<br>Total: " + (totalDist / 1000).toFixed(2) + " km";
        popupHtml += "<br>Est time: " + timeMins + " min (dismounted)";
        popupHtml += "<br><small>";
        legInfo.forEach(function (leg) {
            popupHtml += "Leg " + leg.leg + ": " + Math.round(leg.dist) + "m (cum: " + (leg.cumDist / 1000).toFixed(2) + "km)<br>";
        });
        popupHtml += "</small>";
        popupHtml += '<br><button onclick="window._deleteRoute(\'' + route.id + '\')" style="padding:2px 8px;background:#f44336;color:#fff;border:none;border-radius:3px;cursor:pointer;">Delete</button>';
        line.bindPopup(popupHtml);

        group.addTo(routeLayer);
        routeLayers[route.id] = group;
    }

    window._deleteRoute = function (id) {
        fetch("/api/routes/" + id, { method: "DELETE" });
        if (routeLayers[id]) {
            routeLayer.removeLayer(routeLayers[id]);
            delete routeLayers[id];
        }
    };

    function loadRoutes() {
        fetch("/api/routes")
            .then(function (r) { return r.json(); })
            .then(function (routes) {
                routes.forEach(function (route) { addRouteLayer(route); });
            });
    }

    // ===== CONTROL MEASURES (PHASE LINES, BOUNDARIES) =====

    var CM_DEFAULTS = {
        phase_line: { color: "#ffff00", style: "solid", weight: 3 },
        boundary: { color: "#000000", style: "dashed", weight: 2 },
        feba: { color: "#ff0000", style: "solid", weight: 4 },
        lod: { color: "#ff9800", style: "dashed", weight: 3 },
        fup: { color: "#4caf50", style: "solid", weight: 3 },
        start_line: { color: "#00bcd4", style: "solid", weight: 3 },
        axis_of_advance: { color: "#2196F3", style: "solid", weight: 3 },
    };

    // Control measure options state
    var controlOptions = { type: "phase_line", lineStyle: "solid", color: "#ffff00" };
    var controlOptionsPanel = null;

    function createControlOptionsPanel() {
        if (controlOptionsPanel) return;
        controlOptionsPanel = document.createElement("div");
        controlOptionsPanel.className = "control-options-panel hidden";
        controlOptionsPanel.innerHTML =
            '<div class="control-options-title">Control Measure Options</div>' +
            '<div class="control-options-row"><label>Type:</label>' +
            '<select id="cm-type-select">' +
            '<option value="phase_line">Phase Line</option>' +
            '<option value="boundary">Boundary</option>' +
            '<option value="feba">FEBA</option>' +
            '<option value="lod">LOD</option>' +
            '<option value="fup">FUP</option>' +
            '<option value="start_line">Start Line</option>' +
            '<option value="axis_of_advance">Axis of Advance</option>' +
            '</select></div>' +
            '<div class="control-options-row"><label>Line style:</label>' +
            '<select id="cm-style-select">' +
            '<option value="solid">Solid</option>' +
            '<option value="dashed">Dashed</option>' +
            '<option value="dotted">Dotted</option>' +
            '</select></div>' +
            '<div class="control-options-row"><label>Colour:</label>' +
            '<div class="control-options-colour">' +
            '<div id="cm-colour-preview" class="control-colour-preview" style="background:#ffff00" title="Click to change"></div>' +
            '<span id="cm-colour-hex" style="font-size:11px;color:#aaa">#ffff00</span>' +
            '</div></div>' +
            '<div style="font-size:10px;color:#666;margin-top:4px">Draw the polyline on the map, then enter a name.</div>';
        document.body.appendChild(controlOptionsPanel);

        document.getElementById("cm-type-select").addEventListener("change", function () {
            controlOptions.type = this.value;
            var defaults = CM_DEFAULTS[this.value] || CM_DEFAULTS.phase_line;
            controlOptions.color = defaults.color;
            controlOptions.lineStyle = defaults.style;
            document.getElementById("cm-style-select").value = defaults.style;
            document.getElementById("cm-colour-preview").style.background = defaults.color;
            document.getElementById("cm-colour-hex").textContent = defaults.color;
        });

        document.getElementById("cm-style-select").addEventListener("change", function () {
            controlOptions.lineStyle = this.value;
        });

        document.getElementById("cm-colour-preview").addEventListener("click", function () {
            showColourPicker(function (colour) {
                if (!colour) return;
                controlOptions.color = colour;
                document.getElementById("cm-colour-preview").style.background = colour;
                document.getElementById("cm-colour-hex").textContent = colour;
            }, controlOptions.color);
        });
    }

    function toggleControlMode() {
        controlMode = !controlMode;
        controlBtn.classList.toggle("active", controlMode);
        document.body.classList.toggle("control-mode", controlMode);

        if (controlMode) {
            disableMeasureMode();
            disableWaypointMode();
            disableRouteMode();
            disableFireMissionMode();
            disableAnnotateMode();
            if (geofenceDrawMode) {
                geofenceDrawMode = false;
                geofenceBtn.classList.remove("active");
                if (geofenceDrawControl) map.removeControl(geofenceDrawControl);
            }

            createControlOptionsPanel();
            controlOptionsPanel.classList.remove("hidden");

            if (!controlDrawControl) {
                controlDrawControl = new L.Control.Draw({
                    position: "topleft",
                    draw: {
                        polyline: { shapeOptions: { color: "#ffff00" } },
                        polygon: false, rectangle: false, marker: false, circlemarker: false, circle: false,
                    },
                    edit: false,
                });
            }
            map.addControl(controlDrawControl);
            showToast("Set options above, then draw a polyline", "info");
        } else {
            if (controlDrawControl) map.removeControl(controlDrawControl);
            if (controlOptionsPanel) controlOptionsPanel.classList.add("hidden");
        }
    }

    function disableControlMode() {
        if (controlMode) {
            controlMode = false;
            controlBtn.classList.remove("active");
            document.body.classList.remove("control-mode");
            if (controlDrawControl) map.removeControl(controlDrawControl);
            if (controlOptionsPanel) controlOptionsPanel.classList.add("hidden");
        }
    }

    // Hook into Leaflet.draw for control measures
    map.on(L.Draw.Event.CREATED, function (e) {
        if (!controlMode) return;
        var layer = e.layer;
        var layerType = e.layerType;
        if (layerType !== "polyline") return;

        var name = prompt("Control measure name (e.g., PL ALPHA):");
        if (!name) return;

        var coords = layer.getLatLngs().map(function (ll) { return [ll.lat, ll.lng]; });

        fetch("/api/control-measures", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                name: name,
                measure_type: controlOptions.type,
                coordinates: coords,
                color: controlOptions.color,
                line_style: controlOptions.lineStyle,
            }),
        }).then(function (r) { return r.json(); })
          .then(function (cm) {
              addControlMeasureLayer(cm);
          });
    });

    function addControlMeasureLayer(cm) {
        if (controlMeasureLayers[cm.id]) {
            controlMeasureLayer.removeLayer(controlMeasureLayers[cm.id]);
        }
        var defaults = CM_DEFAULTS[cm.measure_type] || CM_DEFAULTS.phase_line;
        var dashArray = null;
        if (cm.line_style === "dashed") dashArray = "12, 8";
        else if (cm.line_style === "dotted") dashArray = "4, 6";

        var group = L.layerGroup();
        var line = L.polyline(cm.coordinates, {
            color: cm.color || defaults.color,
            weight: defaults.weight,
            dashArray: dashArray,
        }).addTo(group);

        // Label at midpoint
        var midIdx = Math.floor(cm.coordinates.length / 2);
        var midCoord = cm.coordinates[midIdx];
        L.marker(midCoord, {
            icon: L.divIcon({
                className: "control-measure-label",
                html: escapeHtml(cm.name),
                iconSize: null,
            }),
            interactive: false,
        }).addTo(group);

        var popupHtml = "<strong>" + escapeHtml(cm.name) + "</strong>";
        popupHtml += "<br>Type: " + escapeHtml(cm.measure_type);
        popupHtml += '<br><button onclick="window._deleteControlMeasure(\'' + cm.id + '\')" style="padding:2px 8px;background:#f44336;color:#fff;border:none;border-radius:3px;cursor:pointer;">Delete</button>';
        line.bindPopup(popupHtml);

        group.addTo(controlMeasureLayer);
        controlMeasureLayers[cm.id] = group;
    }

    window._deleteControlMeasure = function (id) {
        fetch("/api/control-measures/" + id, { method: "DELETE" });
        if (controlMeasureLayers[id]) {
            controlMeasureLayer.removeLayer(controlMeasureLayers[id]);
            delete controlMeasureLayers[id];
        }
    };

    function loadControlMeasures() {
        fetch("/api/control-measures")
            .then(function (r) { return r.json(); })
            .then(function (cms) {
                cms.forEach(function (cm) { addControlMeasureLayer(cm); });
            });
    }

    // ===== FIRE MISSION =====

    function toggleFireMissionMode() {
        var open = BFT.toggleFireMission();
        fireMissionBtn.classList.toggle("active", open);
        fireMissionMode = open;
        document.body.classList.toggle("firemission-mode", open);
        if (open) {
            disableMeasureMode();
            disableWaypointMode();
            disableRouteMode();
        }
    }

    function disableFireMissionMode() {
        if (fireMissionMode) {
            BFT.toggleFireMission();
            fireMissionBtn.classList.remove("active");
            fireMissionMode = false;
            document.body.classList.remove("firemission-mode");
        }
        fireMissionLayer.clearLayers();
    }

    // Callback so the fire mission panel's X button goes through toggleFireMissionMode
    BFT._onFireMissionClose = function () {
        toggleFireMissionMode();
        fireMissionLayer.clearLayers();
    };

    // ===== CANNED MESSAGE PALETTE =====

    var CANNED_MSGS = [
        // VP Acknowledgements
        "ROGER", "ROGER SO FAR", "WILCO", "ACK",
        // VP Procedural
        "SAY AGAIN", "SAY AGAIN ALL AFTER", "WAIT", "WAIT OUT",
        "NOTHING HEARD", "RADIO CHECK", "LOUD AND CLEAR", "SEND",
        // Contact / Tactical
        "CONTACT - WAIT OUT", "CONTACT - FIGURES", "SHOTS FIRED - WAIT OUT",
        // Reports / Requests
        "SITREP FOLLOWS", "CASEVAC REQ", "AMMO STATE RED", "AMMO STATE AMBER",
        // Status
        "MOVING NOW", "IN POSITION", "SET", "COMPLETE",
        "STAND TO", "STAND DOWN", "ALL CLEAR",
        // End
        "OUT", "OVER",
    ];
    var quickMsgVisible = false;

    function toggleQuickMsg() {
        quickMsgVisible = !quickMsgVisible;
        quickMsgBtn.classList.toggle("active", quickMsgVisible);
        if (quickMsgVisible) {
            if (chatOpen) toggleChat();
            renderQuickMsgPanel();
            quickMsgPanel.classList.remove("hidden");
        } else {
            quickMsgPanel.classList.add("hidden");
        }
    }

    function renderQuickMsgPanel() {
        // Channel selector
        var channelOpts = '<option value="BROADCAST">BROADCAST</option>';
        knownCallsigns.forEach(function (cs) {
            channelOpts += '<option value="' + escapeHtml(cs) + '">' + escapeHtml(cs) + '</option>';
        });
        var html = '<select id="quickmsg-channel" class="quickmsg-channel">' + channelOpts + '</select>';

        CANNED_MSGS.forEach(function (msg, i) {
            html += '<button class="quickmsg-btn" data-idx="' + i + '">' + escapeHtml(msg) + '</button>';
        });
        quickMsgPanel.innerHTML = html;

        var qmChannel = document.getElementById("quickmsg-channel");
        quickMsgPanel.querySelectorAll(".quickmsg-btn").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var text = CANNED_MSGS[parseInt(btn.dataset.idx, 10)];
                var channel = qmChannel.value;
                sendMessageText(channel, text);
                showToast("Sent to " + channel + ": " + text, "info");
            });
            btn.addEventListener("contextmenu", function (e) {
                e.preventDefault();
                var idx = parseInt(btn.dataset.idx, 10);
                var newText = prompt("Edit quick message:", CANNED_MSGS[idx]);
                if (newText) {
                    CANNED_MSGS[idx] = newText;
                    renderQuickMsgPanel();
                }
            });
        });
    }

    // ===== WEATHER WIDGET =====

    function toggleWeather() {
        weatherVisible = !weatherVisible;
        wxBtn.classList.toggle("active", weatherVisible);
        if (weatherVisible) {
            weatherWidget.classList.remove("hidden");
            fetchWeather();
            weatherTimer = setInterval(fetchWeather, 1800000); // 30 min
        } else {
            weatherWidget.classList.add("hidden");
            if (weatherTimer) { clearInterval(weatherTimer); weatherTimer = null; }
        }
    }

    function fetchWeather() {
        var center = map.getCenter();
        fetch("/api/weather?lat=" + center.lat + "&lon=" + center.lng)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.error) {
                    weatherWidget.innerHTML = '<div class="wx-title">Weather</div><div class="wx-error">' + escapeHtml(data.error) + '</div>';
                    return;
                }
                var windDir = data.wind_direction_deg || 0;
                var html = '<div class="wx-title">Weather</div>';
                html += '<div class="wx-row"><span>Temp:</span><span>' + (data.temperature_c != null ? data.temperature_c + ' \u00b0C' : 'N/A') + '</span></div>';
                html += '<div class="wx-row"><span>Wind:</span><span>' + (data.wind_speed_kmh || 0) + ' km/h <span class="wx-wind-arrow" style="transform:rotate(' + windDir + 'deg)">&darr;</span></span></div>';
                html += '<div class="wx-row"><span>Precip:</span><span>' + (data.precip_probability_pct != null ? data.precip_probability_pct + '%' : 'N/A') + '</span></div>';
                html += '<div class="wx-row"><span>Vis:</span><span>' + (data.visibility_m != null ? (data.visibility_m / 1000).toFixed(1) + ' km' : 'N/A') + '</span></div>';
                weatherWidget.innerHTML = html;
            })
            .catch(function () {
                weatherWidget.innerHTML = '<div class="wx-title">Weather</div><div class="wx-error">Failed to fetch weather data</div>';
            });
    }

    // ===== REPORTS PANEL =====

    function toggleReports() {
        var willOpen = !reportsBtn.classList.contains("active");
        if (willOpen) closeAllSidePanels("reports");
        var open = BFT.toggleReportPanel();
        reportsBtn.classList.toggle("active", open);
        document.body.classList.toggle("report-open", open);
        setTimeout(function () { map.invalidateSize(); }, 300);
    }

    // Callback so the report panel's X button goes through toggleReports
    BFT._onReportClose = function () { toggleReports(); };

    // Open reports panel programmatically (for toast click-through)
    BFT._openReports = function () {
        if (!reportsBtn.classList.contains("active")) toggleReports();
    };

    // ===== ADVISOR PANEL =====

    function toggleAdvisor() {
        var willOpen = !advisorBtn.classList.contains("active");
        if (willOpen) closeAllSidePanels("advisor");
        var open = BFT.toggleAdvisorPanel();
        advisorBtn.classList.toggle("active", open);
        document.body.classList.toggle("advisor-open", open);
        setTimeout(function () { map.invalidateSize(); }, 300);
    }

    BFT._onAdvisorClose = function () { toggleAdvisor(); };

    // ===== MESH TOPOLOGY =====

    function toggleMeshTopology() {
        var visible = BFT.toggleTopology();
        meshBtn.classList.toggle("active", visible);
    }

    // ===== TILE CACHE SEEDING =====

    function seedTileCache() {
        if (!("serviceWorker" in navigator) || !navigator.serviceWorker.controller) {
            showToast("Service worker not ready", "warning");
            return;
        }

        var bounds = map.getBounds();
        var currentZoom = map.getZoom();
        var minZoom = Math.max(1, currentZoom - 2);
        var maxZoom = Math.min(19, currentZoom + 2);

        var urls = [];
        // Prioritise OpenTopoMap
        for (var z = minZoom; z <= Math.min(maxZoom, 17); z++) {
            var tileRange = getTileRange(bounds, z);
            for (var x = tileRange.minX; x <= tileRange.maxX; x++) {
                for (var y = tileRange.minY; y <= tileRange.maxY; y++) {
                    var subdomains = ["a", "b", "c"];
                    var s = subdomains[(x + y) % subdomains.length];
                    urls.push("https://" + s + ".tile.opentopomap.org/" + z + "/" + x + "/" + y + ".png");
                }
            }
        }

        if (urls.length === 0) {
            showToast("No tiles to cache", "info");
            return;
        }

        showToast("Caching " + urls.length + " tiles...", "info");
        navigator.serviceWorker.controller.postMessage({ type: "SEED_TILES", urls: urls });
    }

    function getTileRange(bounds, zoom) {
        var min = latLonToTile(bounds.getSouthWest().lat, bounds.getSouthWest().lng, zoom);
        var max = latLonToTile(bounds.getNorthEast().lat, bounds.getNorthEast().lng, zoom);
        return {
            minX: Math.min(min.x, max.x),
            maxX: Math.max(min.x, max.x),
            minY: Math.min(min.y, max.y),
            maxY: Math.max(min.y, max.y),
        };
    }

    function latLonToTile(lat, lon, zoom) {
        var n = Math.pow(2, zoom);
        var x = Math.floor((lon + 180) / 360 * n);
        var latRad = lat * Math.PI / 180;
        var y = Math.floor((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2 * n);
        return { x: x, y: y };
    }

    // Listen for SW progress messages
    if ("serviceWorker" in navigator) {
        navigator.serviceWorker.addEventListener("message", function (event) {
            if (event.data.type === "SEED_PROGRESS") {
                showToast("Caching tiles... " + event.data.done + "/" + event.data.total, "info");
            } else if (event.data.type === "SEED_COMPLETE") {
                showToast("Tile caching complete: " + event.data.total + " tiles", "info");
            }
        });
    }

    // ===== OFFLINE SUPPORT =====

    window.addEventListener("online", function () {
        setConnected(ws && ws.readyState === WebSocket.OPEN);
        removeOfflineBanner();
        flushOfflineQueue();
    });

    window.addEventListener("offline", function () {
        setConnected(false);
        showOfflineBanner();
    });

    var offlineBannerEl = null;

    function showOfflineBanner() {
        if (offlineBannerEl) return;
        offlineBannerEl = document.createElement("div");
        offlineBannerEl.className = "offline-banner";
        offlineBannerEl.textContent = "OFFLINE — cached data";
        document.body.appendChild(offlineBannerEl);
    }

    function removeOfflineBanner() {
        if (offlineBannerEl) {
            offlineBannerEl.remove();
            offlineBannerEl = null;
        }
    }

    function flushOfflineQueue() {
        while (offlineQueue.length > 0) {
            var item = offlineQueue.shift();
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify(item));
            }
        }
    }

    // --- Side panel mutual exclusivity ---
    function closeAllSidePanels(except) {
        if (except !== "chat" && chatOpen) toggleChat();
        if (except !== "reports" && reportsBtn.classList.contains("active")) toggleReports();
        if (except !== "advisor" && advisorBtn.classList.contains("active")) toggleAdvisor();
    }

    // --- Chat panel ---
    function registerCallsign(cs) {
        if (knownCallsigns.has(cs)) return;
        knownCallsigns.add(cs);
        var opt = document.createElement("option");
        opt.value = "DM:" + cs;
        opt.textContent = "DM: " + cs;
        channelSelect.appendChild(opt);
    }

    function toggleChat() {
        chatOpen = !chatOpen;
        if (chatOpen) {
            closeAllSidePanels("chat");
            chatPanel.classList.remove("hidden");
            document.body.classList.add("chat-open");
            unreadCount = 0;
            updateUnreadBadge();
            if (!historyLoaded) loadHistory();
        } else {
            chatPanel.classList.add("hidden");
            document.body.classList.remove("chat-open");
        }
        setTimeout(function () { map.invalidateSize(); }, 300);
    }

    function updateUnreadBadge() {
        if (unreadCount > 0) {
            unreadBadge.textContent = unreadCount > 99 ? "99+" : unreadCount;
            unreadBadge.classList.remove("hidden");
        } else {
            unreadBadge.classList.add("hidden");
        }
    }

    function loadHistory() {
        fetch("/api/messages")
            .then(function (res) { return res.json(); })
            .then(function (msgs) {
                var existingIds = new Set(allMessages.map(function (m) { return m.id; }));
                msgs.forEach(function (m) {
                    if (!existingIds.has(m.id)) {
                        allMessages.push(m);
                        registerCallsign(m.sender);
                    }
                });
                historyLoaded = true;
                renderMessages();
            })
            .catch(function (err) {
                console.error("Failed to load message history:", err);
            });
    }

    function addMessage(msg) {
        // Check for ACK update on existing message
        if (msg.acked) {
            for (var i = allMessages.length - 1; i >= 0; i--) {
                if (allMessages[i].id === msg.id) {
                    allMessages[i] = msg;
                    if (chatOpen) renderMessages();
                    return;
                }
            }
        }

        for (var j = allMessages.length - 1; j >= Math.max(0, allMessages.length - 50); j--) {
            if (allMessages[j].id === msg.id) return;
        }
        allMessages.push(msg);
        registerCallsign(msg.sender);

        if (chatOpen) {
            renderMessages();
        } else {
            unreadCount++;
            updateUnreadBadge();
            var preview = msg.body.length > 60 ? msg.body.substring(0, 60) + "…" : msg.body;
            showToast(msg.sender + ": " + preview, "info", function () {
                if (!chatOpen) toggleChat();
            });
        }
    }

    function messageMatchesChannel(msg, ch) {
        if (ch === "ALL") return true;
        if (ch === "BROADCAST") return msg.channel === "BROADCAST";
        if (ch === "HQ") return msg.channel === "HQ";
        if (ch.indexOf("DM:") === 0) {
            var callsign = ch.substring(3);
            return msg.channel === callsign || (msg.sender === callsign && msg.channel === "HQ");
        }
        return false;
    }

    function renderMessages() {
        var filtered = allMessages.filter(function (m) {
            return messageMatchesChannel(m, currentChannel);
        });

        var html = "";
        filtered.forEach(function (m) {
            var senderClass = m.sender === "HQ" ? "hq" : "node";
            var channelLabel = "";
            if (currentChannel === "ALL") {
                channelLabel = '<span class="msg-channel">[' + escapeHtml(m.channel) + ']</span>';
            }

            // ACK indicators
            var ackHtml = "";
            if (m.requires_ack) {
                if (m.acked) {
                    ackHtml = '<span class="msg-ack-icon acked" title="Acknowledged by ' + escapeHtml(m.acked_by || '') + '">&#10004;</span>';
                } else {
                    ackHtml = '<span class="msg-ack-icon pending" title="Awaiting acknowledgement">&#9201;</span>';
                }
            }

            // ACK button for messages from others requiring ACK
            var ackBtn = "";
            if (m.requires_ack && !m.acked && m.sender !== "HQ") {
                ackBtn = '<button class="msg-ack-btn" data-msg-id="' + m.id + '">ACK</button>';
            }

            html += '<div class="msg-bubble">'
                + '<span class="msg-time">' + formatTime(m.timestamp) + '</span>'
                + '<span class="msg-sender ' + senderClass + '">' + escapeHtml(m.sender) + '</span>'
                + channelLabel
                + ackHtml
                + escapeHtml(m.body)
                + ackBtn
                + '</div>';
        });

        chatMessagesEl.innerHTML = html;
        chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;

        // Attach ACK button handlers
        chatMessagesEl.querySelectorAll(".msg-ack-btn").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var msgId = btn.dataset.msgId;
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "message_ack", id: msgId, by: "HQ" }));
                }
            });
        });
    }

    function sendMessage() {
        var body = chatInput.value.trim();
        if (!body) return;

        var channel = currentChannel;
        if (channel === "ALL") channel = "BROADCAST";
        if (channel.indexOf("DM:") === 0) channel = channel.substring(3);

        var requiresAck = chatReqAck.checked;
        sendMessageText(channel, body, requiresAck);
        chatInput.value = "";
        chatReqAck.checked = false;
    }

    function sendMessageText(channel, body, requiresAck) {
        var payload = { type: "message", channel: channel, body: body };
        if (requiresAck) payload.requires_ack = true;

        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify(payload));
        } else {
            offlineQueue.push(payload);
        }
    }

    // Expose for modules
    BFT._sendMessage = sendMessageText;
    BFT._getPositions = function () { return positions; };
    BFT._setFireMissionClickTarget = function (mode) { fireMissionClickTarget = mode; };
    BFT._fireMissionLayer = fireMissionLayer;

    // Right-click context menu for grid ref
    map.on("contextmenu", function (e) {
        var grid = BFT.formatCoord(e.latlng.lat, e.latlng.lng);
        var popupHtml = '<strong>Grid Reference</strong><br>';
        popupHtml += '<code>' + grid + '</code><br>';
        popupHtml += '<button onclick="navigator.clipboard.writeText(\'' + grid.replace(/'/g, "\\'") + '\');this.textContent=\'Copied!\'" style="margin-top:4px;padding:2px 8px;background:#2196F3;color:#fff;border:none;border-radius:3px;cursor:pointer;">Copy Grid</button>';
        popupHtml += ' <button onclick="window._markTRP(' + e.latlng.lat + ',' + e.latlng.lng + ')" style="margin-top:4px;padding:2px 8px;background:#ff9800;color:#fff;border:none;border-radius:3px;cursor:pointer;">Mark as TRP</button>';

        L.popup().setLatLng(e.latlng).setContent(popupHtml).openOn(map);
    });

    window._markTRP = function (lat, lon) {
        var grid = BFT.formatCoord(lat, lon);
        fetch("/api/waypoints", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                name: "TRP " + grid.substring(grid.length - 9),
                lat: lat, lon: lon,
                waypoint_type: "trp",
                icon: "🎯",
                description: "Target Reference Point: " + grid,
            }),
        });
        map.closePopup();
    };

    // --- Event listeners ---
    msgToggleBtn.addEventListener("click", toggleChat);
    chatSendBtn.addEventListener("click", sendMessage);
    chatInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter") sendMessage();
    });
    channelSelect.addEventListener("change", function () {
        currentChannel = channelSelect.value;
        renderMessages();
    });
    waypointBtn.addEventListener("click", toggleWaypointMode);
    trailsBtn.addEventListener("click", toggleTrails);
    healthBtn.addEventListener("click", toggleHealth);
    healthClose.addEventListener("click", toggleHealth);
    measureBtn.addEventListener("click", toggleMeasureMode);
    geofenceBtn.addEventListener("click", toggleGeofenceMode);
    annotateBtn.addEventListener("click", toggleAnnotateMode);
    routeBtn.addEventListener("click", toggleRouteMode);
    controlBtn.addEventListener("click", toggleControlMode);
    fireMissionBtn.addEventListener("click", toggleFireMissionMode);
    reportsBtn.addEventListener("click", toggleReports);
    quickMsgBtn.addEventListener("click", toggleQuickMsg);
    wxBtn.addEventListener("click", toggleWeather);
    meshBtn.addEventListener("click", toggleMeshTopology);
    advisorBtn.addEventListener("click", toggleAdvisor);
    recordBtn.addEventListener("click", toggleRecording);
    replayBtn.addEventListener("click", startReplay);

    // Replay controls
    replayPlayBtn.addEventListener("click", function () { replayPaused = false; runReplay(); });
    replayPauseBtn.addEventListener("click", function () { replayPaused = true; if (replayTimer) clearTimeout(replayTimer); });
    replayStopBtn.addEventListener("click", stopReplay);
    replayScrubber.addEventListener("input", function () {
        replayIndex = parseInt(replayScrubber.value, 10);
        replayTimeEl.textContent = (replayIndex + 1) + "/" + replayEvents.length;
    });

    // Map click handler
    map.on("click", function (e) {
        lastClickLatLon = { lat: e.latlng.lat, lon: e.latlng.lng };

        if (waypointPlaceMode) {
            handleWaypointPlacement(e);
        } else if (measureMode) {
            handleMeasureClick(e);
        } else if (routeMode) {
            handleRouteClick(e);
        } else if (fireMissionMode) {
            if (fireMissionClickTarget === "observer") {
                BFT.setFireMissionObserver(e.latlng.lat, e.latlng.lng);
            } else {
                BFT.setFireMissionTarget(e.latlng.lat, e.latlng.lng);
            }
        }
    });

    map.on("dblclick", function (e) {
        if (routeMode) {
            e.originalEvent.preventDefault();
            finishRoute();
        }
    });

    // --- Initialise modules ---
    BFT.initReports(reportPanel, null, BFT.formatCoord, lastClickLatLon);
    BFT.initFireMission(fireMissionPanel);
    BFT.initTopology(map);

    // --- WebSocket with auto-reconnect ---
    var ws = null;
    var reconnectDelay = 1000;
    var initialBatch = true;
    var batchTimer = null;

    function connect() {
        var proto = location.protocol === "https:" ? "wss:" : "ws:";
        ws = new WebSocket(proto + "//" + location.host + "/ws");

        ws.onopen = function () {
            setConnected(true);
            reconnectDelay = 1000;
            initialBatch = true;
            if (batchTimer) clearTimeout(batchTimer);
            batchTimer = setTimeout(function () {
                initialBatch = false;
                if (!hasFitBounds) { fitBoundsToAll(); hasFitBounds = true; }
            }, 500);

            // Load initial data via REST
            loadWaypoints();
            loadGeofences();
            loadAnnotations();
            loadRoutes();
            loadControlMeasures();
            if (trailsEnabled) loadAllTrails();

            // Check recording status
            fetch("/api/recording/status")
                .then(function (r) { return r.json(); })
                .then(function (status) {
                    isRecording = status.recording;
                    if (isRecording) {
                        recordBtn.classList.add("recording");
                        recordBtn.textContent = "Stop Rec";
                    }
                });

            // Flush offline queue
            flushOfflineQueue();
        };

        ws.onmessage = function (event) {
            try {
                var data = JSON.parse(event.data);

                if (data.type === "message") {
                    addMessage(data);
                    return;
                }
                if (data.type === "sos") {
                    handleSOSAlert(data);
                    return;
                }
                if (data.type === "geofence_alert") {
                    handleGeofenceAlert(data);
                    return;
                }
                if (data.type === "report") {
                    BFT.handleIncomingReport(data);
                    return;
                }
                // Route events
                if (data.action === "add" && data.route) {
                    addRouteLayer(data.route);
                    return;
                }
                if (data.action === "delete" && data.type === "route_delete") {
                    if (routeLayers[data.id]) {
                        routeLayer.removeLayer(routeLayers[data.id]);
                        delete routeLayers[data.id];
                    }
                    return;
                }
                // Control measure events
                if (data.action === "add" && data.control_measure) {
                    addControlMeasureLayer(data.control_measure);
                    return;
                }
                if (data.action === "delete" && data.type === "control_measure_delete") {
                    if (controlMeasureLayers[data.id]) {
                        controlMeasureLayer.removeLayer(controlMeasureLayers[data.id]);
                        delete controlMeasureLayers[data.id];
                    }
                    return;
                }
                // Waypoint events
                if (data.action === "add" && data.waypoint) {
                    addWaypointMarker(data.waypoint);
                    return;
                }
                if (data.action === "delete" && data.type === "waypoint_delete") {
                    removeWaypointMarker(data.id);
                    return;
                }
                // Geofence events
                if (data.action === "add" && data.geofence) {
                    addGeofenceLayer(data.geofence);
                    return;
                }
                if (data.action === "delete" && data.type === "geofence_delete") {
                    if (geofenceLayers[data.id]) {
                        geofenceLayer.removeLayer(geofenceLayers[data.id]);
                        delete geofenceLayers[data.id];
                    }
                    return;
                }
                if (data.action === "alert" && data.alert) {
                    handleGeofenceAlert(data.alert);
                    return;
                }

                // Default: position report
                if (data.type === "position" || data.callsign) {
                    upsertMarker(data);
                    if (initialBatch) {
                        clearTimeout(batchTimer);
                        batchTimer = setTimeout(function () {
                            initialBatch = false;
                            if (!hasFitBounds) { fitBoundsToAll(); hasFitBounds = true; }
                        }, 500);
                    }
                }
            } catch (e) {
                console.error("Bad message:", e);
            }
        };

        ws.onclose = function () {
            setConnected(false);
            setTimeout(function () {
                reconnectDelay = Math.min(reconnectDelay * 2, 30000);
                connect();
            }, reconnectDelay);
        };

        ws.onerror = function () {
            ws.close();
        };
    }

    connect();
})();
