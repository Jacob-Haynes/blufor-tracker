/**
 * Mesh Network Topology Overlay
 * Shows signal quality lines between nodes with SNR/RSSI colour coding.
 */
(function () {
    "use strict";

    var BFT = window.BFT = window.BFT || {};

    var topologyLayer = null;
    var topologyVisible = false;
    var topologyLinks = [];

    BFT.initTopology = function (map) {
        topologyLayer = L.layerGroup();
        BFT._topologyMap = map;
    };

    BFT.toggleTopology = function () {
        topologyVisible = !topologyVisible;
        if (topologyVisible) {
            topologyLayer.addTo(BFT._topologyMap);
            loadTopology();
        } else {
            BFT._topologyMap.removeLayer(topologyLayer);
        }
        return topologyVisible;
    };

    function loadTopology() {
        fetch("/api/mesh/topology")
            .then(function (r) { return r.json(); })
            .then(function (links) {
                topologyLinks = links;
                renderTopology();
            });
    }

    function renderTopology() {
        topologyLayer.clearLayers();

        topologyLinks.forEach(function (link) {
            var fromPos = BFT._getPosition ? BFT._getPosition(link.from_node) : null;
            var toPos = BFT._getPosition ? BFT._getPosition(link.to_node) : null;
            if (!fromPos || !toPos) return;

            var color = getSignalColor(link.snr, link.rssi);
            var weight = getSignalWeight(link.snr, link.rssi);

            var line = L.polyline(
                [[fromPos.lat, fromPos.lon], [toPos.lat, toPos.lon]],
                {
                    color: color,
                    weight: weight,
                    opacity: 0.7,
                    dashArray: link.hop_count && link.hop_count > 1 ? "8, 4" : null,
                }
            ).addTo(topologyLayer);

            var popupHtml = '<strong>Mesh Link</strong><br>';
            popupHtml += link.from_node + ' ↔ ' + link.to_node + '<br>';
            if (link.snr != null) popupHtml += 'SNR: ' + link.snr.toFixed(1) + ' dB<br>';
            if (link.rssi != null) popupHtml += 'RSSI: ' + link.rssi + ' dBm<br>';
            if (link.hop_count != null) popupHtml += 'Hops: ' + link.hop_count + '<br>';
            popupHtml += 'Last seen: ' + relativeTime(link.last_seen);
            line.bindPopup(popupHtml);
        });
    }

    function getSignalColor(snr, rssi) {
        // Green: SNR > 5 dB, RSSI > -90 dBm
        // Orange: SNR 0–5, RSSI -90 to -110
        // Red: SNR < 0, RSSI < -110
        if (snr != null) {
            if (snr > 5) return "#4caf50";
            if (snr >= 0) return "#ff9800";
            return "#f44336";
        }
        if (rssi != null) {
            if (rssi > -90) return "#4caf50";
            if (rssi >= -110) return "#ff9800";
            return "#f44336";
        }
        return "#888";
    }

    function getSignalWeight(snr, rssi) {
        if (snr != null) {
            if (snr > 5) return 3;
            if (snr >= 0) return 2;
            return 1;
        }
        return 2;
    }

    function relativeTime(ts) {
        var diff = Math.floor(Date.now() / 1000 - ts);
        if (diff < 5) return "just now";
        if (diff < 60) return diff + "s ago";
        if (diff < 3600) return Math.floor(diff / 60) + "m ago";
        return Math.floor(diff / 3600) + "h ago";
    }

    // Refresh topology periodically
    setInterval(function () {
        if (topologyVisible) loadTopology();
    }, 15000);

    // Expose for health panel signal column
    BFT.getTopologyLinks = function () {
        return topologyLinks;
    };

    BFT.getBestSignalForNode = function (callsign) {
        var best = null;
        topologyLinks.forEach(function (link) {
            if (link.from_node === callsign || link.to_node === callsign) {
                if (best === null || (link.snr != null && (best.snr == null || link.snr > best.snr))) {
                    best = link;
                }
            }
        });
        return best;
    };

})();
