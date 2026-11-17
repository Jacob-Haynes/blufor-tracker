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

This replaces the legacy pytak simulator in ``mesh_bridge.py``, whose connection registered as
its first fake unit and whose opening ``t-x-d-d`` OTS dropped.
"""

from __future__ import annotations

import logging
import os
import random
import time

from bridge.tak_client import TakClient

logger = logging.getLogger("bft_sim")

UNITS = ["Alpha1", "Bravo2", "Charlie3", "Delta4", "Echo5"]
MIN_UPDATE_S = 5.0
STALE_S = 120.0
DEFAULT_CENTRE = (51.15, -1.75)  # Salisbury Plain, same as the other tools' simulators
UID_PREFIX = "SIM-BFT-"


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

    def run(self):
        self.client.start()
        logger.info("BFT simulator: %d units around %.5f, %.5f as %s (%s) -> %s:%d", len(UNITS),
                    *self.centre, self.client.uid, self.client.callsign, self.client.host, self.client.port)
        try:
            while True:
                self.tick()
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
        finally:
            self.client.stop()


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

    mode = demo_mode()
    sim = BftSimulator(host=args.tak_host, port=args.tak_port)
    demo = DemoController(sim.centre, client=sim.client, mode=mode).start()
    serve(demo)
    if mode == "continuous":
        sim.run()
        return
    logger.info("DEMO_MODE=on_demand: no simulated units until a scenario is fired from the Demo page")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        sim.client.stop()


if __name__ == "__main__":
    main()
