/**
 * Indirect Fire Support — Fire Mission Calculator and Adjustment Panel
 * All bearings in NATO mils (6400 mils = 360°)
 */
(function () {
    "use strict";

    var BFT = window.BFT = window.BFT || {};

    var fireMissionPanel = null;
    var fireMissionOpen = false;
    var currentFireMission = null; // last sent fire mission for adjustments

    BFT.initFireMission = function (panelEl) {
        fireMissionPanel = panelEl;
    };

    BFT.toggleFireMission = function () {
        fireMissionOpen = !fireMissionOpen;
        if (fireMissionOpen) {
            renderFireMissionPanel();
            fireMissionPanel.classList.remove("hidden");
        } else {
            fireMissionPanel.classList.add("hidden");
        }
        return fireMissionOpen;
    };

    BFT.setFireMissionTarget = function (lat, lon) {
        var targetInput = document.getElementById("fm-target-grid");
        if (targetInput && fireMissionOpen) {
            targetInput.value = BFT.formatCoord(lat, lon, "mgrs");
        }
    };

    BFT.setFireMissionObserver = function (lat, lon) {
        var obsInput = document.getElementById("fm-observer-grid");
        if (obsInput && fireMissionOpen) {
            obsInput.value = BFT.formatCoord(lat, lon, "mgrs");
        }
    };

    function renderFireMissionPanel() {
        // Build observer node options
        var nodeOptions = '<option value="">-- Select node --</option>';
        if (BFT._getPositions) {
            var pos = BFT._getPositions();
            Object.keys(pos).sort().forEach(function (cs) {
                nodeOptions += '<option value="' + cs + '">' + cs + '</option>';
            });
        }

        var html = '<div class="fm-header">';
        html += '<strong>Fire Mission</strong>';
        html += '<button id="fm-close-btn" class="fm-close">&times;</button>';
        html += '</div>';
        html += '<div class="fm-body">';
        html += '<div class="fm-row"><label>Target Grid:</label><input type="text" id="fm-target-grid" class="fm-input" placeholder="Click map to set" /></div>';
        html += '<div class="fm-click-btns"><button id="fm-set-target" class="fm-click-btn active">Map click: Target</button><button id="fm-set-observer" class="fm-click-btn">Map click: Observer</button></div>';
        html += '<div class="fm-row"><label>Observer Grid:</label><input type="text" id="fm-observer-grid" class="fm-input" placeholder="Select node or click" /><select id="fm-node-select" class="fm-node-select">' + nodeOptions + '</select></div>';
        html += '<div class="fm-row"><label>Target Description:</label><input type="text" id="fm-target-desc" class="fm-input" placeholder="e.g. Enemy FP in treeline" /></div>';
        html += '<div class="fm-row"><label>Effect:</label><select id="fm-effect" class="fm-input">';
        html += '<option value="neutralise">Neutralise</option><option value="suppress">Suppress</option>';
        html += '<option value="destroy">Destroy</option><option value="smoke">Smoke</option><option value="illum">Illumination</option>';
        html += '</select></div>';
        html += '<div class="fm-row"><label>Rounds:</label><input type="number" id="fm-rounds" class="fm-input" value="3" min="1" /></div>';
        html += '<div class="fm-row"><button id="fm-calculate-btn" class="fm-btn">Calculate</button><button id="fm-send-btn" class="fm-btn fm-btn-send">Send Fire Mission</button></div>';
        html += '<div id="fm-result" class="fm-result hidden"></div>';
        html += '<div id="fm-adjust-section" class="fm-adjust hidden">';
        html += '<div class="fm-adjust-header">Adjustment</div>';
        html += '<div class="fm-row"><label>ADD/DROP (m):</label><input type="number" id="fm-add-drop" class="fm-input" value="0" /><small>+ve = ADD, -ve = DROP</small></div>';
        html += '<div class="fm-row"><label>LEFT/RIGHT (m):</label><input type="number" id="fm-left-right" class="fm-input" value="0" /><small>+ve = RIGHT, -ve = LEFT</small></div>';
        html += '<div class="fm-row"><label>Direction (mils):</label><input type="number" id="fm-adj-direction" class="fm-input" readonly /></div>';
        html += '<div class="fm-row"><button id="fm-send-adjust-btn" class="fm-btn fm-btn-send">Send Adjustment</button></div>';
        html += '<div id="fm-adj-result" class="fm-result hidden"></div>';
        html += '</div>';
        html += '</div>';

        fireMissionPanel.innerHTML = html;

        document.getElementById("fm-close-btn").addEventListener("click", function () {
            if (BFT._onFireMissionClose) BFT._onFireMissionClose();
            else BFT.toggleFireMission();
        });
        document.getElementById("fm-calculate-btn").addEventListener("click", calculateFireMission);
        document.getElementById("fm-send-btn").addEventListener("click", sendFireMission);
        document.getElementById("fm-send-adjust-btn").addEventListener("click", sendAdjustment);

        // Map click target/observer toggle
        var setTargetBtn = document.getElementById("fm-set-target");
        var setObserverBtn = document.getElementById("fm-set-observer");
        setTargetBtn.addEventListener("click", function () {
            setTargetBtn.classList.add("active");
            setObserverBtn.classList.remove("active");
            if (BFT._setFireMissionClickTarget) BFT._setFireMissionClickTarget("target");
        });
        setObserverBtn.addEventListener("click", function () {
            setObserverBtn.classList.add("active");
            setTargetBtn.classList.remove("active");
            if (BFT._setFireMissionClickTarget) BFT._setFireMissionClickTarget("observer");
        });

        // Node dropdown for observer
        var nodeSelect = document.getElementById("fm-node-select");
        nodeSelect.addEventListener("change", function () {
            var cs = nodeSelect.value;
            if (!cs || !BFT._getPositions) return;
            var pos = BFT._getPositions();
            var p = pos[cs];
            if (p) {
                var obsInput = document.getElementById("fm-observer-grid");
                obsInput.value = BFT.formatCoord(p.lat, p.lon, "mgrs");
            }
        });
    }

    function calculateFireMission() {
        var targetStr = document.getElementById("fm-target-grid").value.trim();
        var obsStr = document.getElementById("fm-observer-grid").value.trim();
        var resultDiv = document.getElementById("fm-result");

        if (!targetStr || !obsStr) {
            resultDiv.innerHTML = '<span class="fm-error">Target and Observer grids required</span>';
            resultDiv.classList.remove("hidden");
            return;
        }

        var target = BFT.mgrsToLatLon(targetStr);
        var observer = BFT.mgrsToLatLon(obsStr);

        if (!target || !observer) {
            // Try parsing as DD
            var tParts = targetStr.split(",").map(function (s) { return parseFloat(s.trim()); });
            var oParts = obsStr.split(",").map(function (s) { return parseFloat(s.trim()); });
            if (tParts.length === 2 && !isNaN(tParts[0])) target = { lat: tParts[0], lon: tParts[1] };
            if (oParts.length === 2 && !isNaN(oParts[0])) observer = { lat: oParts[0], lon: oParts[1] };
        }

        if (!target || !observer) {
            resultDiv.innerHTML = '<span class="fm-error">Could not parse grid references</span>';
            resultDiv.classList.remove("hidden");
            return;
        }

        var dist = haversineDistance(observer.lat, observer.lon, target.lat, target.lon);
        var bearingDeg = compassBearing(observer.lat, observer.lon, target.lat, target.lon);
        var bearingMils = Math.round(BFT.degreesToMils(bearingDeg));

        currentFireMission = {
            target: target,
            observer: observer,
            bearingDeg: bearingDeg,
            bearingMils: bearingMils,
            range: dist,
            targetGrid: targetStr,
            observerGrid: obsStr,
        };

        resultDiv.innerHTML =
            '<div class="fm-result-line"><strong>Bearing:</strong> ' + bearingMils + ' mils (' + bearingDeg.toFixed(1) + '&deg;)</div>' +
            '<div class="fm-result-line"><strong>Range:</strong> ' + Math.round(dist) + ' m</div>' +
            '<div class="fm-result-line"><strong>Target Grid:</strong> ' + targetStr + '</div>';
        resultDiv.classList.remove("hidden");

        // Show adjust section
        var adjustSection = document.getElementById("fm-adjust-section");
        adjustSection.classList.remove("hidden");
        document.getElementById("fm-adj-direction").value = bearingMils;
    }

    function sendFireMission() {
        if (!currentFireMission) {
            calculateFireMission();
            if (!currentFireMission) return;
        }

        var effect = document.getElementById("fm-effect").value;
        var rounds = document.getElementById("fm-rounds").value;
        var desc = document.getElementById("fm-target-desc").value;

        var text = "FIRE MISSION|" +
            "TGT:" + currentFireMission.targetGrid + "|" +
            "OBS:" + currentFireMission.observerGrid + "|" +
            "DIR:" + currentFireMission.bearingMils + " mils|" +
            "RNG:" + Math.round(currentFireMission.range) + "m|" +
            "DESC:" + desc + "|" +
            "EFFECT:" + effect.toUpperCase() + "|" +
            "RNDS:" + rounds;

        // Send as message
        if (BFT._sendMessage) {
            BFT._sendMessage("BROADCAST", text);
        }
        BFT._showToast("Fire mission sent: " + currentFireMission.targetGrid, "warning");
    }

    function sendAdjustment() {
        if (!currentFireMission) return;

        var addDrop = parseFloat(document.getElementById("fm-add-drop").value) || 0;
        var leftRight = parseFloat(document.getElementById("fm-left-right").value) || 0;
        var dirMils = parseFloat(document.getElementById("fm-adj-direction").value) || currentFireMission.bearingMils;
        var dirRad = BFT.milsToDegrees(dirMils) * Math.PI / 180;

        // Calculate new target position
        // ADD = further along direction, DROP = closer
        // RIGHT = perpendicular clockwise, LEFT = perpendicular counter-clockwise
        var targetLat = currentFireMission.target.lat;
        var targetLon = currentFireMission.target.lon;

        // Add/drop along bearing direction
        var dlat = addDrop * Math.cos(dirRad) / 111320;
        var dlon = addDrop * Math.sin(dirRad) / (111320 * Math.cos(targetLat * Math.PI / 180));
        targetLat += dlat;
        targetLon += dlon;

        // Left/right perpendicular to bearing
        var perpRad = dirRad + Math.PI / 2; // right is +90°
        dlat = leftRight * Math.cos(perpRad) / 111320;
        dlon = leftRight * Math.sin(perpRad) / (111320 * Math.cos(targetLat * Math.PI / 180));
        targetLat += dlat;
        targetLon += dlon;

        var newGrid = BFT.latLonToMGRS(targetLat, targetLon);
        var adjResultDiv = document.getElementById("fm-adj-result");

        var adjustText = "";
        if (addDrop > 0) adjustText += "ADD " + addDrop + " ";
        else if (addDrop < 0) adjustText += "DROP " + Math.abs(addDrop) + " ";
        if (leftRight > 0) adjustText += "RIGHT " + leftRight;
        else if (leftRight < 0) adjustText += "LEFT " + Math.abs(leftRight);

        adjResultDiv.innerHTML =
            '<div class="fm-result-line"><strong>Adjustment:</strong> ' + adjustText + '</div>' +
            '<div class="fm-result-line"><strong>New Grid:</strong> ' + newGrid + '</div>';
        adjResultDiv.classList.remove("hidden");

        // Update current fire mission target
        currentFireMission.target = { lat: targetLat, lon: targetLon };
        currentFireMission.targetGrid = newGrid;

        // Send as message
        var msgText = "ADJUST FIRE|" + adjustText.trim() + "|NEW TGT:" + newGrid;
        if (BFT._sendMessage) {
            BFT._sendMessage("BROADCAST", msgText);
        }
        BFT._showToast("Fire adjustment sent: " + adjustText, "warning");
    }

    // Haversine helpers (duplicate to avoid cross-file dependency issues)
    function haversineDistance(lat1, lon1, lat2, lon2) {
        var R = 6371000;
        var p1 = lat1 * Math.PI / 180, p2 = lat2 * Math.PI / 180;
        var dp = (lat2 - lat1) * Math.PI / 180, dl = (lon2 - lon1) * Math.PI / 180;
        var a = Math.sin(dp / 2) * Math.sin(dp / 2) + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) * Math.sin(dl / 2);
        return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    }

    function compassBearing(lat1, lon1, lat2, lon2) {
        var p1 = lat1 * Math.PI / 180, p2 = lat2 * Math.PI / 180;
        var dl = (lon2 - lon1) * Math.PI / 180;
        var y = Math.sin(dl) * Math.cos(p2);
        var x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
        return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
    }

})();
