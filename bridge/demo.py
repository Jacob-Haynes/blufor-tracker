"""Step-by-step demo for blufor-tracker (ui-kit DEMO-TRIGGER-CONTRACT.md): a simulated patrol on demand.

``DEMO_MODE=continuous`` (default) changes nothing that runs today: ``python -m bridge --simulate``
keeps its five drifting units, the mesh bridge keeps bridging. ``DEMO_MODE=on_demand`` runs no
BFT simulation (and no fake mesh texts under ``bridge.mqtt_shim --simulate``); the real radio
bridge keeps running. In both modes the presenter can fire:

* ``patrol``: three simulated friendly units (``SIM-Patrol1..3``, type ``a-f-G-U-C``) walking a
  route near the OP in file, 20 m apart, at 1.5 m/s for 3 minutes. Each unit updates every 5 s
  (C4.4), remarks start ``SIMULATED: `` (C4.3), positions around ``SENSOR_LAT/LON`` (C4.2).

Sent through ``bridge.tak_client`` as plain markers linked to this tool's registered uid, like
``bridge.bft_sim``. The mesh bridge itself has no TAK connection, so the demo opens one
(``isrkit-blufor-tracker-<ISR_HOST>``, callsign ``BFT-SIM <ISR_HOST>``) the first time a scenario
is fired, never before: continuous mode adds nothing on the TAK server.

``clear()`` stops the patrol and removes its markers from ATAK at once: each again with stale =
now, a ``t-x-d-d`` forcedelete, then the registration SA.

The bridge had no web server, so this module adds a small stdlib one on ``DEMO_PORT`` (default
8170, after isr-maps' 8160; ``DEMO_PORT=0`` turns it off), dual-stack like the launcher. It
serves the Demo page at ``/`` (kit v2 look) and the API:

* ``GET  /api/demo/scenarios``, ``POST /api/demo/trigger {"id": "patrol"}``, ``POST /api/demo/clear``
* ``GET  /api/status``: demo mode, active scenarios, TAK state, the patrol's units.
"""

from __future__ import annotations

import json
import logging
import math
import os
import socket
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from bridge.tak_client import TakClient, build_delete

logger = logging.getLogger("bft_demo")

MODES = ("continuous", "on_demand")
DEFAULT_PORT = 8170
DEFAULT_CENTRE = (51.15, -1.75)  # Salisbury Plain, as bridge.bft_sim
M_PER_DEG = 111_320.0
UPDATE_S = 5.0  # C4.4: at most one update per unit per 5 s
STALE_S = 120.0
SIM_REMARKS = "SIMULATED: "
STATIC_DIR = Path(__file__).parent / "web" / "static"

# Patrol route, metres (east, north) from the OP: out to the north-east and back, a loop of ~1 km
ROUTE = [(-250.0, -150.0), (-150.0, 0.0), (-50.0, 120.0), (100.0, 180.0), (220.0, 120.0),
         (180.0, -60.0), (0.0, -180.0)]
PATROL = ["Patrol1", "Patrol2", "Patrol3"]
PATROL_SPEED = 1.5  # m/s, walking
PATROL_GAP_M = 20.0
PATROL_S = 180.0


def demo_mode(value: str | None = None) -> str:
    """``DEMO_MODE`` (continuous | on_demand); anything else is continuous, with a warning."""
    raw = (os.environ.get("DEMO_MODE", "") if value is None else value).strip().lower().replace("-", "_")
    if raw in ("", "continuous"):
        return "continuous"
    if raw in ("on_demand", "ondemand", "step", "step_by_step"):
        return "on_demand"
    logger.warning("DEMO_MODE=%r not understood: using continuous", raw)
    return "continuous"


def demo_port() -> int:
    try:
        return int(os.environ.get("DEMO_PORT", str(DEFAULT_PORT)).strip() or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def offset(lat: float, lon: float, east: float, north: float) -> tuple[float, float]:
    return lat + north / M_PER_DEG, lon + east / (M_PER_DEG * math.cos(math.radians(lat)))


def _legs(route):
    legs = []
    for a, b in zip(route, route[1:] + route[:1]):
        legs.append((a, b, math.hypot(b[0] - a[0], b[1] - a[1])))
    return legs


def along_route(distance: float, route=ROUTE) -> tuple[float, float, float]:
    """(east, north, course) ``distance`` metres along the closed route (wraps round)."""
    legs = _legs(route)
    total = sum(n for _, _, n in legs)
    d = distance % total
    for (a, b, n) in legs:
        if d <= n:
            f = d / n if n else 0.0
            course = math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360
            return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, course
        d -= n
    a, b, _ = legs[-1]
    return b[0], b[1], 0.0


@dataclass(frozen=True)
class Scenario:
    id: str
    label: str
    hint: str
    duration_s: float

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "hint": self.hint, "duration_s": self.duration_s}


