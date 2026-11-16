"""meshbus: let other tools on the Pi use the mesh radio without opening the serial port.

Only one process can own the Meshtastic serial port, and on this Pi that is the
bridge (mqtt_shim). Everything else talks to the radio through two MQTT topics
on the local broker (OpenTAKServer's RabbitMQ, 127.0.0.1:1883, guest/guest):

``meshbus/tx`` (tools -> radio)::

    {"text": "...", "to": null | "!nodeid", "channel": 0, "source": "<tool>"}

``meshbus/rx`` (radio -> tools), one message per inbound text::

    {"from": "!nodeid", "text": "...", "channel": 0, "ts": <epoch>,
     "from_short": "AB12", "from_long": "Alpha 1"}

``from_short`` and ``from_long`` are extra fields this bridge adds when it knows
the sender's node names. They are optional, so consumers must fall back to ``from``.

LoRa airtime is scarce, so transmit is limited here rather than trusting every
tool: text is truncated to ``MAX_TEXT_LEN``, each source may send at most one
message per ``PER_SOURCE_INTERVAL`` seconds, all sends are spaced by
``GLOBAL_INTERVAL`` and the queue is bounded (oldest dropped first).
"""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger("meshbus")

TOPIC_TX = "meshbus/tx"
TOPIC_RX = "meshbus/rx"

MAX_TEXT_LEN = 200
PER_SOURCE_INTERVAL = 3.0
GLOBAL_INTERVAL = 1.0
MAX_QUEUE = 20
MAX_CHANNEL = 7

_NODE_ID_RE = re.compile(r"^![0-9a-fA-F]{8}$")


@dataclass
class TxRequest:
    text: str
    to: str | None
    channel: int
    source: str


def parse_tx(payload: bytes | str) -> TxRequest:
    """Validate a meshbus/tx payload. Raises ValueError with a reason if invalid."""
    try:
        msg = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"not JSON: {e}") from None
    if not isinstance(msg, dict):
        raise ValueError("payload must be a JSON object")

    text = msg.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("missing or empty 'text'")
    text = text.strip()
    if len(text) > MAX_TEXT_LEN:
        text = text[: MAX_TEXT_LEN - 3] + "..."

    to = msg.get("to")
    if to in ("", "^all"):
        to = None
    if to is not None and (not isinstance(to, str) or not _NODE_ID_RE.match(to)):
        raise ValueError(f"'to' must be null or '!xxxxxxxx', got {to!r}")
    if to is not None:
        to = to.lower()

    channel = msg.get("channel", 0)
    if channel is None:
        channel = 0
    if not isinstance(channel, int) or isinstance(channel, bool) or not 0 <= channel <= MAX_CHANNEL:
        raise ValueError(f"'channel' must be an int 0-{MAX_CHANNEL}, got {channel!r}")

    source = msg.get("source") or "unknown"
    source = str(source)[:32]
    return TxRequest(text=text, to=to, channel=channel, source=source)


def build_rx(packet: dict, nodes: dict | None = None, my_node_num: int | None = None) -> dict | None:
    """Turn a meshtastic-python receive packet into a meshbus/rx message, or None to skip."""
    decoded = packet.get("decoded") or {}
    if decoded.get("portnum") != "TEXT_MESSAGE_APP":
        return None
    text = decoded.get("text")
    if not isinstance(text, str) or not text.strip():
        return None

    from_num = packet.get("from")
    if my_node_num is not None and from_num == my_node_num:
        return None

    from_id = packet.get("fromId")
    if not from_id and isinstance(from_num, int):
        from_id = f"!{from_num:08x}"
    if not from_id:
        return None

    msg = {
        "from": from_id,
        "text": text.strip(),
        "channel": int(packet.get("channel", 0) or 0),
        "ts": int(packet.get("rxTime") or time.time()),
    }
    user = ((nodes or {}).get(from_id) or {}).get("user") or {}
    if user.get("shortName"):
        msg["from_short"] = user["shortName"]
    if user.get("longName"):
        msg["from_long"] = user["longName"]
    return msg


class MeshbusGateway:
    """Rate-limited transmit queue in front of the radio.

    ``send_text(text, to, channel)`` is called from the worker thread and should
    raise on failure. ``clock`` and ``sleep`` are injectable for tests.
    """

    def __init__(
        self,
        send_text: Callable[[str, str | None, int], None],
        *,
        per_source_interval: float = PER_SOURCE_INTERVAL,
        global_interval: float = GLOBAL_INTERVAL,
        max_queue: int = MAX_QUEUE,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._send_text = send_text
        self._per_source_interval = per_source_interval
        self._global_interval = global_interval
        self._clock = clock
        self._queue: queue.Queue[TxRequest] = queue.Queue(maxsize=max_queue)
        self._last_by_source: dict[str, float] = {}
        self._last_send = float("-inf")
        self._lock = threading.Lock()
        self._running = False
        self.stats = {"accepted": 0, "rejected": 0, "rate_limited": 0, "dropped": 0, "sent": 0, "failed": 0}

    def submit(self, payload: bytes | str) -> bool:
        """Handle one meshbus/tx payload. Returns True if queued for transmit."""
        try:
            req = parse_tx(payload)
        except ValueError as e:
            self.stats["rejected"] += 1
            logger.warning("meshbus/tx rejected: %s", e)
            return False

        now = self._clock()
        with self._lock:
            last = self._last_by_source.get(req.source)
            if last is not None and now - last < self._per_source_interval:
                self.stats["rate_limited"] += 1
                logger.warning("meshbus/tx rate-limited source=%s (%.1fs since last)", req.source, now - last)
                return False
            self._last_by_source[req.source] = now

        while True:
            try:
                self._queue.put_nowait(req)
                break
            except queue.Full:
                try:
                    old = self._queue.get_nowait()
                    self.stats["dropped"] += 1
                    logger.warning("meshbus/tx queue full, dropped oldest from source=%s", old.source)
                except queue.Empty:
                    pass
        self.stats["accepted"] += 1
        logger.info("meshbus/tx queued source=%s to=%s ch=%d len=%d", req.source, req.to or "^all",
                    req.channel, len(req.text))
        return True

    def send_next(self, timeout: float | None = None) -> bool:
        """Send one queued message, respecting the global gap. Returns True if one was sent."""
        try:
            req = self._queue.get(timeout=timeout)
        except queue.Empty:
            return False
        wait = self._global_interval - (self._clock() - self._last_send)
        if wait > 0:
            time.sleep(wait)
        try:
            self._send_text(req.text, req.to, req.channel)
            self.stats["sent"] += 1
            logger.info("meshbus TX source=%s to=%s ch=%d: %s", req.source, req.to or "^all", req.channel,
                        req.text[:80])
        except Exception:
            self.stats["failed"] += 1
            logger.exception("meshbus TX failed source=%s", req.source)
        finally:
            self._last_send = self._clock()
        return True

    def start(self):
        self._running = True
        threading.Thread(target=self._worker, daemon=True, name="meshbus-tx").start()

    def stop(self):
        self._running = False

    def _worker(self):
        while self._running:
            self.send_next(timeout=1.0)
