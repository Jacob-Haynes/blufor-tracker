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
    monkeypatch.setenv("SENSOR_LAT", "51.2")
    monkeypatch.setenv("SENSOR_LON", "-1.8")
    assert mesh_bridge.sim_centre() == (51.2, -1.8)
    monkeypatch.setenv("SENSOR_LAT", "")
    assert mesh_bridge.sim_centre() == mesh_bridge.SIM_DEFAULT_CENTRE
    monkeypatch.setenv("SENSOR_LAT", "0")
    monkeypatch.setenv("SENSOR_LON", "0")
    assert mesh_bridge.sim_centre() == mesh_bridge.SIM_DEFAULT_CENTRE  # never 0,0


def test_simulator_rate_and_labels(monkeypatch):
    """Run the simulator loop on a fake clock: every CoT is labelled, <=1 update per 5 s per unit."""
    sent, clock = [], {"t": 1000.0}
    monkeypatch.setattr(mesh_bridge.time, "time", lambda: clock["t"])

    def fake_sleep(s):
        clock["t"] += s
        if clock["t"] > 1600:
            bridge._running = False

    monkeypatch.setattr(mesh_bridge.time, "sleep", fake_sleep)
    bridge = mesh_bridge.MeshBridge.__new__(mesh_bridge.MeshBridge)
    bridge._running = True
    bridge._send_to_tak = lambda xml: sent.append((clock["t"], xml))
    bridge._simulator_loop()
    assert sent
    last = {}
    for t, xml in sent:
        ev = ET.fromstring(xml)
        assert ev.find("detail/remarks").text.startswith("SIMULATED: ")
        uid = ev.get("uid")
        if ev.get("type") == "a-f-G-U-C":
            assert uid not in last or t - last[uid] >= 5.0
            last[uid] = t
            assert abs(float(ev.find("point").get("lat")) - 51.15) < 0.1
