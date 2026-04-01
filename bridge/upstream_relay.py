"""Optional upstream TAK server relay — forwards CoT bidirectionally."""

import logging
import queue
import socket
import ssl
import threading
import time

from bridge.cot_converter import UID_PREFIX, CotStreamParser

logger = logging.getLogger(__name__)

_DEDUP_WINDOW = 30  # seconds
_MAX_QUEUE = 200
_BACKOFF_INITIAL = 2.0
_BACKOFF_MAX = 60.0


class UpstreamRelay:
    """Manages a TCP connection to a remote TAK server, forwarding CoT events
    upstream and optionally relaying downstream events to a callback."""

    def __init__(
        self,
        host: str,
        port: int,
        tls: bool = False,
        certfile: str | None = None,
        cafile: str | None = None,
        downstream_callback=None,
    ):
        self.host = host
        self.port = port
        self.tls = tls
        self.certfile = certfile
        self.cafile = cafile
        self._downstream_callback = downstream_callback

        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._running = False
        self._send_queue: queue.Queue[str] = queue.Queue(maxsize=_MAX_QUEUE)
        self._sent_uids: dict[str, float] = {}
        self._backoff = _BACKOFF_INITIAL

    def start(self):
        self._running = True
        threading.Thread(target=self._send_loop, daemon=True).start()
        if self._downstream_callback:
            threading.Thread(target=self._recv_loop, daemon=True).start()
        logger.info("Upstream relay started → %s:%d (TLS=%s)", self.host, self.port, self.tls)

    def stop(self):
        self._running = False
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None

    def send(self, cot_xml: str):
        uid = _extract_uid(cot_xml)
        if uid:
            self._sent_uids[uid] = time.time()

        try:
            self._send_queue.put_nowait(cot_xml)
        except queue.Full:
            logger.debug("Upstream send queue full, dropping oldest")
            try:
                self._send_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._send_queue.put_nowait(cot_xml)
            except queue.Full:
                pass

    # ── Connection ────────────────────────────────────────────────────

    def _connect(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)

            if self.tls:
                ctx = ssl.create_default_context(cafile=self.cafile)
                if self.certfile:
                    ctx.load_cert_chain(self.certfile)
                sock = ctx.wrap_socket(sock, server_hostname=self.host)

            sock.connect((self.host, self.port))
            sock.settimeout(None)

            with self._lock:
                self._sock = sock

            self._backoff = _BACKOFF_INITIAL
            logger.info("Connected to upstream TAK server %s:%d", self.host, self.port)
        except (OSError, ssl.SSLError) as e:
            logger.warning(
                "Cannot connect to upstream %s:%d: %s (retry in %.0fs)",
                self.host, self.port, e, self._backoff,
            )
            with self._lock:
                self._sock = None
            time.sleep(self._backoff)
            self._backoff = min(self._backoff * 2, _BACKOFF_MAX)

    def _ensure_connected(self):
        with self._lock:
            connected = self._sock is not None
        if not connected:
            self._connect()

    # ── Send thread ───────────────────────────────────────────────────

    def _send_loop(self):
        while self._running:
            self._ensure_connected()

            try:
                cot_xml = self._send_queue.get(timeout=1)
            except queue.Empty:
                self._cleanup_sent_uids()
                continue

            with self._lock:
                sock = self._sock

            if sock is None:
                continue

            try:
                sock.sendall(cot_xml.encode("utf-8"))
            except (OSError, BrokenPipeError):
                logger.warning("Upstream send failed, reconnecting")
                with self._lock:
                    self._sock = None

    # ── Recv thread ───────────────────────────────────────────────────

    def _recv_loop(self):
        parser = CotStreamParser()
        while self._running:
            with self._lock:
                sock = self._sock

            if sock is None:
                time.sleep(1)
                continue

            try:
                data = sock.recv(4096)
                if not data:
                    logger.warning("Upstream connection closed")
                    with self._lock:
                        self._sock = None
                    time.sleep(2)
                    continue

                for event_xml in parser.feed(data.decode("utf-8", errors="replace")):
                    self._handle_downstream(event_xml)

            except socket.timeout:
                continue
            except OSError:
                logger.warning("Upstream read error")
                with self._lock:
                    self._sock = None
                time.sleep(2)

    def _handle_downstream(self, cot_xml: str):
        uid = _extract_uid(cot_xml)

        # Drop echoes of events we sent upstream
        if uid and uid in self._sent_uids:
            return

        # Drop events that originated from any bridge
        if uid and uid.startswith(UID_PREFIX):
            return

        logger.info("Upstream→Local: [%s]", uid or "?")
        self._downstream_callback(cot_xml)

    # ── Housekeeping ──────────────────────────────────────────────────

    def _cleanup_sent_uids(self):
        now = time.time()
        stale = [uid for uid, ts in self._sent_uids.items() if now - ts > _DEDUP_WINDOW]
        for uid in stale:
            del self._sent_uids[uid]


def _extract_uid(cot_xml: str) -> str | None:
    start = cot_xml.find('uid="')
    if start < 0:
        return None
    start += 5
    end = cot_xml.find('"', start)
    if end < 0:
        return None
    return cot_xml[start:end]
