"""Step-by-step demo (DEMO_MODE=on_demand): the patrol scenario, Clear demo and the Demo page/API."""

import json
import math
import socket
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

import pytest

from bridge import bft_sim, demo as demo_mod
from bridge.demo import PATROL_S, DemoController, along_route, demo_mode, serve
from bridge.tak_client import TakClient
from tests.fake_ots import FakeOTS

CENTRE = (51.2, -1.8)


class Recorder(TakClient):
    """Builds markers like the real client; records instead of connecting."""

    def __init__(self):
        super().__init__("blufor-tracker", "BFT-SIM", station="test", sensor_lat=CENTRE[0], sensor_lon=CENTRE[1])
        self.sent, self.sa_now, self.started = [], 0, 0

    def send(self, xml):
        self.sent.append(xml)

    def send_sa_now(self):
        self.sa_now += 1

    def start(self):
        self.started += 1
        return self


def make(mode="on_demand"):
    c = {"t": 1_000_000.0}
    rec = Recorder()
    return DemoController(CENTRE, client=rec, mode=mode, clock=lambda: c["t"]), rec, c


def dist_m(lat, lon):
    return math.hypot((lat - CENTRE[0]) * 111_320, (lon - CENTRE[1]) * 111_320 * math.cos(math.radians(CENTRE[0])))


