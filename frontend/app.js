(function () {
    "use strict";

    const BLUE = "#2196F3";
    const GREY = "#9E9E9E";
    const MARKER_RADIUS = 8;

    // --- Map setup ---
    const map = L.map("map", {
        zoomControl: true,
    }).setView([51.5225, -0.0865], 15);

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
    }, null, { position: "topright" }).addTo(map);

    // --- State ---
    const markers = {};   // callsign -> L.circleMarker
    const labels = {};    // callsign -> L.marker (divIcon label)
    const positions = {}; // callsign -> latest report
    let hasFitBounds = false;

    // --- Message state ---
    const allMessages = [];
    let currentChannel = "ALL";
    let unreadCount = 0;
    let chatOpen = false;
    let historyLoaded = false;
    const knownCallsigns = new Set();

    // --- DOM refs ---
    const connStatusEl = document.getElementById("conn-status");
    const nodeCountEl = document.getElementById("node-count");
    const msgToggleBtn = document.getElementById("msg-toggle");
    const unreadBadge = document.getElementById("unread-badge");
    const chatPanel = document.getElementById("chat-panel");
    const channelSelect = document.getElementById("channel-select");
    const chatMessagesEl = document.getElementById("chat-messages");
    const chatInput = document.getElementById("chat-input");
    const chatSendBtn = document.getElementById("chat-send");

    // --- Helpers ---
    function escapeHtml(str) {
        var div = document.createElement("div");
        div.appendChild(document.createTextNode(str));
        return div.innerHTML;
    }

    function setConnected(connected) {
        connStatusEl.textContent = connected ? "CONNECTED" : "DISCONNECTED";
        connStatusEl.className = connected ? "connected" : "disconnected";
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

    // --- Popup content ---
    function popupContent(r) {
        var html = "<strong>" + escapeHtml(r.callsign) + "</strong>";
        if (r.stale) html += '<span class="stale-badge">STALE</span>';
        html += "<br>Last seen: " + relativeTime(r.timestamp);
        if (r.battery != null) html += "<br>Battery: " + r.battery.toFixed(0) + "%";
        if (r.altitude) html += "<br>Alt: " + r.altitude.toFixed(0) + " m";
        if (r.speed != null) html += "<br>Speed: " + r.speed.toFixed(1) + " km/h";
        if (r.heading != null) html += "<br>Heading: " + r.heading.toFixed(0) + "&deg;";
        html += "<br><small>" + r.lat.toFixed(6) + ", " + r.lon.toFixed(6) + "</small>";
        return html;
    }

    // --- Upsert marker ---
    function upsertMarker(report) {
        positions[report.callsign] = report;
        registerCallsign(report.callsign);
        var latlng = [report.lat, report.lon];
        var color = report.stale ? GREY : BLUE;

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

        updateNodeCount();
    }

    function fitBoundsToAll() {
        var coords = Object.values(positions).map(function (r) {
            return [r.lat, r.lon];
        });
        if (coords.length > 0) {
            map.fitBounds(coords, { padding: [40, 40], maxZoom: 16 });
        }
    }

    // --- Stale check timer ---
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
            chatPanel.classList.remove("hidden");
            document.body.classList.add("chat-open");
            unreadCount = 0;
            updateUnreadBadge();
            if (!historyLoaded) {
                loadHistory();
            }
        } else {
            chatPanel.classList.add("hidden");
            document.body.classList.remove("chat-open");
        }
        setTimeout(function () {
            map.invalidateSize();
        }, 300);
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
        // Avoid duplicates
        for (var i = allMessages.length - 1; i >= Math.max(0, allMessages.length - 50); i--) {
            if (allMessages[i].id === msg.id) return;
        }
        allMessages.push(msg);
        registerCallsign(msg.sender);

        if (chatOpen) {
            renderMessages();
        } else {
            unreadCount++;
            updateUnreadBadge();
        }
    }

    function messageMatchesChannel(msg, ch) {
        if (ch === "ALL") return true;
        if (ch === "BROADCAST") return msg.channel === "BROADCAST";
        if (ch === "HQ") return msg.channel === "HQ";
        // DM channel: "DM:CALLSIGN"
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
            html += '<div class="msg-bubble">'
                + '<span class="msg-time">' + formatTime(m.timestamp) + '</span>'
                + '<span class="msg-sender ' + senderClass + '">' + escapeHtml(m.sender) + '</span>'
                + channelLabel
                + escapeHtml(m.body)
                + '</div>';
        });

        chatMessagesEl.innerHTML = html;
        chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
    }

    function sendMessage() {
        var body = chatInput.value.trim();
        if (!body) return;

        var channel = currentChannel;
        if (channel === "ALL") channel = "BROADCAST";
        if (channel.indexOf("DM:") === 0) channel = channel.substring(3);

        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: "message",
                channel: channel,
                body: body,
            }));
        }
        chatInput.value = "";
    }

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
                if (!hasFitBounds) {
                    fitBoundsToAll();
                    hasFitBounds = true;
                }
            }, 500);
        };

        ws.onmessage = function (event) {
            try {
                var data = JSON.parse(event.data);
                if (data.type === "message") {
                    addMessage(data);
                    return;
                }
                // Default: position report
                upsertMarker(data);
                if (initialBatch) {
                    clearTimeout(batchTimer);
                    batchTimer = setTimeout(function () {
                        initialBatch = false;
                        if (!hasFitBounds) {
                            fitBoundsToAll();
                            hasFitBounds = true;
                        }
                    }, 500);
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
