"""ISR-kit TAK client (contract v5): one registered, long-lived TCP connection to OpenTAKServer.

Why (OpenTAKServer 1.8.3 on PostgreSQL, from its source):

1. CoT whose sender has no row in OTS's ``euds`` table is **dropped**, not just left out of the
   history: cot_parser's insert fails a foreign key and the event is never routed.
   A connection's sender is the uid of the first event on it that has a ``<contact>``.
2. OTS only subscribes a connection to the relay if that first ``<contact>`` event arrives
   *after* its internal RabbitMQ channel has opened (asynchronously, shortly after connect),
   and never retries. A client that registers at once can send but never receives.
3. ``euds.callsign`` is unique: a new uid with a callsign already taken silently gets no row,
   so everything it sends is dropped by (1).

So, per tool process (C5.1-C5.4):

* one TCP connection to ``TAK_HOST:TAK_PORT``, reconnecting with backoff;
* on every (re)connect, wait ``REGISTER_DELAY_S`` (3 s), then send a registration SA:
  uid ``isrkit-<repo>-<ISR_HOST>``, callsign ``<SHORT> <ISR_HOST>`` (stable, unique per
  tool and station), type ``a-f-G-E-S``, ``<contact endpoint="*:-1:stcp">``,
  ``<__group name="Cyan" role="Team Member">``, ``<takv platform="isr-kit" device="<repo>">``,
  at ``SENSOR_LAT``/``SENSOR_LON`` with stale now+120 s, re-sent every 60 s. With no position
  set: point 0,0 with ce/le/hae 9999999 and stale = start, so it is never drawn;
* every marker goes on that same connection, and only after registration (queued until then);
* receivers pass ``on_event`` and get every CoT event OTS relays to them.

Markers (``build_marker`` / ``TakClient.marker``): OTS relays any CoT from a registered sender,
but it only stores a marker (OTS web map, ``/api/markers``) when cot_parser.parse_marker can
insert it. From the 1.8.3 source:

* type ``a-[fhupansjk]-[ZPAGSUF]...`` or ``b-m-p...`` (not ``b-m-p-s-p-loc``);
* **no** ``<takv>`` and **no** ``<contact endpoint=...>`` (those mean "EUD position update");
* a **real position**: ``markers.point_id`` is NOT NULL, and OTS stores no point for lat/lon 0,0
  or lat "999...". Without a position the insert fails ("'NoneType' object is not
  subscriptable") and the marker is relayed but never stored;
* ``<link uid=...>`` only to an **EUD uid** (``markers.parent_uid`` is a foreign key to
  ``euds.uid``), e.g. this tool's registration uid. A link to another marker fails the same way;
* name from ``<contact callsign>``; optional ``<color argb>``, ``<usericon iconsetpath>``,
  ``<precisionlocation altsrc>``. ``<remarks>`` is relayed but not stored in the markers table.

Field lengths: OTS 1.8.3 stores text in ``varchar(255)`` columns (euds.uid/callsign/device,
markers.uid/callsign/parent_callsign/iconset_path/location_source, geochat.remarks, ...).
A longer value fails the insert: for a marker only the stored copy is lost, but for the CoT
row (uid/type/how) or a GeoChat the event is nacked and **never relayed**. ``ots_fit`` cuts
such fields to ``OTS_FIELD_MAX`` (250) characters, measured after XML escaping, ending in "…",
with one warning per field. Marker ``<remarks>`` are not stored, so they are not cut.

Self-contained (stdlib only); the same file is in each kit repo.
Env: ``TAK_HOST`` (127.0.0.1), ``TAK_PORT`` (8087), ``ISR_HOST``, ``SENSOR_LAT``/``SENSOR_LON``,
``TAK_REGISTER_DELAY_S`` (3; tests use less).
"""

from __future__ import annotations

import logging
import os
import queue
import select
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Callable
from xml.sax.saxutils import quoteattr

logger = logging.getLogger("tak_client")

REGISTER_DELAY_S = 3.0
SA_INTERVAL_S = 60.0
SA_STALE_S = 120.0
SA_TYPE = "a-f-G-E-S"
PLATFORM = "isr-kit"
QUEUE_MAX = 500
BACKOFF_MIN, BACKOFF_MAX = 1.0, 30.0
UNKNOWN = "9999999"


