import socket
import threading

from bridge import check, heartbeat


def test_heartbeat_payload_contract():
    p = heartbeat.build_payload("blufor-tracker", "http://isr-pi.local/", "cpu", "ok", "radio up", now=100)
    assert set(p) == {"tool", "host", "url", "mode", "status", "ts", "detail"}
    assert p["tool"] == "blufor-tracker" and p["ts"] == 100 and "." not in p["host"]
    assert heartbeat.build_payload("x", "", "bogus", "weird")["mode"] == "cpu"
    assert heartbeat.build_payload("x", "", "simulate", "weird")["status"] == "error"


def test_default_url_uses_dot_local(monkeypatch):
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    assert heartbeat.default_url(8090) == f"http://{heartbeat.short_hostname()}.local:8090/"
    monkeypatch.setenv("PUBLIC_URL", "http://isr-pi.local:8090/")
    assert heartbeat.default_url(8090) == "http://isr-pi.local:8090/"


def test_heartbeat_disabled_and_broker_absent_are_harmless(monkeypatch):
    monkeypatch.setenv("HEARTBEAT", "0")
    heartbeat.Heartbeat("t", lambda: ("cpu", "ok", "")).start().stop()
    monkeypatch.setenv("HEARTBEAT", "1")
    monkeypatch.setenv("MQTT_PORT", "9")
    hb = heartbeat.Heartbeat("t", lambda: ("cpu", "ok", ""), port=1).start()
    assert hb.publish_now() is False
    hb.stop()


class FakeOTS:
    """Routes CoT from any client to clients that have sent a contact event (like OTS __ANON__)."""

    def __init__(self):
        self.listeners = []
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(5)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn):
        buf = b""
        while True:
            try:
                data = conn.recv(65536)
            except OSError:
                return
            if not data:
                return
            buf += data
            while b"</event>" in buf:
                ev, buf = buf.split(b"</event>", 1)
                ev += b"</event>"
                if b"<contact" in ev and b"takv" in ev and conn not in self.listeners:
                    self.listeners.append(conn)
                for l in self.listeners:
                    try:
                        l.sendall(ev)
                    except OSError:
                        pass


def test_check_tak_passes_against_ots_like_server():
    ots = FakeOTS()
    ok, msg = check.check_tak("127.0.0.1", ots.port, timeout=3)
    assert ok, msg
    ots.srv.close()


def test_check_tak_fails_cleanly_when_nothing_listens():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    ok, msg = check.check_tak("127.0.0.1", port, timeout=1)
    assert not ok and "cannot connect" in msg
