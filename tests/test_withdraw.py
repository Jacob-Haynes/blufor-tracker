"""The --simulate drifting units (and a running patrol) come off ATAK at once on stop, and a start
in on_demand mode withdraws whatever an earlier continuous run left, by their fixed uids."""

import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from bridge import bft_sim
from bridge.bft_sim import UNITS, BftSimulator, shutdown
from bridge.demo import DemoController
from bridge.tak_client import TakClient
from tests.fake_ots import FakeOTS

CENTRE = (51.2, -1.8)
WANT = {f"SIM-BFT-{u}" for u in UNITS}


class Recorder(TakClient):
    def __init__(self):
        super().__init__("blufor-tracker", "BFT-SIM", station="test", sensor_lat=CENTRE[0], sensor_lon=CENTRE[1])
        self.out, self.stopped, self.flushed = [], 0, 0

    def send(self, xml):
        self.out.append(xml)

    def start(self):
        return self

    def stop(self):
        self.stopped += 1

    def flush(self, timeout=5.0):
        self.flushed += 1
        return True


def test_withdraw_sends_stale_markers_then_deletes_then_sa():
    rec = Recorder()
    sim = BftSimulator(client=rec, centre=CENTRE)
    assert sim.withdraw("simulator stopped", now=1_000_000.0) == 5
    ev = [ET.fromstring(x) for x in rec.out]
    markers, deletes, sa = ev[:5], ev[5:10], ev[10:]
    assert {e.get("uid") for e in markers} == WANT and all(e.get("stale") == e.get("time") for e in markers)
    assert all(e.find("detail/remarks").text.startswith("SIMULATED: ") for e in markers)
    assert all(e.get("type") == "t-x-d-d" and e.find("detail/__forcedelete") is not None for e in deletes)
    assert {e.find("detail/link").get("uid") for e in deletes} == WANT
    assert [e.get("uid") for e in sa] == [rec.uid], "SA last: OTS marks us disconnected on t-x-d-d"


def test_shutdown_in_continuous_withdraws_the_units_and_flushes():
    rec = Recorder()
    sim = BftSimulator(client=rec, centre=CENTRE)
    demo = DemoController(CENTRE, client=rec, mode="continuous")
    shutdown(sim, demo, "continuous")
    links = {ET.fromstring(x).find("detail/link").get("uid") for x in rec.out if "t-x-d-d" in x}
    assert links == WANT and rec.flushed == 1 and rec.stopped == 1


def test_shutdown_in_on_demand_clears_a_running_patrol_only():
    rec = Recorder()
    sim = BftSimulator(client=rec, centre=CENTRE)
    demo = DemoController(CENTRE, client=rec, mode="on_demand")
    demo.trigger("patrol")
    rec.out.clear()
    shutdown(sim, demo, "on_demand")
    links = {ET.fromstring(x).find("detail/link").get("uid") for x in rec.out if "t-x-d-d" in x}
    assert links == {"SIM-BFT-Patrol1", "SIM-BFT-Patrol2", "SIM-BFT-Patrol3"} and rec.flushed == 1


def test_demo_shutdown_without_a_patrol_opens_no_tak_connection():
    demo = DemoController(CENTRE, mode="on_demand")
    demo.shutdown()
    assert demo._client is None


def test_demo_shutdown_clears_the_patrol_through_ots():
    ots = FakeOTS(bind_delay=0.1)
    try:
        client = TakClient("blufor-tracker", "BFT-SIM", host="127.0.0.1", port=ots.port, station="isr-pi",
                           register_delay=0.15, sensor_lat=CENTRE[0], sensor_lon=CENTRE[1])
        demo = DemoController(CENTRE, client=client, mode="on_demand")
        demo.trigger("patrol")
        demo.shutdown()
        time.sleep(0.2)
        assert sum("-del-" in u for u in ots.relayed) == 3 and not ots.nacked
        client.stop()
        client._thread.join(2)
    finally:
        ots.close()


def _run_sim(ots_port: int, mode: str, seconds: float) -> None:
    """``python -m bridge --simulate`` as its own process, stopped with SIGTERM."""
    env = {"PATH": "/usr/bin:/bin", "TAK_REGISTER_DELAY_S": "0.2", "DEMO_MODE": mode, "DEMO_PORT": "0",
           "SENSOR_LAT": str(CENTRE[0]), "SENSOR_LON": str(CENTRE[1]), "ISR_HOST": "isr-pi"}
    p = subprocess.Popen([sys.executable, "-m", "bridge", "--simulate", "--tak-host", "127.0.0.1",
                          "--tak-port", str(ots_port)], cwd=Path(__file__).resolve().parents[1], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(seconds)
    p.terminate()
    assert p.wait(10) == 0, "SIGTERM is a normal exit"


def test_sigterm_and_on_demand_restart_withdraw_through_ots():
    ots = FakeOTS(bind_delay=0.1)
    try:
        _run_sim(ots.port, "continuous", 8.0)  # one update per second: all five on the map
        time.sleep(0.2)
        assert {m for m in ots.markers if m.startswith("SIM-BFT-")} == WANT
        stopped = {u.split("-del-")[0] for u in ots.relayed if "-del-" in u}
        assert stopped == WANT and not ots.nacked, "SIGTERM withdrew all five"
        ots.relayed.clear()
        _run_sim(ots.port, "on_demand", 2.0)
        time.sleep(0.2)
        assert {u.split("-del-")[0] for u in ots.relayed if "-del-" in u} == WANT, "on_demand start withdrew them"
    finally:
        ots.close()

