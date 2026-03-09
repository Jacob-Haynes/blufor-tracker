/**
 * Coordinate conversion utilities for Blue Force Tracker
 * Supports MGRS, BNG (British National Grid), DMS, Decimal Degrees
 * All bearings in fire support use NATO mils (6400 mils = 360°)
 */
(function () {
    "use strict";

    var BFT = window.BFT = window.BFT || {};

    // --- MGRS conversions (using mgrs.js CDN) ---
    BFT.latLonToMGRS = function (lat, lon) {
        try {
            if (typeof mgrs !== "undefined") {
                var raw = mgrs.forward([lon, lat], 5);
                // Format as "30U WB 12345 67890"
                var zone = raw.substring(0, 3).trim();
                var letters = raw.substring(3, 5);
                var digits = raw.substring(5);
                var half = digits.length / 2;
                var easting = digits.substring(0, half);
                var northing = digits.substring(half);
                return zone + " " + letters + " " + easting + " " + northing;
            }
        } catch (e) {
            console.warn("MGRS conversion failed:", e);
        }
        return lat.toFixed(5) + ", " + lon.toFixed(5);
    };

    BFT.mgrsToLatLon = function (mgrsStr) {
        try {
            if (typeof mgrs !== "undefined") {
                var clean = mgrsStr.replace(/\s+/g, "");
                var point = mgrs.toPoint(clean);
                return { lat: point[1], lon: point[0] };
            }
        } catch (e) {
            console.warn("MGRS parse failed:", e);
        }
        return null;
    };

    // --- BNG conversions (using proj4js CDN) ---
    var _proj4Ready = false;
    function ensureProj4() {
        if (_proj4Ready || typeof proj4 === "undefined") return;
        proj4.defs("EPSG:27700", "+proj=tmerc +lat_0=49 +lon_0=-2 +k=0.9996012717 +x_0=400000 +y_0=-100000 +ellps=airy +towgs84=446.448,-125.157,542.060,0.1502,0.2470,0.8421,-20.4894 +units=m +no_defs");
        _proj4Ready = true;
    }

    var BNG_LETTERS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"; // no I
    function eastNorthToGridRef(easting, northing) {
        var e100 = Math.floor(easting / 500000);
        var n100 = Math.floor(northing / 500000);
        var firstIdx = (4 - n100) * 5 + e100;
        if (firstIdx < 0 || firstIdx >= 25) return null;
        var firstLetter = BNG_LETTERS[firstIdx];

        var e10 = Math.floor((easting % 500000) / 100000);
        var n10 = Math.floor((northing % 500000) / 100000);
        var secondIdx = (4 - n10) * 5 + e10;
        if (secondIdx < 0 || secondIdx >= 25) return null;
        var secondLetter = BNG_LETTERS[secondIdx];

        var eStr = String(Math.floor(easting % 100000)).padStart(5, "0");
        var nStr = String(Math.floor(northing % 100000)).padStart(5, "0");

        return firstLetter + secondLetter + " " + eStr + " " + nStr;
    }

    BFT.latLonToBNG = function (lat, lon) {
        try {
            ensureProj4();
            if (typeof proj4 !== "undefined") {
                var result = proj4("EPSG:4326", "EPSG:27700", [lon, lat]);
                var easting = result[0];
                var northing = result[1];
                if (easting < 0 || easting > 700000 || northing < 0 || northing > 1300000) {
                    return "Outside BNG";
                }
                var ref = eastNorthToGridRef(easting, northing);
                return ref || "Outside BNG";
            }
        } catch (e) {
            console.warn("BNG conversion failed:", e);
        }
        return "BNG unavailable";
    };

    // --- DMS conversion ---
    BFT.latLonToDMS = function (lat, lon) {
        function toDMS(dd, posChar, negChar) {
            var dir = dd >= 0 ? posChar : negChar;
            dd = Math.abs(dd);
            var d = Math.floor(dd);
            var m = Math.floor((dd - d) * 60);
            var s = ((dd - d) * 60 - m) * 60;
            return d + "°" + String(m).padStart(2, "0") + "'" + s.toFixed(1).padStart(4, "0") + '"' + dir;
        }
        return toDMS(lat, "N", "S") + " " + toDMS(lon, "E", "W");
    };

    // --- Master formatter ---
    BFT.coordFormat = "mgrs"; // default

    BFT.formatCoord = function (lat, lon, format) {
        format = format || BFT.coordFormat;
        switch (format) {
            case "mgrs": return BFT.latLonToMGRS(lat, lon);
            case "bng": return BFT.latLonToBNG(lat, lon);
            case "dms": return BFT.latLonToDMS(lat, lon);
            case "dd":
            default:
                return lat.toFixed(6) + ", " + lon.toFixed(6);
        }
    };

    // --- Mils / Degrees conversions ---
    BFT.degreesToMils = function (degrees) {
        return degrees * 6400 / 360;
    };

    BFT.milsToDegrees = function (mils) {
        return mils * 360 / 6400;
    };

    BFT.formatBearing = function (degrees, useMils) {
        if (useMils) {
            var m = BFT.degreesToMils(degrees);
            return Math.round(m) + " mils";
        }
        return degrees.toFixed(0) + "°";
    };

    // --- Grid ref from map click for fire missions ---
    BFT.gridRefFromLatLon = function (lat, lon) {
        return BFT.latLonToMGRS(lat, lon);
    };

})();
