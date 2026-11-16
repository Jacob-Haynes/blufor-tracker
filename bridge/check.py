"""Demo-day self-check: is TAK and meshbus plumbing actually working on this Pi?

    python -m bridge.check                       # TAK + MQTT checks, list heartbeats
    python -m bridge.check --mesh-text "radio check"   # also transmit a real mesh text via meshbus

Checks:
1. **TAK send + receive through OpenTAKServer.** Connection A says hello (uid + contact, as
   tac-advisor does) and listens; connection B sends a test marker with no hello, as the sensor
   tools do. PASS means OTS routed B's event to A. It proves the CoT port, the sender path
   and the listener path in one go. The test marker is already stale, so ATAK won't draw it.
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


def _event(uid: str, callsign: str, cot_type: str = "a-f-G-U-C", extra: str = "") -> str:
    now = _t(time.time())
    return (f'<event version="2.0" uid={quoteattr(uid)} type="{cot_type}" how="h-g-i-g-o" '
            f'time="{now}" start="{now}" stale="{now}"><point lat="0" lon="0" hae="0" ce="9999999" '
            f'le="9999999"/><detail><contact callsign={quoteattr(callsign)}/>{extra}</detail></event>')


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


def check_tak(host: str, port: int, timeout: float = 8.0) -> tuple[bool, str]:
    tag = uuid.uuid4().hex[:8]
    try:
        a = socket.create_connection((host, port), timeout=5)
    except OSError as e:
        return False, explain_no_listener(host, port, e)
    try:
        b = socket.create_connection((host, port), timeout=5)
    except OSError as e:
        a.close()
        return False, f"cannot open a second connection to {host}:{port} ({e})"
    try:
        a.sendall(_event(f"ISR-CHECK-RX-{tag}", f"ISR-CHECK-RX-{tag}",
                         extra='<takv platform="isr-check" device="pi" os="linux" version="1"/>').encode())
        time.sleep(1.0)  # let OTS bind A's queue
        b.sendall(_event(f"ISR-CHECK-TX-{tag}", f"ISR-CHECK-TX-{tag}", cot_type="a-u-G").encode())
        a.settimeout(0.5)
        got = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data = a.recv(65536)
            except socket.timeout:
                continue
            if not data:
                return False, "OTS closed the listener connection"
            got += data
            if f"ISR-CHECK-TX-{tag}".encode() in got:
                return True, f"OTS at {host}:{port} routed a sender's CoT to a listener"
        return False, (f"connected to {host}:{port} but the test event never came back within {timeout:.0f}s "
                       "(wrong port? not OTS? OTS cot_parser not running?)")
    finally:
        a.close()
        b.close()


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
    p = argparse.ArgumentParser(description="ISR kit self-check (TAK + MQTT/meshbus + heartbeats)")
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
