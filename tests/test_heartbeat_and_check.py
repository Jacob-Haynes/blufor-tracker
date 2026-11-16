import socket
import threading

from bridge import check, heartbeat


def test_heartbeat_payload_contract():
    p = heartbeat.build_payload("blufor-tracker", "http://isr-pi.local/", "cpu", "ok", "radio up", now=100)
    assert set(p) == {"tool", "host", "url", "mode", "status", "ts", "detail"}
    assert p["tool"] == "blufor-tracker" and p["ts"] == 100 and "." not in p["host"]
    assert heartbeat.build_payload("x", "", "bogus", "weird")["mode"] == "cpu"
    assert heartbeat.build_payload("x", "", "simulate", "weird")["status"] == "error"
    assert heartbeat.build_payload("x", "", "llm", "ok")["mode"] == "llm"


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


def test_check_tak_fails_cleanly_when_nothing_listens():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    ok, msg = check.check_tak("127.0.0.1", port, timeout=1)
    assert not ok and "nothing listening on TAK_PORT" in msg
