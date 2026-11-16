"""A fake OpenTAKServer TCP port that enforces the three OTS 1.8.3 rules tak_client works around.

1. **Foreign-key drop.** An event is relayed only if its sender (the connection's registered uid,
   else the event's own uid) has an EUD row.
2. **Bind delay.** A connection is subscribed to the relay only if its first ``<contact>`` event
   arrives at least ``bind_delay`` seconds after connect (OTS's RabbitMQ channel opening).
   It is never re-checked.
3. **Unique callsign.** A new uid whose callsign another EUD already has gets no EUD row.

Relayed events go to every bound connection except the sender's. ``markers`` mimics
cot_parser.parse_marker's storage: a marker-type event with no ``<takv>``/``<contact endpoint>`` is
stored only with a real position (``point_id`` is NOT NULL) and a ``<link uid>``, if any, that
is an EUD uid (``parent_uid`` foreign key); failures go to ``marker_errors``. Test helper, stdlib only.
"""

from __future__ import annotations

import re
import socket
import threading
import time

_UID = re.compile(r'<event[^>]*\suid="([^"]*)"')
_CALLSIGN = re.compile(r'<contact[^>]*\scallsign="([^"]*)"')
_TYPE = re.compile(r'<event[^>]*\stype="([^"]*)"')
_POINT = re.compile(r'<point[^>]*\slat="([^"]*)"[^>]*\slon="([^"]*)"')
_LINK = re.compile(r'<link[^>]*\suid="([^"]*)"')
_MARKER_TYPE = re.compile(r"^(a-[fhupansjk]-[ZPAGSUF]|b-m-p)")


class _Conn:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.connected_at = time.monotonic()
        self.uid: str | None = None
        self.bound = False


class FakeOTS:
    def __init__(self, bind_delay: float = 0.3):
        self.bind_delay = bind_delay
        self.euds: dict[str, str] = {}  # uid -> callsign
        self.dropped: list[str] = []  # sender uids whose events were dropped (FK)
        self.relayed: list[str] = []  # event uids relayed
        self.markers: dict[str, str] = {}  # stored marker uid -> callsign
        self.marker_errors: list[str] = []
        self.conns: list[_Conn] = []
        self._lock = threading.Lock()
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(20)
        self.port = self._srv.getsockname()[1]
        self._running = True
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self):
        self._running = False
        try:
            self._srv.close()
        except OSError:
            pass
        for c in list(self.conns):
            try:
                c.sock.close()
            except OSError:
                pass

    def drop_all_connections(self):
        for c in list(self.conns):
            try:
                c.sock.shutdown(socket.SHUT_RDWR)
                c.sock.close()
            except OSError:
                pass

    def _accept(self):
        while self._running:
            try:
                s, _ = self._srv.accept()
            except OSError:
                return
            c = _Conn(s)
            with self._lock:
                self.conns.append(c)
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c: _Conn):
        buf = ""
        while self._running:
            try:
                data = c.sock.recv(65536)
            except OSError:
                break
            if not data:
                break
            buf += data.decode("utf-8", errors="replace")
            while "</event>" in buf:
                end = buf.index("</event>") + len("</event>")
                ev, buf = buf[:end], buf[end:]
                ev = ev[ev.find("<event"):]
                self._handle(c, ev)
        with self._lock:
            if c in self.conns:
                self.conns.remove(c)

    def _handle(self, c: _Conn, ev: str):
        m = _UID.search(ev)
        ev_uid = m.group(1) if m else ""
        cs = _CALLSIGN.search(ev)
        with self._lock:
            if c.uid is None and cs and ev_uid and not ev_uid.endswith("ping"):
                c.uid = ev_uid
                c.bound = time.monotonic() - c.connected_at >= self.bind_delay  # rule 2
                owner = next((u for u, name in self.euds.items() if name == cs.group(1)), None)
                if owner is None or owner == ev_uid:  # rule 3
                    self.euds[ev_uid] = cs.group(1)
            sender = c.uid or ev_uid
            if sender not in self.euds:  # rule 1
                self.dropped.append(sender)
                return
            self.relayed.append(ev_uid)
            self._store_marker(ev, ev_uid)
            targets = [o for o in self.conns if o.bound and o.uid != sender]
        for o in targets:
            try:
                o.sock.sendall(ev.encode())
            except OSError:
                pass

    def _store_marker(self, ev: str, ev_uid: str):
        t = _TYPE.search(ev)
        cot_type = t.group(1) if t else ""
        if (not _MARKER_TYPE.match(cot_type) or cot_type == "b-m-p-s-p-loc" or "<takv" in ev
                or re.search(r"<contact[^>]*endpoint=", ev)):
            return
        p = _POINT.search(ev)
        lat, lon = (p.group(1), p.group(2)) if p else ("0", "0")
        link = _LINK.search(ev)
        if lat.startswith("999") or (float(lat) == 0 and float(lon) == 0):
            self.marker_errors.append(f"{ev_uid}: no point ('NoneType' object is not subscriptable)")
        elif link and link.group(1) not in self.euds:
            self.marker_errors.append(f"{ev_uid}: parent_uid {link.group(1)} not in euds")
        else:
            cs = _CALLSIGN.search(ev)
            self.markers[ev_uid] = cs.group(1) if cs else ""