def test_demo_mode_env(monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    assert demo_mode() == "continuous"
    assert demo_mode("on-demand") == "on_demand" and demo_mode("bogus") == "continuous"


def test_route_is_continuous_and_closed():
    pts = [along_route(d) for d in range(0, 2000, 5)]
    assert all(math.hypot(b[0] - a[0], b[1] - a[1]) <= 5.01 for a, b in zip(pts, pts[1:]))
    assert all(0 <= p[2] < 360 for p in pts)
    assert max(math.hypot(p[0], p[1]) for p in pts) < 300, "near the OP"


def test_scenarios_follow_the_contract():
    d, _, _ = make()
    s = d.scenarios()
    assert s == {"mode": "on_demand", "active": [],
                 "scenarios": [{"id": "patrol", "label": "Start patrol", "duration_s": PATROL_S,
                                "hint": s["scenarios"][0]["hint"]}]}


def test_nothing_sent_and_no_tak_connection_until_triggered():
    d = DemoController(CENTRE, mode="on_demand")
    assert d.tick() == 0 and d._client is None, "continuous/idle adds nothing to the TAK server"


def test_patrol_walks_near_the_op_labelled_and_rate_limited():
    d, rec, c = make()
    r = d.trigger("patrol")
    assert r == {"ok": True, "id": "patrol", "until": int(c["t"] + PATROL_S)} and rec.started == 1
    assert d.active() == ["patrol"]
    first = [ET.fromstring(x) for x in rec.sent]
    assert [e.get("uid") for e in first] == ["SIM-BFT-Patrol1", "SIM-BFT-Patrol2", "SIM-BFT-Patrol3"]
    for e in first:
        assert e.get("type") == "a-f-G-U-C"
        assert e.find("detail/contact").get("callsign").startswith("SIM-Patrol")
        assert e.find("detail/remarks").text.startswith("SIMULATED: ")
        assert e.find("detail/link").get("uid") == rec.uid, "linked to the registered uid: OTS stores it"
        p = e.find("point")
        assert 50 < dist_m(float(p.get("lat")), float(p.get("lon"))) < 400
    c["t"] += 2
    assert d.tick() == 0, "C4.4: one update per unit per 5 s"
    c["t"] += 3
    assert d.tick() == 3
    moved = ET.fromstring(rec.sent[-3]).find("point")
    p0 = first[0].find("point")
    walked = dist_m(float(moved.get("lat")), float(moved.get("lon"))) - 0
    assert moved.get("lat") != p0.get("lat") or moved.get("lon") != p0.get("lon")
    assert walked > 0 and len(d.units()) == 3
    c["t"] += PATROL_S
    assert d.tick() == 0 and d.active() == [], "ends on its own"


def test_clear_takes_the_patrol_off_atak_at_once():
    d, rec, c = make()
    d.trigger("patrol")
    c["t"] += 10
    d.tick()
    rec.sent.clear()
    out = d.clear()
    assert out == {"ok": True, "cleared": 1, "tracks": 3}
    events = [ET.fromstring(x) for x in rec.sent]
    deletes = [e for e in events if e.get("type") == "t-x-d-d"]
    assert {e.find("detail/link").get("uid") for e in deletes} == {"SIM-BFT-Patrol1", "SIM-BFT-Patrol2",
                                                                  "SIM-BFT-Patrol3"}
    assert all(e.find("detail/__forcedelete") is not None and e.find("point").get("lat") == "51.2000000"
               for e in deletes)
    markers = [e for e in events if e.get("type") == "a-f-G-U-C"]
    assert len(markers) == 3 and all(e.get("stale") == e.get("time") for e in markers), "stale = now first"
    assert rec.sa_now == 1 and d.active() == [] and d.units() == []
    c["t"] += 10
    assert d.tick() == 0, "stopped"
    assert d.clear() == {"ok": True, "cleared": 0, "tracks": 0}


def test_clear_after_the_patrol_ended_still_removes_its_markers():
    d, rec, c = make()
    d.trigger("patrol")
    c["t"] += PATROL_S + 1
    d.tick()
    out = d.clear()
    assert out["cleared"] == 0 and out["tracks"] == 3


def test_unknown_scenario():
    d, _, _ = make()
    with pytest.raises(KeyError):
        d.trigger("nope")


def test_patrol_and_clear_through_ots():
    ots = FakeOTS(bind_delay=0.2)
    try:
        client = TakClient("blufor-tracker", "BFT-SIM", host="127.0.0.1", port=ots.port, station="isr-pi",
                           register_delay=0.3, sensor_lat=CENTRE[0], sensor_lon=CENTRE[1])
        d = DemoController(CENTRE, client=client, mode="on_demand")
        d.trigger("patrol")
        end = time.time() + 5
        while time.time() < end and len([m for m in ots.markers if m.startswith("SIM-BFT-Patrol")]) < 3:
            time.sleep(0.05)
        assert {m for m in ots.markers if m.startswith("SIM-BFT-")} == {
            "SIM-BFT-Patrol1", "SIM-BFT-Patrol2", "SIM-BFT-Patrol3"}, "stored on the OTS web map"
        d.clear()
        end = time.time() + 5
        while time.time() < end and sum(u.startswith("SIM-BFT-Patrol") and "-del-" in u for u in ots.relayed) < 3:
            time.sleep(0.05)
        assert sum("-del-" in u for u in ots.relayed) == 3 and not ots.nacked and not ots.dropped
        client.stop()
    finally:
        ots.close()


# ── HTTP ──────────────────────────────────────────────────────────

def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _req(port, path, body=None):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                 headers={"Content-Type": "application/json"}, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, r.read(), r.headers.get("Content-Type")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type")


@pytest.fixture
def server():
    d, rec, c = make()
    port = _free_port()
    httpd = serve(d, port=port)
    assert httpd is not None
    yield d, port
    httpd.shutdown()
    httpd.server_close()


def test_demo_page_and_api(server):
    d, port = server
    code, body, ctype = _req(port, "/")
    assert code == 200 and ctype.startswith("text/html")
    html = body.decode()
    assert 'id="demo-card"' in html and "Clear demo" in html and "isr-ui.css?v=2" in html
    assert _req(port, "/static/isr-ui.css")[0] == 200 and _req(port, "/static/fonts/ibm-plex-mono-400.woff2")[0] == 200
    assert _req(port, "/static/../demo.py")[0] == 404 and _req(port, "/static/%2e%2e/demo.py")[0] == 404
    assert json.loads(_req(port, "/api/demo/scenarios")[1])["mode"] == "on_demand"
    assert _req(port, "/api/demo/trigger", {"id": "nope"})[0] == 400
    assert _req(port, "/api/demo/trigger", b"not json")[0] == 400
    code, body, _ = _req(port, "/api/demo/trigger", {"id": "patrol"})
    assert code == 200 and json.loads(body)["ok"]
    st = json.loads(_req(port, "/api/status")[1])
    assert st["demo_active"] == ["patrol"] and len(st["units"]) == 3 and st["demo_mode"] == "on_demand"
    assert json.loads(_req(port, "/api/demo/clear", {})[1]) == {"ok": True, "cleared": 1, "tracks": 3}


def test_demo_port_zero_turns_the_page_off(monkeypatch):
    monkeypatch.setenv("DEMO_PORT", "0")
    d, _, _ = make()
    assert serve(d) is None


def test_page_text_is_sentence_case():
    html = (demo_mod.STATIC_DIR / "index.html").read_text()
    assert "Blue force tracker" in html and "ISR" not in html.replace("isr-", "").replace("ISR.", "")


# ── modes at start-up ─────────────────────────────────────────────

def _bft_main(monkeypatch, mode):
    ran = {"run": 0, "served": 0}
    monkeypatch.setattr(bft_sim.BftSimulator, "run", lambda self: ran.__setitem__("run", 1))
    monkeypatch.setattr(demo_mod, "serve", lambda demo, **k: ran.__setitem__("served", 1))
    monkeypatch.setattr(bft_sim.time, "sleep", lambda s: (_ for _ in ()).throw(KeyboardInterrupt()))
    if mode:
        monkeypatch.setenv("DEMO_MODE", mode)
    else:
        monkeypatch.delenv("DEMO_MODE", raising=False)
    monkeypatch.setattr(DemoController, "start", lambda self: self)
    bft_sim.main([])
    return ran


def test_continuous_bft_sim_runs_its_units_as_before(monkeypatch):
    assert _bft_main(monkeypatch, None) == {"run": 1, "served": 1}


def test_on_demand_bft_sim_runs_no_units(monkeypatch):
    assert _bft_main(monkeypatch, "on_demand") == {"run": 0, "served": 1}


def test_simulated_meshbus_sends_no_fake_texts_on_demand(monkeypatch):
    from bridge import mqtt_shim

    monkeypatch.setenv("HEARTBEAT", "0")
    on = mqtt_shim.SimulatedMeshbus("127.0.0.1", 1, "u", "p", demo=DemoController(CENTRE, mode="on_demand"))
    cont = mqtt_shim.SimulatedMeshbus("127.0.0.1", 1, "u", "p", demo=DemoController(CENTRE, mode="continuous"))
    legacy = mqtt_shim.SimulatedMeshbus("127.0.0.1", 1, "u", "p")
    assert not on.fake_texts and cont.fake_texts and legacy.fake_texts
    for s in (on, cont, legacy):
        s._mqtt.loop_stop()


def test_heartbeat_extra_names_the_demo(monkeypatch):
    from bridge import mqtt_shim

    monkeypatch.setenv("ISR_HOST", "isr-pi")
    d, _, _ = make()
    d.trigger("patrol")
    extra = mqtt_shim._demo_extra(d)
    assert extra == {"demo_mode": "on_demand", "demo_active": ["patrol"], "demo_url": "http://isr-pi.local:8170/"}
    assert d.heartbeat_text() == "; demo on_demand: patrol (SIMULATED)"
    assert mqtt_shim._demo_extra(None) == {}
    assert DemoController(CENTRE, mode="continuous").heartbeat_text() == ""