SCENARIOS = {"patrol": Scenario("patrol", "Start patrol",
                                "Three simulated friendly units walk a route near the OP for 3 minutes", PATROL_S)}


class DemoController:
    """Runs triggered scenarios and clears them. ``client`` is created on first use if not given."""

    def __init__(self, centre: tuple[float, float] | None = None, client: TakClient | None = None,
                 mode: str = "continuous", clock: Callable[[], float] = time.time,
                 host: str | None = None, port: int | None = None):
        self.centre = centre or DEFAULT_CENTRE
        self.mode = mode
        self._client = client
        self._client_args = (host, port)
        self._clock = clock
        self._lock = threading.Lock()
        self._t0: float | None = None  # patrol start
        self._last_sent: dict[str, float] = {}
        self._positions: dict[str, tuple[float, float, float]] = {}  # unit -> lat, lon, course
        self._shown: set[str] = set()  # units with a marker on ATAK (what clear() removes)
        self._running = False
        self.sent = 0

    # ── TAK ───────────────────────────────────────────────────────

    @property
    def client(self) -> TakClient:
        if self._client is None:
            host, port = self._client_args
            self._client = TakClient("blufor-tracker", "BFT-SIM", host=host, port=port,
                                     sensor_lat=self.centre[0], sensor_lon=self.centre[1])
        return self._client

    @property
    def tak_connected(self) -> bool:
        return self._client is not None and self._client.registered.is_set()

    # ── API ───────────────────────────────────────────────────────

    def active(self) -> list[str]:
        with self._lock:
            t0 = self._t0
        return ["patrol"] if t0 is not None and self._clock() < t0 + PATROL_S else []

    def scenarios(self) -> dict:
        return {"mode": self.mode, "scenarios": [s.to_dict() for s in SCENARIOS.values()], "active": self.active()}

    def trigger(self, scenario_id: str, **_options) -> dict:
        """Start (or restart from the beginning) a scenario. KeyError if unknown."""
        scenario = SCENARIOS[scenario_id]
        now = self._clock()
        with self._lock:
            self._t0 = now
            self._last_sent.clear()
        self.client.start()
        logger.info("Demo %s started (%d simulated units, %.0f s)", scenario_id, len(PATROL), scenario.duration_s)
        self.tick(now)
        return {"ok": True, "id": scenario_id, "until": int(now + scenario.duration_s)}

    def clear(self) -> dict:
        """Stop the patrol and take its markers off ATAK now (live or already ended)."""
        now = self._clock()
        with self._lock:
            stopped = 1 if self._t0 is not None and now < self._t0 + PATROL_S else 0
            self._t0 = None
            shown = sorted(self._shown)
            self._shown.clear()
            positions = dict(self._positions)
            self._positions.clear()
        for unit in shown:
            lat, lon, course = positions.get(unit, (*self.centre, 0.0))
            uid = f"SIM-BFT-{unit}"
            self.client.send(self._marker(unit, lat, lon, course, now, stale_s=0.0, note="demo cleared"))
            delete = build_delete(uid, "a-f-G-U-C", *self.centre, now=now, remarks=SIM_REMARKS + "demo cleared")
            if delete:
                self.client.send(delete)
        if shown:
            self.client.send_sa_now()  # OTS marks the sender disconnected on t-x-d-d
        logger.info("Demo cleared: %d scenario stopped, %d markers removed", stopped, len(shown))
        return {"ok": True, "cleared": stopped, "tracks": len(shown)}

    def units(self) -> list[dict]:
        with self._lock:
            return [{"callsign": f"SIM-{u}", "lat": round(p[0], 6), "lon": round(p[1], 6), "course": round(p[2])}
                    for u, p in sorted(self._positions.items())]

    # ── running ───────────────────────────────────────────────────

    def _marker(self, unit: str, lat: float, lon: float, course: float, now: float, stale_s: float = STALE_S,
                note: str = "demo patrol") -> str | None:
        return self.client.marker(
            f"SIM-BFT-{unit}", "a-f-G-U-C", f"SIM-{unit}", lat, lon, now=now, stale_s=stale_s, hae=120.0,
            remarks=f"{SIM_REMARKS}{note} (step-by-step demo)", speed=PATROL_SPEED, course=course,
            location_source="GPS")

    def tick(self, now: float | None = None) -> int:
        """Send each due unit's position. Returns how many went out."""
        now = self._clock() if now is None else now
        with self._lock:
            t0 = self._t0
            if t0 is None:
                return 0
            if now >= t0 + PATROL_S:
                self._t0 = None
                logger.info("Demo patrol finished (its markers expire as normal)")
                return 0
            due = [(i, u) for i, u in enumerate(PATROL) if now - self._last_sent.get(u, -1e9) >= UPDATE_S]
            for _, u in due:
                self._last_sent[u] = now
        out = 0
        for i, unit in due:
            east, north, course = along_route(PATROL_SPEED * (now - t0) + (len(PATROL) - 1 - i) * PATROL_GAP_M)
            lat, lon = offset(*self.centre, east, north)
            xml = self._marker(unit, lat, lon, course, now)
            if xml:
                with self._lock:
                    self._positions[unit] = (lat, lon, course)
                    self._shown.add(unit)
                self.client.send(xml)
                self.sent += 1
                out += 1
        return out

    def start(self) -> "DemoController":
        if not self._running:
            self._running = True
            threading.Thread(target=self._loop, daemon=True, name="bft-demo").start()
        return self

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                self.tick()
            except Exception:
                logger.exception("Demo tick failed")
            time.sleep(1.0)

    def heartbeat_extra(self) -> dict:
        return {"demo_mode": self.mode, "demo_active": self.active()}

    def heartbeat_text(self) -> str:
        active = self.active()
        if self.mode == "continuous" and not active:
            return ""
        return f"; demo {self.mode}" + (f": {', '.join(active)} (SIMULATED)" if active else "")


