"""Contract C4.2-C4.4 for blufor-tracker's --simulate BFT CoT."""

import xml.etree.ElementTree as ET

from bridge import mesh_bridge
from bridge.cot_converter import label_simulated, meshtastic_to_cot_xml


def pli():
    return meshtastic_to_cot_xml({"fromId": "Alpha1", "_callsign": "Alpha1", "decoded": {
        "portnum": "POSITION_APP", "position": {"latitude": 51.15, "longitude": -1.75, "altitude": 100}}}, {})


def chat():
    return meshtastic_to_cot_xml({"fromId": "Alpha1", "toId": "^all", "_callsign": "Alpha1",
                                  "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "In position"}}, {})


def test_simulated_pli_gets_remarks():
    ev = ET.fromstring(label_simulated(pli()))
    assert ev.find("detail/remarks").text == "SIMULATED: simulated BFT position"


def test_simulated_chat_remarks_prefixed_once():
    xml = label_simulated(label_simulated(chat()))
    rem = ET.fromstring(xml).find("detail/remarks").text
    assert rem.startswith("SIMULATED: ") and rem.count("SIMULATED") == 1 and "In position" in rem


def test_sim_centre_uses_shared_op_position(monkeypatch):
    from bridge import bft_sim

    monkeypatch.setenv("SENSOR_LAT", "51.2")
    monkeypatch.setenv("SENSOR_LON", "-1.8")
    assert bft_sim.sim_centre() == (51.2, -1.8)
    monkeypatch.setenv("SENSOR_LAT", "")
    assert bft_sim.sim_centre() == bft_sim.DEFAULT_CENTRE
    monkeypatch.setenv("SENSOR_LAT", "0")
    monkeypatch.setenv("SENSOR_LON", "0")
    assert bft_sim.sim_centre() == bft_sim.DEFAULT_CENTRE  # never 0,0


class Recorder:
    """Stands in for TakClient.send so the simulator can run on a fake clock."""

    def __init__(self):
        from bridge.tak_client import TakClient

        self.client = TakClient("blufor-tracker", "BFT-SIM", host="127.0.0.1", port=1, station="isr-pi",
                                sensor_lat=51.15, sensor_lon=-1.75)
        self.sent = []
        self.client.send = self.sent.append


def test_simulated_units_are_labelled_storable_markers_at_most_every_5s():
    import random

    from bridge import bft_sim

    rec = Recorder()
    sim = bft_sim.BftSimulator(client=rec.client, centre=(51.15, -1.75), rng=random.Random(1))
    last = {}
    for step in range(600):  # 10 minutes at 1 s
        now = 1000.0 + step
        xml = sim.tick(now)
        if xml is None:
            continue
        ev = ET.fromstring(xml)
        uid = ev.get("uid")
        assert uid.startswith("SIM-BFT-") and ev.get("type") == "a-f-G-U-C"
        assert ev.find("detail/contact").get("callsign").startswith("SIM-")
        assert ev.find("detail/contact").get("endpoint") is None and ev.find("detail/takv") is None
        assert ev.find("detail/link").get("uid") == "isrkit-blufor-tracker-isr-pi"
        assert ev.find("detail/remarks").text.startswith("SIMULATED: ")
        assert abs(float(ev.find("point").get("lat")) - 51.15) < 0.1
        assert uid not in last or now - last[uid] >= 5.0
        last[uid] = now
    assert len(last) == len(bft_sim.UNITS) and sim.sent >= 100


def test_bft_sim_through_fake_ots_stores_units_without_fake_euds(monkeypatch):
    import time

    from bridge import bft_sim
    from bridge.tak_client import TakClient
    from tests.fake_ots import FakeOTS

    monkeypatch.setenv("TAK_REGISTER_DELAY_S", "0.4")
    ots = FakeOTS(bind_delay=0.3)
    got = []
    atak = TakClient("atak", "Bravo2", port=ots.port, station="phone", callsign="Bravo2", on_event=got.append)
    atak.start()
    sim = bft_sim.BftSimulator(client=TakClient("blufor-tracker", "BFT-SIM", port=ots.port, station="isr-pi",
                                                sensor_lat=51.15, sensor_lon=-1.75), centre=(51.15, -1.75))
    sim.client.start()
    try:
        assert atak.wait_registered(3) and sim.client.wait_registered(3)
        for i in range(5):
            sim.tick(1000.0 + i * 10)
        end = time.time() + 5
        while time.time() < end and sum("SIM-BFT-" in e for e in got) < 5:
            time.sleep(0.05)
        assert sum("SIM-BFT-" in e for e in got) == 5
        assert set(ots.euds) == {"isrkit-atak-phone", "isrkit-blufor-tracker-isr-pi"}  # no fake-unit EUDs
        assert ots.euds["isrkit-atak-phone"] == "Bravo2"  # a real "Bravo2" keeps its callsign
        sent_uids = {uid for uid in ots.relayed if uid.startswith("SIM-BFT-")}
        assert set(ots.markers) == sent_uids and not ots.marker_errors and not ots.dropped
    finally:
        sim.client.stop()
        atak.stop()
        ots.close()


def test_legacy_bridge_refuses_without_flag(monkeypatch, capsys):
    import pytest

    from bridge import mesh_bridge

    monkeypatch.setattr("sys.argv", ["bridge"])
    with pytest.raises(SystemExit) as e:
        mesh_bridge.main()
    assert e.value.code == 2 and "LEGACY" in capsys.readouterr().err
