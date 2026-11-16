"""Contract C4.1: heartbeat host is ISR_HOST, else the lowercased short hostname."""

from bridge import heartbeat


def test_isr_host_wins(monkeypatch):
    monkeypatch.setenv("ISR_HOST", "isr-droneops")
    monkeypatch.setattr(heartbeat.socket, "gethostname", lambda: "Field-Laptop.local")
    assert heartbeat.short_hostname() == "isr-droneops"
    assert heartbeat.build_payload("t", "", "cpu", "ok")["host"] == "isr-droneops"


def test_fallback_is_lowercased_short_hostname(monkeypatch):
    monkeypatch.delenv("ISR_HOST", raising=False)
    monkeypatch.setattr(heartbeat.socket, "gethostname", lambda: "Field-Laptop.local")
    assert heartbeat.short_hostname() == "field-laptop"
    monkeypatch.setenv("ISR_HOST", "  ")
    assert heartbeat.short_hostname() == "field-laptop"
