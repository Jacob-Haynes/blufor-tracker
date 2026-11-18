"""Simulated Blue Force Tracker units on ATAK, the contract-v5 way (``python -m bridge --simulate``).

No radio. One ``bridge.tak_client`` connection registers as ``isrkit-blufor-tracker-<ISR_HOST>``
(callsign ``BFT-SIM <ISR_HOST>``) at the shared OP position. The fake units are sent as plain
**markers** on that connection (uid ``SIM-BFT-<name>``, callsign ``SIM-<name>``, type
``a-f-G-U-C``), not as EUDs:

* no ``<takv>`` and no ``<contact endpoint>``, so OTS never takes a fake unit for a device and
  its callsign can't collide with a real ATAK user's ("Bravo2" vs "SIM-Bravo2");
* a real position and a ``<link>`` to the registered uid, so OTS stores them (web map) as well
  as relaying them;
* remarks start ``SIMULATED: `` (C4.3), units drift around ``SENSOR_LAT``/``SENSOR_LON``
  (default Salisbury Plain, never 0,0; C4.2) and each updates at most every 5 s (C4.4).

``DEMO_MODE=on_demand`` (step-by-step demo) runs none of these units: the process only serves the
Demo page and API (``bridge.demo``, port ``DEMO_PORT``, default 8170), where the presenter fires the
``patrol`` scenario. ``DEMO_MODE=continuous`` (default) runs them as before, with the Demo page too.

The units' uids are fixed, so they are withdrawn from ATAK at once (stale = now, then a ``t-x-d-d``
forcedelete, then the registration SA) when the simulator stops (SIGTERM or Ctrl-C) and at every
start in on_demand mode, rather than staying on the map until stale.

This replaces the legacy pytak simulator in ``mesh_bridge.py``, whose connection registered as
its first fake unit and whose opening ``t-x-d-d`` OTS dropped.
"""

from __future__ import annotations

import logging
import os
import random
import signal
import sys
import time

from bridge.tak_client import TakClient, build_delete

logger = logging.getLogger("bft_sim")

UNITS = ["Alpha1", "Bravo2", "Charlie3", "Delta4", "Echo5"]
MIN_UPDATE_S = 5.0
STALE_S = 120.0
DEFAULT_CENTRE = (51.15, -1.75)  # Salisbury Plain, same as the other tools' simulators
UID_PREFIX = "SIM-BFT-"
SHUTDOWN_FLUSH_S = 3.0


def sim_centre() -> tuple[float, float]:
    """Shared OP position from SENSOR_LAT/SENSOR_LON, else the default demo site (never 0,0)."""
    try:
        lat = float(os.environ.get("SENSOR_LAT", "").strip())
        lon = float(os.environ.get("SENSOR_LON", "").strip())
        if lat or lon:
            return lat, lon
    except ValueError:
        pass
    return DEFAULT_CENTRE


