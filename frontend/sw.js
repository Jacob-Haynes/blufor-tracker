/**
 * Service Worker for Blue Force Tracker — Offline/PWA Support
 * - App shell: cache-first
 * - Tile requests: cache-first with network fallback (OpenTopoMap priority)
 * - API requests: network-first with cache fallback
 */

var CACHE_NAME = "bft-shell-v1";
var TILE_CACHE = "bft-tiles-v1";
var API_CACHE = "bft-api-v1";

var APP_SHELL = [
    "/",
    "/static/style.css",
    "/static/app.js",
    "/static/coords.js",
    "/static/reports.js",
    "/static/firemission.js",
    "/static/topology.js",
    "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css",
    "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
    "https://cdnjs.cloudflare.com/ajax/libs/leaflet.draw/1.0.4/leaflet.draw.css",
    "https://cdnjs.cloudflare.com/ajax/libs/leaflet.draw/1.0.4/leaflet.draw.js",
    "https://cdn.jsdelivr.net/npm/mgrs@1/mgrs.min.js",
    "https://cdn.jsdelivr.net/npm/proj4@2/dist/proj4.js",
];

var TILE_HOSTS = [
    "server.arcgisonline.com",
    "tile.opentopomap.org",
    "basemaps.cartocdn.com",
];

var MAX_TILE_CACHE_ENTRIES = 5000;

// Install — pre-cache app shell
self.addEventListener("install", function (event) {
    event.waitUntil(
        caches.open(CACHE_NAME).then(function (cache) {
            return cache.addAll(APP_SHELL);
        }).then(function () {
            return self.skipWaiting();
        })
    );
});

// Activate — clean old caches
self.addEventListener("activate", function (event) {
    event.waitUntil(
        caches.keys().then(function (keys) {
            return Promise.all(
                keys.filter(function (key) {
                    return key !== CACHE_NAME && key !== TILE_CACHE && key !== API_CACHE;
                }).map(function (key) {
                    return caches.delete(key);
                })
            );
        }).then(function () {
            return self.clients.claim();
        })
    );
});

// Fetch handler
self.addEventListener("fetch", function (event) {
    var url = new URL(event.request.url);

    // Skip WebSocket and non-GET
    if (event.request.method !== "GET") return;
    if (url.protocol === "ws:" || url.protocol === "wss:") return;

    // Tile requests — cache-first
    var isTile = TILE_HOSTS.some(function (host) {
        return url.hostname.indexOf(host) !== -1;
    });
    if (isTile) {
        event.respondWith(
            caches.open(TILE_CACHE).then(function (cache) {
                return cache.match(event.request).then(function (cached) {
                    if (cached) return cached;
                    return fetch(event.request).then(function (response) {
                        if (response.ok) {
                            cache.put(event.request, response.clone());
                            trimTileCache(cache);
                        }
                        return response;
                    }).catch(function () {
                        return new Response("", { status: 503 });
                    });
                });
            })
        );
        return;
    }

    // API requests — network-first
    if (url.pathname.indexOf("/api/") === 0) {
        event.respondWith(
            fetch(event.request).then(function (response) {
                if (response.ok) {
                    var clone = response.clone();
                    caches.open(API_CACHE).then(function (cache) {
                        cache.put(event.request, clone);
                    });
                }
                return response;
            }).catch(function () {
                return caches.open(API_CACHE).then(function (cache) {
                    return cache.match(event.request).then(function (cached) {
                        return cached || new Response(JSON.stringify({ error: "offline" }), {
                            headers: { "Content-Type": "application/json" },
                            status: 503,
                        });
                    });
                });
            })
        );
        return;
    }

    // App shell — cache-first
    event.respondWith(
        caches.match(event.request).then(function (cached) {
            if (cached) return cached;
            return fetch(event.request).then(function (response) {
                if (response.ok) {
                    var clone = response.clone();
                    caches.open(CACHE_NAME).then(function (cache) {
                        cache.put(event.request, clone);
                    });
                }
                return response;
            });
        })
    );
});

// LRU eviction for tile cache
function trimTileCache(cache) {
    cache.keys().then(function (keys) {
        if (keys.length > MAX_TILE_CACHE_ENTRIES) {
            // Delete oldest entries (first in list)
            var toDelete = keys.length - MAX_TILE_CACHE_ENTRIES;
            for (var i = 0; i < toDelete; i++) {
                cache.delete(keys[i]);
            }
        }
    });
}

// Listen for tile seeding messages from main thread
self.addEventListener("message", function (event) {
    if (event.data && event.data.type === "SEED_TILES") {
        var urls = event.data.urls;
        var total = urls.length;
        var done = 0;

        caches.open(TILE_CACHE).then(function (cache) {
            var queue = urls.slice();

            function next() {
                if (queue.length === 0) {
                    notifyClients({ type: "SEED_COMPLETE", total: total });
                    return;
                }
                var url = queue.shift();
                cache.match(url).then(function (existing) {
                    if (existing) {
                        done++;
                        notifyProgress(done, total);
                        next();
                    } else {
                        fetch(url).then(function (resp) {
                            if (resp.ok) cache.put(url, resp);
                            done++;
                            notifyProgress(done, total);
                            next();
                        }).catch(function () {
                            done++;
                            next();
                        });
                    }
                });
            }

            // Run 4 concurrent fetchers
            for (var i = 0; i < Math.min(4, queue.length); i++) {
                next();
            }
        });
    }
});

function notifyProgress(done, total) {
    if (done % 10 === 0 || done === total) {
        notifyClients({ type: "SEED_PROGRESS", done: done, total: total });
    }
}

function notifyClients(msg) {
    self.clients.matchAll().then(function (clients) {
        clients.forEach(function (client) {
            client.postMessage(msg);
        });
    });
}
