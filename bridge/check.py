"""Demo-day self-check: is TAK and meshbus plumbing actually working on this Pi?

    python -m bridge.check                       # TAK + MQTT checks, list heartbeats
    python -m bridge.check --mesh-text "radio check"   # also transmit a real mesh text via meshbus

Checks:
1. **TAK send + receive through OpenTAKServer** (kit contract v5, ``bridge.tak_client``).
   Two registered clients with stable identities, ``isrkit-check-a-<host>`` / "CHECK-A <host>"
   and ``isrkit-check-b-<host>`` / "CHECK-B <host>", each waiting 3 s after connect before
   its SA. B listens; A sends a test marker (already stale, so ATAK won't draw it). The result
   is one of: "round-trip OK", "unregistered sender dropped" (OTS on PostgreSQL drops CoT whose
   sender has no EUD row, e.g. a callsign already taken by another uid) or "listener not bound"
   (OTS never subscribed B, e.g. it registered before OTS's RabbitMQ channel opened). On the
   Pi it also looks the two uids up in OTS's ``euds`` table via ``psql`` when it can.
2. **MQTT broker** (OTS RabbitMQ) accepts our login.
3. **Heartbeats**: every retained ``isrkit/heartbeat/#`` message, with its status and age.
4. Optional: publish ``--mesh-text`` to ``meshbus/tx`` (the bridge transmits it on the mesh).

Exit code 0 if all required checks pass.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from xml.sax.saxutils import quoteattr


def _t(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


OTS_DEFAULT_TCP_PORT = 8088  # OTS's own default OTS_TCP_STREAMING_PORT


def _listening(host: str, port: int) -> bool:
    try:
        socket.create_connection((host, port), timeout=2).close()
        return True
    except OSError:
        return False


def explain_no_listener(host: str, port: int, error: OSError) -> str:
    """Why the TAK port is closed, naming the OTS-on-8088 case (real-Pi bug BT-2)."""
    if port != OTS_DEFAULT_TCP_PORT and _listening(host, OTS_DEFAULT_TCP_PORT):
        return (f"nothing listening on TAK_PORT {port}, OTS may be on {OTS_DEFAULT_TCP_PORT} "
                f"(its default). Set OTS_TCP_STREAMING_PORT: {port} in ~/ots/config.yml "
                f"(sudo bash deploy/install.sh does it) and restart OTS")
    return (f"nothing listening on TAK_PORT {port} at {host} ({error}); "
            f"nothing on {OTS_DEFAULT_TCP_PORT} either. Is opentakserver running?")


CHECK_MARKER_PREFIX = "ISR-CHECK-"


def _marker(uid: str, tag: str, client=None, now: float | None = None) -> str:
    """Test marker, already stale (never drawn). With SENSOR_LAT/LON set it is built with
    tak_client.marker so OTS also stores it (one row per station, updated each run); without a
    position OTS still relays it but logs "Failed to parse marker" and does not store it."""
    from bridge.tak_client import _env_float

    now = time.time() if now is None else now
    lat, lon = _env_float("SENSOR_LAT"), _env_float("SENSOR_LON")
    remarks = f"isr-kit self-check marker {tag}"
    if client is not None:
        xml = client.marker(uid, "a-u-G", "ISTAR check", lat, lon, now=now, stale_s=0, remarks=remarks)
        if xml:
            return xml
    t = _t(now)
    return (f'<event version="2.0" uid={quoteattr(uid)} type="a-u-G" how="h-g-i-g-o" time="{t}" '
            f'start="{t}" stale="{t}"><point lat="0" lon="0" hae="9999999" ce="9999999" le="9999999"/>'
            f'<detail><remarks>{remarks}</remarks></detail></event>')


def euds_lookup(uids: list[str], callsigns: list[str], config: str | None = None) -> dict | None:
    """OTS's euds rows for these uids/callsigns via psql, or None if that isn't possible here.

    Reads SQLALCHEMY_DATABASE_URI from ~/ots/config.yml (OTS_CONFIG overrides). Returns
    {"by_uid": {uid: callsign}, "by_callsign": {callsign: uid}}."""
    import re
    import shutil
    import subprocess

    path = config or os.environ.get("OTS_CONFIG") or os.path.expanduser("~/ots/config.yml")
    if not shutil.which("psql") or not os.path.exists(path):
        return None
    try:
        text = open(path).read()
    except OSError:
        return None
    m = re.search(r"^SQLALCHEMY_DATABASE_URI:\s*['\"]?(postgresql[^'\"\s]*)", text, re.M)
    if not m:
        return None
    uri = re.sub(r"^postgresql\+\w+://", "postgresql://", m.group(1))

    def q(v: str) -> str:
        return "'" + v.replace("'", "''") + "'"

    sql = (f"SELECT uid, coalesce(callsign, '') FROM euds WHERE uid IN ({','.join(map(q, uids))}) "
           f"OR callsign IN ({','.join(map(q, callsigns))})")
    from urllib.parse import unquote, urlsplit, urlunsplit

    parts = urlsplit(uri)
    env = dict(os.environ)
    if parts.password:  # pass it via the environment, not on the command line (ps shows argv)
        env["PGPASSWORD"] = unquote(parts.password)
        netloc = parts.netloc.split("@", 1)[1]
        user = parts.username or ""
        uri = urlunsplit((parts.scheme, f"{user}@{netloc}" if user else netloc, parts.path, "", ""))
    try:
        out = subprocess.run(["psql", uri, "-tAF", "|", "-c", sql], capture_output=True, text=True,
                             timeout=10, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    rows = [ln.split("|", 1) for ln in out.stdout.splitlines() if "|" in ln]
    return {"by_uid": {u: c for u, c in rows}, "by_callsign": {c: u for u, c in rows if c}}


def _sensor_pos():
    from bridge.tak_client import _env_float

    return _env_float("SENSOR_LAT"), _env_float("SENSOR_LON")


def check_tak(host: str, port: int, timeout: float = 8.0, station: str | None = None,
              register_delay: float | None = None, db=euds_lookup) -> tuple[bool, str]:
    from bridge.tak_client import TakClient

    try:
        socket.create_connection((host, port), timeout=5).close()
    except OSError as e:
        return False, explain_no_listener(host, port, e)

    got_a: list[str] = []
    got_b: list[str] = []
    b = TakClient("check-b", "CHECK-B", host=host, port=port, station=station, on_event=got_b.append,
                  register_delay=register_delay, sensor_lat=0.0, sensor_lon=0.0, device="blufor-tracker")
    a = TakClient("check-a", "CHECK-A", host=host, port=port, station=station, on_event=got_a.append,
                  register_delay=register_delay, sensor_lat=0.0, sensor_lon=0.0, device="blufor-tracker")
    tag = uuid.uuid4().hex[:8]  # matches this run's marker; its uid is stable per station
    marker_uid = f"{CHECK_MARKER_PREFIX}{b.station}"
    wait_reg = (b.register_delay or 0) + 10
    try:
        b.start()
        if not b.wait_registered(wait_reg):
            return False, f"CHECK-B could not connect and register at {host}:{port} ({b.last_error})"
        a.start()
        if not a.wait_registered(wait_reg):
            return False, f"CHECK-A could not connect and register at {host}:{port} ({a.last_error})"
        a.send(_marker(marker_uid, tag, a))
        b.send_sa_now()  # lets A tell whether OTS relays B at all
        deadline = time.time() + timeout
        while time.time() < deadline and not any(tag in e for e in got_b):
            time.sleep(0.1)
    finally:
        a.stop()
        b.stop()

    if any(tag in e for e in got_b):
        note = "" if a.marker(marker_uid, "a-u-G", "x", *_sensor_pos()) else (
            " (SENSOR_LAT/LON not set: OTS relays the marker but can't store it on its web map, "
            "so cot_parser logs 'Failed to parse marker' for it; that is expected)")
        return True, f"round-trip OK: OTS at {host}:{port} relayed CHECK-A's marker to CHECK-B{note}"

    b_saw_a = any(a.uid in e for e in got_b)
    a_saw_b = any(b.uid in e for e in got_a)
    rows = db([a.uid, b.uid], [a.callsign, b.callsign]) if db else None
    if rows is not None:
        owner = rows["by_callsign"].get(a.callsign)
        if a.uid not in rows["by_uid"]:
            why = (f"callsign '{a.callsign}' already belongs to uid {owner}" if owner and owner != a.uid
                   else "no EUD row was created")
            return False, (f"unregistered sender dropped: {a.uid} is not in OTS's euds table ({why}), "
                           "so OTS drops its CoT")
        return False, (f"listener not bound: both clients are registered in euds, but CHECK-B received "
                       f"nothing{' (CHECK-A did receive CHECK-B)' if a_saw_b else ''}. OTS subscribed it "
                       "before its RabbitMQ channel opened? Retry with TAK_REGISTER_DELAY_S=6")
    if b_saw_a:
        return False, ("unregistered sender dropped? CHECK-A's SA reached CHECK-B but its marker did not "
                       "(check cot_parser.log for ForeignKeyViolation)")
    if a_saw_b:
        return False, ("unregistered sender dropped or listener not bound: CHECK-A received CHECK-B, but "
                       "nothing from CHECK-A reached CHECK-B. Likely CHECK-A's callsign is taken in euds "
                       "(run this on the Pi with psql to tell). See cot_parser.log for ForeignKeyViolation")
    return False, ("listener not bound: neither client received anything from the other, so OTS bound "
                   "neither connection, or dropped both senders (cot_parser.log: ForeignKeyViolation?). "
                   "Retry with TAK_REGISTER_DELAY_S=6")


def check_mqtt(host: str, port: int, user: str, password: str, mesh_text: str | None, wait: float = 3.0):
    import paho.mqtt.client as mqtt

    result = {"connected": False, "rc": None, "heartbeats": {}, "sent": False}
    connected = threading.Event()

    def on_connect(c, u, f, rc, p):
        result["rc"] = str(rc)
        if rc == 0:
            result["connected"] = True
            c.subscribe("isrkit/heartbeat/#")
        connected.set()

    def on_message(c, u, m):
        try:
            result["heartbeats"][m.topic.rsplit("/", 1)[-1]] = json.loads(m.payload)
        except ValueError:
            result["heartbeats"][m.topic] = {"status": "unparseable"}

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"isr-check-{os.getpid()}",
                    protocol=mqtt.MQTTv311)
    c.username_pw_set(user, password)
    c.on_connect = on_connect
    c.on_message = on_message
    try:
        c.connect(host, port, keepalive=30)
    except OSError as e:
        result["rc"] = str(e)
        return result
    c.loop_start()
    connected.wait(5)
    if result["connected"]:
        if mesh_text:
            c.publish("meshbus/tx", json.dumps({"text": mesh_text[:200], "to": None, "channel": 0,
                                                "source": "isr-check"}))
            result["sent"] = True
        time.sleep(wait)  # retained heartbeats arrive straight after subscribe
    c.loop_stop()
    c.disconnect()
    return result


def main(argv=None) -> int:
    env = os.environ.get
    p = argparse.ArgumentParser(description="ISTAR kit self-check (TAK + MQTT/meshbus + heartbeats)")
    p.add_argument("--tak-host", default=env("TAK_HOST", "127.0.0.1"))
    p.add_argument("--tak-port", type=int, default=int(env("TAK_PORT", "8087")))
    p.add_argument("--mqtt-host", default=env("MQTT_HOST", "127.0.0.1"))
    p.add_argument("--mqtt-port", type=int, default=int(env("MQTT_PORT", "1883")))
    p.add_argument("--mqtt-user", default=env("MQTT_USER", "guest"))
    p.add_argument("--mqtt-pass", default=env("MQTT_PASS", "guest"))
    p.add_argument("--mesh-text", default=None, help="Also transmit this text on the mesh via meshbus")
    p.add_argument("--skip-tak", action="store_true")
    args = p.parse_args(argv)

    ok = True
    if not args.skip_tak:
        tak_ok, msg = check_tak(args.tak_host, args.tak_port)
        print(f"[{'PASS' if tak_ok else 'FAIL'}] TAK: {msg}")
        ok &= tak_ok

    r = check_mqtt(args.mqtt_host, args.mqtt_port, args.mqtt_user, args.mqtt_pass, args.mesh_text)
    if r["connected"]:
        print(f"[PASS] MQTT: logged in to {args.mqtt_host}:{args.mqtt_port} as {args.mqtt_user}")
    else:
        print(f"[FAIL] MQTT: {args.mqtt_host}:{args.mqtt_port} as {args.mqtt_user}: {r['rc']}")
        ok = False
    if r["sent"]:
        print(f"[INFO] meshbus: sent {args.mesh_text!r} — check it arrives on a handheld "
              "(journalctl -u mesh-bridge shows 'meshbus TX source=isr-check')")

    if r["connected"]:
        if not r["heartbeats"]:
            print("[WARN] Heartbeats: none retained yet (no tool has started since the broker came up)")
        now = time.time()
        for tool, hb in sorted(r["heartbeats"].items()):
            age = now - float(hb.get("ts", 0) or 0)
            state = hb.get("status", "?")
            stale = age > 30 and state not in ("stopped",)
            mark = "WARN" if stale or state in ("error", "degraded") else "OK  "
            print(f"[{mark}] {tool:18} {state:9} {hb.get('mode', ''):9} {age:6.0f}s ago  "
                  f"{hb.get('url', '')}  {hb.get('detail', '')}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