class BftSimulator:
    def __init__(self, client: TakClient | None = None, centre: tuple[float, float] | None = None,
                 rng: random.Random | None = None, host: str | None = None, port: int | None = None):
        self.centre = centre or sim_centre()
        self.client = client or TakClient("blufor-tracker", "BFT-SIM", host=host, port=port,
                                          sensor_lat=self.centre[0], sensor_lon=self.centre[1])
        self.rng = rng or random.Random()
        lat0, lon0 = self.centre
        self.positions = {u: (lat0 + self.rng.uniform(-0.01, 0.01), lon0 + self.rng.uniform(-0.02, 0.02))
                          for u in UNITS}
        self.last_sent: dict[str, float] = {}
        self.sent = 0

    def marker(self, unit: str, now: float) -> str:
        lat, lon = self.positions[unit]
        lat += self.rng.uniform(-0.0005, 0.0005)
        lon += self.rng.uniform(-0.0005, 0.0005)
        self.positions[unit] = (lat, lon)
        return self.client.marker(
            f"{UID_PREFIX}{unit}", "a-f-G-U-C", f"SIM-{unit}", lat, lon, now=now, stale_s=STALE_S,
            hae=float(self.rng.randint(80, 200)), remarks="SIMULATED: simulated BFT position",
            speed=self.rng.uniform(0, 4), course=self.rng.uniform(0, 360), location_source="GPS")

    def tick(self, now: float | None = None) -> str | None:
        """Send one due unit update (at most one per unit per MIN_UPDATE_S). Returns the CoT sent."""
        now = time.time() if now is None else now
        ready = [u for u in UNITS if now - self.last_sent.get(u, -1e9) >= MIN_UPDATE_S]
        if not ready:
            return None
        unit = self.rng.choice(ready)
        self.last_sent[unit] = now
        xml = self.marker(unit, now)
        self.client.send(xml)
        self.sent += 1
        logger.info("SIM position: %s @ %.5f, %.5f", unit, *self.positions[unit])
        return xml

    def withdraw(self, why: str, now: float | None = None) -> int:
        """Take the drifting units off ATAK now (their uids are fixed, so this also works for a
        previous run's): each marker again with stale = now, then a ``t-x-d-d`` forcedelete, then
        the registration SA, queued after them (OTS marks the sender disconnected on t-x-d-d).
        Returns the number of deletes queued."""
        now = time.time() if now is None else now
        markers, deletes = [], []
        for unit in UNITS:
            lat, lon = self.positions[unit]
            uid = f"{UID_PREFIX}{unit}"
            xml = self.client.marker(uid, "a-f-G-U-C", f"SIM-{unit}", lat, lon, now=now, stale_s=0.0,
                                     remarks=f"SIMULATED: simulated BFT position ({why})")
            delete = build_delete(uid, "a-f-G-U-C", *self.centre, now=now, remarks=f"SIMULATED: {why}")
            if xml and delete:
                markers.append(xml)
                deletes.append(delete)
        for xml in markers + deletes:
            self.client.send(xml)
        if deletes:
            self.client.send(self.client.sa(now))
        self.client.start()
        logger.info("Withdrew %d simulated BFT units from TAK: %s", len(deletes), why)
        return len(deletes)

    def run(self):
        self.client.start()
        logger.info("BFT simulator: %d units around %.5f, %.5f as %s (%s) -> %s:%d", len(UNITS),
                    *self.centre, self.client.uid, self.client.callsign, self.client.host, self.client.port)
        while True:
            self.tick()
            time.sleep(1.0)


def main(argv=None):
    import argparse

    logging.basicConfig(force=True, level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    p = argparse.ArgumentParser(description="Simulated BFT units on ATAK via OpenTAKServer (no radio)")
    p.add_argument("--simulate", action="store_true", help=argparse.SUPPRESS)  # `python -m bridge --simulate`
    p.add_argument("--tak-host", default=None, help="TAK server host (env TAK_HOST, default 127.0.0.1)")
    p.add_argument("--tak-port", type=int, default=None, help="TAK server CoT port (env TAK_PORT, default 8087)")
    args, _ = p.parse_known_args(argv)
    from bridge.demo import DemoController, demo_mode, serve

    # systemd/kill stop us with SIGTERM: exit through the finally below, which withdraws the units
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    mode = demo_mode()
    sim = BftSimulator(host=args.tak_host, port=args.tak_port)
    demo = DemoController(sim.centre, client=sim.client, mode=mode).start()
    serve(demo)
    try:
        if mode == "continuous":
            sim.run()
        else:
            logger.info("DEMO_MODE=on_demand: no simulated units until a scenario is fired from the Demo page")
            # a previous continuous run's units would stay on ATAK until stale (2 min)
            sim.withdraw("step-by-step demo started")
            while True:
                time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        shutdown(sim, demo, mode)


def shutdown(sim: BftSimulator, demo, mode: str, flush_s: float = SHUTDOWN_FLUSH_S):
    """On stop: Clear demo, and in continuous mode withdraw the drifting units, then give the TAK
    client up to ``flush_s`` to send the deletes before the process exits."""
    try:
        demo.stop()
        if demo.clear()["tracks"] + (sim.withdraw("simulator stopped") if mode == "continuous" else 0):
            if not sim.client.flush(flush_s):
                logger.warning("TAK server not reachable: simulated units expire when stale instead")
    except Exception:
        logger.exception("Withdrawing simulated units at shutdown failed")
    finally:
        sim.client.stop()


if __name__ == "__main__":
    main()
