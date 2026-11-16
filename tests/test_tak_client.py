"""tak_client (contract v5) against a fake OTS enforcing the FK drop, bind delay and unique callsign."""

import time
import xml.etree.ElementTree as ET

import pytest

from bridge.tak_client import TakClient, build_marker, build_sa
from tests.fake_ots import FakeOTS

BIND = 0.3


@pytest.fixture
def ots():
    o = FakeOTS(bind_delay=BIND)
    yield o
    o.close()


def client(ots, repo, short, delay=BIND + 0.2, **kw):
    kw.setdefault("station", "isr-pi")
    return TakClient(repo, short, host="127.0.0.1", port=ots.port, register_delay=delay, **kw)


def until(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def marker(uid="DRONE-SIM-1"):
    t = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return (f'<event version="2.0" uid="{uid}" type="a-h-A-M-F-Q" how="m-g" time="{t}" start="{t}" '
            f'stale="{t}"><point lat="51.15" lon="-1.75" hae="0" ce="10" le="10"/><detail/></event>')


def test_sa_contract_with_and_without_position():
    ev = ET.fromstring(build_sa("isrkit-x-isr-pi", "X isr-pi", "x", 51.15, -1.75, now=1000))
    assert ev.get("type") == "a-f-G-E-S" and ev.get("uid") == "isrkit-x-isr-pi"
    assert ev.get("start").endswith("Z") and ev.get("stale") > ev.get("start")
    assert ev.find("detail/contact").get("endpoint") == "*:-1:stcp"
    assert ev.find("detail/__group").get("name") == "Cyan"
    assert ev.find("detail/takv").get("platform") == "isr-kit" and ev.find("detail/takv").get("device") == "x"
    assert float(ev.find("point").get("lat")) == pytest.approx(51.15)
    nopos = ET.fromstring(build_sa("u", "c", "x", None, None, now=1000))
    p = nopos.find("point")
    assert (p.get("lat"), p.get("lon"), p.get("ce"), p.get("hae")) == ("0", "0", "9999999", "9999999")
    assert nopos.get("stale") == nopos.get("start")  # never drawn


def test_identity_from_station(monkeypatch):
    monkeypatch.setenv("ISR_HOST", "isr-hq")
    c = TakClient("drone-detector", "DRONE-DET", host="127.0.0.1", port=1)
    assert c.uid == "isrkit-drone-detector-isr-hq" and c.callsign == "DRONE-DET isr-hq"


def test_markers_reach_a_registered_listener(ots):
    got = []
    rx = client(ots, "rx", "RX", on_event=got.append).start()
    assert rx.wait_registered(3)
    tx = client(ots, "tx", "TX").start()
    tx.send(marker())  # queued before registration, sent after it
    assert until(lambda: any("DRONE-SIM-1" in e for e in got))
    assert "isrkit-tx-isr-pi" in ots.euds and not ots.dropped
    assert any("isrkit-tx-isr-pi" in e for e in got)  # the sender's SA is relayed too
    rx.stop(), tx.stop()


def test_without_registration_markers_are_dropped(ots):
    """What the kit did before v5: a bare socket sending markers. OTS drops every one."""
    import socket

    s = socket.create_connection(("127.0.0.1", ots.port))
    s.sendall(marker("DRONE-AC1").encode())
    assert until(lambda: "DRONE-AC1" in ots.dropped)
    s.close()


def test_registering_too_early_means_never_receiving(ots):
    got = []
    deaf = client(ots, "rx", "RX", delay=0.0, on_event=got.append).start()
    assert deaf.wait_registered(3)
    tx = client(ots, "tx", "TX").start()
    tx.send(marker())
    assert until(lambda: "DRONE-SIM-1" in ots.relayed)
    time.sleep(0.3)
    assert got == []
    deaf.stop(), tx.stop()


def test_callsign_clash_drops_the_second_tool(ots):
    a = client(ots, "a", "SAME").start()
    assert until(lambda: "isrkit-a-isr-pi" in ots.euds)
    b = client(ots, "b", "SAME").start()
    b.send(marker("DRONE-B"))
    assert until(lambda: "isrkit-b-isr-pi" in ots.dropped)
    assert "isrkit-b-isr-pi" not in ots.euds
    a.stop(), b.stop()


def test_reconnects_and_registers_again(ots):
    got = []
    rx = client(ots, "rx", "RX", on_event=got.append).start()
    tx = client(ots, "tx", "TX").start()
    assert rx.wait_registered(3) and tx.wait_registered(3)
    ots.drop_all_connections()
    assert until(lambda: not tx.registered.is_set(), 3)
    assert until(lambda: rx.registered.is_set() and tx.registered.is_set(), 10)
    tx.send(marker("DRONE-AFTER"))
    assert until(lambda: any("DRONE-AFTER" in e for e in got), 5)
    rx.stop(), tx.stop()


def test_sa_resent_periodically(ots):
    got = []
    rx = client(ots, "rx", "RX", on_event=got.append).start()
    assert rx.wait_registered(3)
    tx = client(ots, "tx", "TX", sa_interval=0.3).start()
    assert until(lambda: sum("isrkit-tx-isr-pi" in e for e in got) >= 3, 5)
    rx.stop(), tx.stop()


def test_build_marker_fields_and_no_position():
    ev = ET.fromstring(build_marker("DRONE-1", "a-h-A-M-F-Q", "UAV 1", 51.15, -1.75, now=1000,
                                    remarks="SIMULATED: x", parent_uid="isrkit-x-isr-pi",
                                    parent_callsign="X isr-pi", speed=3, course=90, argb=-65536))
    assert ev.find("detail/contact").get("endpoint") is None and ev.find("detail/takv") is None
    assert ev.find("detail/link").get("uid") == "isrkit-x-isr-pi"
    assert ev.find("detail/remarks").text == "SIMULATED: x"
    assert ev.find("detail/color").get("argb") == "-65536" and ev.find("detail/track").get("course") == "90.0"
    assert ev.get("stale") > ev.get("start")
    assert build_marker("u", "a-u-G", "c", None, None) is None
    assert build_marker("u", "a-u-G", "c", 0.0, 0.0) is None  # never a marker at 0,0


def test_markers_are_stored_by_ots_only_with_position_and_eud_parent(ots):
    tx = client(ots, "tx", "TX", sensor_lat=51.15, sensor_lon=-1.75).start()
    assert tx.wait_registered(3)
    tx.send(tx.marker("DRONE-OK", "a-h-A-M-F-Q", "UAV OK", 51.151, -1.751))
    tx.send(marker("DRONE-NOPOS").replace('lat="51.15" lon="-1.75"', 'lat="0" lon="0"'))
    tx.send(build_marker("DRONE-BADLINK", "a-u-G", "bad link", 51.15, -1.75, parent_uid="DRONE-OK"))
    assert until(lambda: len(ots.markers) + len(ots.marker_errors) >= 3)
    assert ots.markers == {"DRONE-OK": "UAV OK"}
    assert any("DRONE-NOPOS" in e for e in ots.marker_errors)
    assert any("DRONE-BADLINK" in e and "parent_uid" in e for e in ots.marker_errors)
    tx.stop()


def test_ots_fit_cuts_varchar_fields_and_warns_once(caplog):
    import logging

    from bridge import tak_client as tc

    tc._warned_fields.discard("marker callsign")
    with caplog.at_level(logging.WARNING, logger="tak_client"):
        a = ET.fromstring(build_marker("u1", "a-u-G", "C" * 300, 51.15, -1.75))
        b = ET.fromstring(build_marker("u2", "a-u-G", "&" * 300, 51.15, -1.75))
    cs = a.find("detail/contact").get("callsign")
    assert len(cs) == tc.OTS_FIELD_MAX and cs.endswith("…")
    assert len(b.find("detail/contact").get("callsign")) * 5 <= tc.OTS_FIELD_MAX  # "&amp;" counted
    assert sum("marker callsign" in r.getMessage() for r in caplog.records) == 1
    assert tc.ots_fit("short", "x") == "short" and tc.ots_fit(None, "x") is None


def test_long_marker_remarks_are_not_cut_and_still_stored(ots):
    """OTS 1.8.3 doesn't store marker <remarks>, so long remarks are fine as they are."""
    tx = client(ots, "tx", "TX", sensor_lat=51.15, sensor_lon=-1.75).start()
    assert tx.wait_registered(3)
    xml = tx.marker("DRONE-LONG", "a-h-A-M-F-Q", "UAV LONG", 51.151, -1.751, remarks="SIMULATED: " + "r" * 600)
    assert len(ET.fromstring(xml).find("detail/remarks").text) > 600
    tx.send(xml)
    assert until(lambda: "DRONE-LONG" in ots.markers)
    assert not ots.nacked
    tx.stop()