# ── HTTP ──────────────────────────────────────────────────────────

_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
          ".js": "application/javascript; charset=utf-8", ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8"}


def _handler(demo: DemoController):
    class Handler(BaseHTTPRequestHandler):
        server_version = "bft-demo"

        def log_message(self, fmt, *args):  # quiet: one line per request is noise on the Pi
            logger.debug("http %s", fmt % args)

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            cache = "max-age=3600" if ctype.startswith(("font/", "text/css", "application/javascript")) else "no-cache"
            self.send_header("Cache-Control", cache)  # the page and the API always fresh; kit files are ?v=N
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data):
            self._send(code, json.dumps(data).encode(), "application/json")

        def _static(self, rel: str):
            path = (STATIC_DIR / rel).resolve()
            if STATIC_DIR.resolve() not in path.parents or not path.is_file() or path.suffix not in _TYPES:
                return self._json(404, {"detail": "Not found"})
            self._send(200, path.read_bytes(), _TYPES[path.suffix])

        def do_GET(self):
            route = self.path.split("?", 1)[0]
            if route == "/":
                return self._static("index.html")
            if route.startswith("/static/"):
                return self._static(route[len("/static/"):])
            if route == "/api/demo/scenarios":
                return self._json(200, demo.scenarios())
            if route == "/api/status":
                return self._json(200, {"demo_mode": demo.mode, "demo_active": demo.active(),
                                        "tak_connected": demo.tak_connected, "units": demo.units(),
                                        "centre": list(demo.centre)})
            return self._json(404, {"detail": "Not found"})

        def do_POST(self):
            route = self.path.split("?", 1)[0]
            length = min(int(self.headers.get("Content-Length") or 0), 4096)
            raw = self.rfile.read(length) if length else b""
            if route == "/api/demo/clear":
                return self._json(200, demo.clear())
            if route == "/api/demo/trigger":
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    return self._json(400, {"detail": "Body must be JSON"})
                sid = body.get("id") if isinstance(body, dict) else None
                if not isinstance(sid, str) or sid not in SCENARIOS:
                    return self._json(400, {"detail": "Unknown scenario"})
                return self._json(200, demo.trigger(sid, **{k: v for k, v in body.items() if k != "id"}))
            return self._json(404, {"detail": "Not found"})

    return Handler


class _V4(ThreadingHTTPServer):
    daemon_threads = True


class _DualStack(_V4):
    address_family = socket.AF_INET6

    def server_bind(self):
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            pass
        super().server_bind()


def serve(demo: DemoController, port: int | None = None, host: str = "::") -> ThreadingHTTPServer | None:
    """Start the Demo page and API on a background thread. None when ``DEMO_PORT=0`` or the port is taken."""
    port = demo_port() if port is None else port
    if not port:
        logger.info("Demo page off (DEMO_PORT=0)")
        return None
    handler = _handler(demo)
    try:
        httpd: ThreadingHTTPServer = _DualStack((host, port), handler)
    except OSError:
        try:  # no IPv6 on this host
            httpd = _V4(("0.0.0.0" if host == "::" else host, port), handler)
        except OSError as e:
            logger.error("Demo page could not listen on port %d (%s): the bridge carries on without it", port, e)
            return None
    threading.Thread(target=httpd.serve_forever, daemon=True, name="bft-demo-http").start()
    logger.info("Demo page and API on port %d (DEMO_MODE=%s)", httpd.server_address[1], demo.mode)
    return httpd
