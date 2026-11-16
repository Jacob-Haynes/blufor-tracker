"""ISR-kit heartbeat: retained JSON on ``isrkit/heartbeat/<tool>`` every 10 s.

Payload (kit contract)::

    {"tool": "<repo-name>", "host": "<short hostname>", "url": "http://<host>.local:<port>/",
     "mode": "cpu|cuda|mps|gemini|vlm|simulate|replay", "status": "ok|degraded|error|stopped",
     "ts": <unix seconds>, "detail": "<optional human text>"}

The launcher on the Pi reads these. ``detail`` is an optional extra. On a clean exit
``stop()`` publishes a retained ``"stopped"``; if the process dies, the broker
publishes the MQTT will (``"error"``, "connection lost").

Environment: ``MQTT_HOST/PORT/USER/PASS`` (defaults 127.0.0.1:1883 guest/guest),
``PUBLIC_URL`` overrides the advertised UI URL, ``HEARTBEAT=0`` disables it.
Must never take the tool down: every failure is logged and ignored.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import socket
import threading
import time
from typing import Callable

logger = logging.getLogger("heartbeat")

TOPIC_FMT = "isrkit/heartbeat/{tool}"
INTERVAL_S = 10
MODES = ("cpu", "cuda", "mps", "gemini", "vlm", "simulate", "replay")
STATUSES = ("ok", "degraded", "error", "stopped")


def short_hostname() -> str:
    return socket.gethostname().split(".")[0]


def default_url(port: int | None, path: str = "/") -> str:
    env = os.environ.get("PUBLIC_URL", "").strip()
    if env:
        return env
    if port is None:
        return ""
    return f"http://{short_hostname()}.local:{port}{path}"


def build_payload(tool: str, url: str, mode: str, status: str, detail: str = "", now: float | None = None) -> dict:
    payload = {
        "tool": tool,
        "host": short_hostname(),
        "url": url,
        "mode": mode if mode in MODES else "cpu",
        "status": status if status in STATUSES else "error",
        "ts": int(time.time() if now is None else now),
    }
    if detail:
        payload["detail"] = detail[:200]
    return payload


class Heartbeat:
    """Publishes the tool's heartbeat from its own MQTT connection.

    ``state_fn()`` returns ``(mode, status, detail)`` and is called every interval.
    """

    def __init__(self, tool: str, state_fn: Callable[[], tuple[str, str, str]], port: int | None = None,
                 url: str | None = None, interval: float = INTERVAL_S):
        self.tool = tool
        self.topic = TOPIC_FMT.format(tool=tool)
        self.url = url if url is not None else default_url(port)
        self._state_fn = state_fn
        self._interval = interval
        self._enabled = os.environ.get("HEARTBEAT", "1").strip() != "0"
        self._running = False
        self._client = None
        self._stopped = False

    def start(self) -> "Heartbeat":
        if not self._enabled:
            logger.info("Heartbeat disabled (HEARTBEAT=0)")
            return self
        try:
            import paho.mqtt.client as mqtt

            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                            client_id=f"hb-{self.tool}-{short_hostname()}-{os.getpid()}",
                            protocol=mqtt.MQTTv311)
            c.username_pw_set(os.environ.get("MQTT_USER", "guest"), os.environ.get("MQTT_PASS", "guest"))
            will = build_payload(self.tool, self.url, self._safe_state()[0], "error", "connection lost")
            c.will_set(self.topic, json.dumps(will), qos=1, retain=True)
            c.reconnect_delay_set(min_delay=2, max_delay=30)
            c.connect_async(os.environ.get("MQTT_HOST", "127.0.0.1"), int(os.environ.get("MQTT_PORT", "1883")),
                            keepalive=30)
            c.loop_start()
            self._client = c
        except Exception:
            logger.exception("Heartbeat could not start (continuing without it)")
            return self
        self._running = True
        threading.Thread(target=self._loop, daemon=True, name="heartbeat").start()
        atexit.register(self.stop)
        return self

    def _safe_state(self) -> tuple[str, str, str]:
        try:
            mode, status, detail = self._state_fn()
            return mode, status, detail or ""
        except Exception as e:
            return "cpu", "error", f"state check failed: {e}"[:200]

    def publish_now(self, status_override: str | None = None, detail: str | None = None) -> bool:
        if self._client is None or not self._client.is_connected():
            return False
        mode, status, d = self._safe_state()
        payload = build_payload(self.tool, self.url, mode, status_override or status,
                                d if detail is None else detail)
        info = self._client.publish(self.topic, json.dumps(payload), qos=1, retain=True)
        return info.rc == 0

    def _loop(self):
        while self._running:
            try:
                self.publish_now()
            except Exception:
                logger.debug("Heartbeat publish failed", exc_info=True)
            for _ in range(int(self._interval * 10)):
                if not self._running:
                    return
                time.sleep(0.1)

    def stop(self):
        """Publish a retained "stopped" and disconnect (safe to call more than once)."""
        if self._stopped or self._client is None:
            return
        self._stopped = True
        self._running = False
        try:
            if self._client.is_connected():
                mode, _, _ = self._safe_state()
                info = self._client.publish(
                    self.topic, json.dumps(build_payload(self.tool, self.url, mode, "stopped")), qos=1, retain=True)
                info.wait_for_publish(timeout=2)
            self._client.disconnect()  # clean disconnect: broker does not send the will
            self._client.loop_stop()
        except Exception:
            logger.debug("Heartbeat stop failed", exc_info=True)