def station_name() -> str:
    """``ISR_HOST`` if set, else the lowercased short hostname (contract C4.1)."""
    env = os.environ.get("ISR_HOST", "").strip()
    return env or socket.gethostname().split(".")[0].lower()


def _env_float(name: str) -> float | None:
    try:
        v = os.environ.get(name, "").strip()
        return float(v) if v else None
    except ValueError:
        return None


def cot_time(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


OTS_VARCHAR = 255  # OTS 1.8.3 String(255) columns
OTS_FIELD_MAX = 250  # what we send, with a margin
_warned_fields: set[str] = set()


def ots_fit(value: str | None, field: str, limit: int = OTS_FIELD_MAX) -> str | None:
    """``value`` cut so it is at most ``limit`` characters both as text and XML-escaped
    (OTS varchar(255) columns), ending in "…"; warns once per ``field``."""
    from xml.sax.saxutils import escape

    if value is None or len(escape(value)) <= limit:
        return value
    cut = value[:limit]
    while cut and len(escape(cut.rstrip() + "…")) > limit:
        cut = cut[:-1]
    if field not in _warned_fields:
        _warned_fields.add(field)
        logger.warning("%s is %d characters, over OTS's varchar(%d): cut to %d (logged once)",
                       field, len(value), OTS_VARCHAR, limit)
    return cut.rstrip() + "…"


def build_sa(uid: str, callsign: str, device: str, lat: float | None, lon: float | None,
             now: float | None = None, version: str = "5") -> str:
    """The registration SA (contract C5.2)."""
    uid, callsign, device = ots_fit(uid, "SA uid"), ots_fit(callsign, "SA callsign"), ots_fit(device, "SA device")
    now = time.time() if now is None else now
    t = cot_time(now)
    if lat is not None and lon is not None and (lat or lon):
        point = f'<point lat="{lat:.7f}" lon="{lon:.7f}" hae="0" ce="50" le="50"/>'
        stale = cot_time(now + SA_STALE_S)
    else:  # no position: the only allowed 0,0, already stale so ATAK never draws it
        point = f'<point lat="0" lon="0" hae="{UNKNOWN}" ce="{UNKNOWN}" le="{UNKNOWN}"/>'
        stale = t
    return (
        f'<event version="2.0" uid={quoteattr(uid)} type="{SA_TYPE}" how="h-g-i-g-o" '
        f'time="{t}" start="{t}" stale="{stale}">{point}<detail>'
        f'<contact callsign={quoteattr(callsign)} endpoint="*:-1:stcp"/>'
        '<__group name="Cyan" role="Team Member"/>'
        f'<takv platform="{PLATFORM}" device={quoteattr(device)} os="linux" version={quoteattr(version)}/>'
        f"<uid Droid={quoteattr(callsign)}/>"
        "</detail></event>"
    )


def build_marker(uid: str, cot_type: str, callsign: str, lat: float | None, lon: float | None,
                 now: float | None = None, stale_s: float = 300.0, hae: float = 0.0, ce: float = 10.0,
                 le: float = 10.0, remarks: str = "", how: str = "m-g", parent_uid: str | None = None,
                 parent_callsign: str | None = None, argb: int | None = None,
                 iconsetpath: str | None = None, speed: float | None = None, course: float | None = None,
                 location_source: str | None = None, extra: str = "") -> str | None:
    """A marker OTS relays *and* stores (see the module docstring). None without a position:
    a position-bearing marker is never sent at 0,0 (contract C4.2)."""
    from xml.sax.saxutils import escape

    if lat is None or lon is None or (lat == 0 and lon == 0):
        return None
    uid, callsign = ots_fit(uid, "marker uid"), ots_fit(callsign, "marker callsign")
    parent_uid, parent_callsign = ots_fit(parent_uid, "marker parent_uid"), ots_fit(parent_callsign,
                                                                                   "marker parent_callsign")
    iconsetpath = ots_fit(iconsetpath, "marker iconsetpath")
    location_source = ots_fit(location_source, "marker location_source")
    now = time.time() if now is None else now
    t = cot_time(now)
    detail = [f"<contact callsign={quoteattr(callsign)}/>"]
    if remarks:
        detail.append(f"<remarks>{escape(remarks)}</remarks>")
    if parent_uid:  # must be an EUD uid (markers.parent_uid -> euds.uid)
        attrs = f'uid={quoteattr(parent_uid)} relation="p-p" type="{SA_TYPE}" production_time="{t}"'
        if parent_callsign:
            attrs += f" parent_callsign={quoteattr(parent_callsign)}"
        detail.append(f"<link {attrs}/>")
    if argb is not None:
        detail.append(f'<color argb="{int(argb)}"/>')
    if iconsetpath:
        detail.append(f"<usericon iconsetpath={quoteattr(iconsetpath)}/>")
    if speed is not None or course is not None:
        detail.append(f'<track speed="{(speed or 0):.1f}" course="{(course or 0):.1f}"/>')
    if location_source:
        detail.append(f"<precisionlocation altsrc={quoteattr(location_source)} "
                      f"geopointsrc={quoteattr(location_source)}/>")
    return (
        f'<event version="2.0" uid={quoteattr(uid)} type={quoteattr(cot_type)} how={quoteattr(how)} '
        f'time="{t}" start="{t}" stale="{cot_time(now + stale_s)}">'
        f'<point lat="{lat:.7f}" lon="{lon:.7f}" hae="{hae:.1f}" ce="{ce:.0f}" le="{le:.0f}"/>'
        f"<detail>{''.join(detail)}{extra}</detail></event>"
    )


def build_delete(target_uid: str, target_type: str, lat: float | None, lon: float | None,
                 now: float | None = None, remarks: str = "") -> str | None:
    """Remove a marker from ATAK maps at once: ``t-x-d-d`` linked to it, with ``<__forcedelete/>``.

    OTS 1.8.3 relays it but keeps its own stored copy, so send the marker once more with
    stale = now first. It stores the event's point against the sender, so give the OP (never
    0,0; None without a position), and it marks the sender "Disconnected" until its next SA, so
    call ``TakClient.send_sa_now()`` afterwards."""
    from xml.sax.saxutils import escape

    if lat is None or lon is None or (lat == 0 and lon == 0):
        return None
    now = time.time() if now is None else now
    t = cot_time(now)
    uid = ots_fit(f"{target_uid}-del-{int(now * 1000)}", "delete uid")
    detail = (f"<link uid={quoteattr(ots_fit(target_uid, 'delete link uid'))} relation=\"none\" "
              f"type={quoteattr(target_type)}/><__forcedelete/>")
    if remarks:
        detail += f"<remarks>{escape(remarks)}</remarks>"
    return (
        f'<event version="2.0" uid={quoteattr(uid)} type="t-x-d-d" how="h-g-i-g-o" '
        f'time="{t}" start="{t}" stale="{cot_time(now + 10)}">'
        f'<point lat="{lat:.7f}" lon="{lon:.7f}" hae="0.0" ce="9999999" le="9999999"/>'
        f"<detail>{detail}</detail></event>"
    )


class CotStream:
    """Splits a TCP byte stream into complete ``<event>...</event>`` strings."""

    def __init__(self):
        self._buf = ""

    def feed(self, data: str) -> list[str]:
        self._buf += data
        out = []
        while "</event>" in self._buf:
            end = self._buf.index("</event>") + len("</event>")
            chunk, self._buf = self._buf[:end], self._buf[end:]
            start = chunk.rfind("<event")
            if start >= 0:
                out.append(chunk[start:])
        if len(self._buf) > 1_000_000:  # garbage without an </event>: don't grow forever
            self._buf = ""
        return out


class TakClient:
    """A registered OTS client. ``send(xml)`` queues CoT; a background thread owns the socket."""

    def __init__(self, repo: str, short: str, host: str | None = None, port: int | None = None,
                 station: str | None = None, sensor_lat: float | None = None,
                 sensor_lon: float | None = None, on_event: Callable[[str], None] | None = None,
                 register_delay: float | None = None, sa_interval: float = SA_INTERVAL_S,
                 uid: str | None = None, callsign: str | None = None, device: str | None = None):
        env = os.environ.get
        self.host = host or env("TAK_HOST", "127.0.0.1")
        self.port = int(port or env("TAK_PORT", "8087"))
        self.station = station or station_name()
        self.uid = uid or f"isrkit-{repo}-{self.station}"
        self.callsign = callsign or f"{short} {self.station}"
        self.device = device or repo
        self.sensor_lat = sensor_lat if sensor_lat is not None else _env_float("SENSOR_LAT")
        self.sensor_lon = sensor_lon if sensor_lon is not None else _env_float("SENSOR_LON")
        self.on_event = on_event
        self.register_delay = (float(env("TAK_REGISTER_DELAY_S", REGISTER_DELAY_S))
                               if register_delay is None else register_delay)
        self.sa_interval = sa_interval
        self._queue: queue.Queue[str] = queue.Queue(maxsize=QUEUE_MAX)
        self._running = False
        self._thread: threading.Thread | None = None
        self._sa_now = threading.Event()
        self.connected = False
        self.registered = threading.Event()
        self.sent = 0
        self.dropped = 0
        self.received = 0
        self.last_error = ""

    @property
    def has_position(self) -> bool:
        return self.sensor_lat is not None and self.sensor_lon is not None and bool(
            self.sensor_lat or self.sensor_lon)

    def sa(self, now: float | None = None) -> str:
        return build_sa(self.uid, self.callsign, self.device, self.sensor_lat, self.sensor_lon, now)

    def marker(self, uid: str, cot_type: str, callsign: str, lat: float | None, lon: float | None,
               **kw) -> str | None:
        """``build_marker`` with this tool as the parent EUD, so OTS can store it."""
        kw.setdefault("parent_uid", self.uid)
        kw.setdefault("parent_callsign", self.callsign)
        return build_marker(uid, cot_type, callsign, lat, lon, **kw)

    # ── public ────────────────────────────────────────────────────

    def start(self) -> "TakClient":
        if not self._running:
            self._running = True
            self._thread = threading.Thread(target=self._run, daemon=True, name=f"tak-{self.device}")
            self._thread.start()
        return self

    def stop(self):
        self._running = False

    def send(self, xml: str) -> None:
        """Queue one CoT event; sent on the registered connection (oldest dropped when full)."""
        try:
            self._queue.put_nowait(xml)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self.dropped += 1
            except queue.Empty:
                pass
            self._queue.put_nowait(xml)

    def send_sa_now(self):
        self._sa_now.set()

    def wait_registered(self, timeout: float) -> bool:
        return self.registered.wait(timeout)

    # ── socket thread ─────────────────────────────────────────────

    def _sleep(self, seconds: float) -> bool:
        end = time.monotonic() + seconds
        while self._running and time.monotonic() < end:
            time.sleep(min(0.05, max(0.0, end - time.monotonic())))
        return self._running

    def _run(self):
        backoff = BACKOFF_MIN
        pending: str | None = None
        while self._running:
            try:
                sock = socket.create_connection((self.host, self.port), timeout=5)
            except OSError as e:
                self.last_error = str(e)
                logger.warning("TAK %s:%d unavailable (%s), retry in %.0fs", self.host, self.port, e, backoff)
                self._sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)
                continue
            self.connected = True
            stream = CotStream()
            try:
                # OTS binds this connection to the relay only once its RabbitMQ channel is open
                if not self._sleep(self.register_delay):
                    break
                sock.sendall(self.sa().encode())
                last_sa = time.monotonic()
                self.registered.set()
                backoff = BACKOFF_MIN
                logger.info("Registered with TAK %s:%d as %s (%s)", self.host, self.port, self.uid,
                            self.callsign)
                while self._running:
                    if self._sa_now.is_set() or time.monotonic() - last_sa >= self.sa_interval:
                        self._sa_now.clear()
                        sock.sendall(self.sa().encode())
                        last_sa = time.monotonic()
                    readable, _, _ = select.select([sock], [], [], 0.05)
                    if readable:
                        data = sock.recv(65536)
                        if not data:
                            raise ConnectionError("TAK server closed the connection")
                        for ev in stream.feed(data.decode("utf-8", errors="replace")):
                            self.received += 1
                            if self.on_event is not None:
                                try:
                                    self.on_event(ev)
                                except Exception:
                                    logger.exception("on_event failed")
                    for _ in range(50):
                        if pending is None:
                            try:
                                pending = self._queue.get_nowait()
                            except queue.Empty:
                                break
                        sock.sendall(pending.encode("utf-8"))
                        pending = None
                        self.sent += 1
            except OSError as e:
                self.last_error = str(e)
                logger.warning("TAK connection lost (%s), reconnecting", e)
            finally:
                self.connected = False
                self.registered.clear()
                try:
                    sock.close()
                except OSError:
                    pass
            self._sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)
