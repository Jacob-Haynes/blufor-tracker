import logging
import math
import random
import time

from server.models import Message, PositionReport
from server.state import message_store, store

logger = logging.getLogger(__name__)

CALLSIGNS = ["ALPHA-1", "BRAVO-2", "CHARLIE-3", "DELTA-4", "ECHO-5"]

# Start near Honourable Artillery Company, EC1Y London
BASE_LAT = 51.5225
BASE_LON = -0.0865

CANNED_MESSAGES = [
    "Position secured",
    "Moving to checkpoint",
    "Copy that",
    "All clear",
    "Holding position",
    "Request status update",
    "En route",
    "Eyes on objective",
    "Standing by",
    "Returning to base",
]


def _random_walk(
    lat: float, lon: float, heading: float, dt: float
) -> tuple[float, float, float, float]:
    speed_kmh = random.uniform(1.0, 5.0)
    heading += random.gauss(0, 15)
    heading %= 360

    speed_ms = speed_kmh / 3.6
    dist = speed_ms * dt

    dlat = dist * math.cos(math.radians(heading)) / 111320
    dlon = dist * math.sin(math.radians(heading)) / (
        111320 * math.cos(math.radians(lat))
    )

    return lat + dlat, lon + dlon, speed_kmh, heading


def _maybe_send_message() -> None:
    if random.random() > 0.10:
        return

    sender = random.choice(CALLSIGNS)
    roll = random.random()
    if roll < 0.4:
        channel = "BROADCAST"
    elif roll < 0.7:
        recipient = random.choice([c for c in CALLSIGNS if c != sender])
        channel = recipient
    else:
        channel = "HQ"

    msg = Message(
        sender=sender,
        channel=channel,
        body=random.choice(CANNED_MESSAGES),
    )
    message_store.add(msg)


def run_simulator(interval: float = 2.0) -> None:
    nodes: list[dict] = []
    for i, cs in enumerate(CALLSIGNS):
        nodes.append(
            {
                "callsign": cs,
                "lat": BASE_LAT + random.uniform(-0.002, 0.002),
                "lon": BASE_LON + random.uniform(-0.002, 0.002),
                "heading": random.uniform(0, 360),
                "battery": random.uniform(70, 100),
                "altitude": random.uniform(80, 120),
            }
        )

    logger.info("Simulator started with %d nodes", len(nodes))
    stale_index = len(nodes) - 1
    stale_after = time.time() + 30

    while True:
        now = time.time()
        for i, node in enumerate(nodes):
            if i == stale_index and now > stale_after:
                continue

            node["lat"], node["lon"], speed, node["heading"] = _random_walk(
                node["lat"], node["lon"], node["heading"], interval
            )
            node["battery"] = max(0, node["battery"] - random.uniform(0, 0.05))

            report = PositionReport(
                callsign=node["callsign"],
                lat=node["lat"],
                lon=node["lon"],
                altitude=node["altitude"],
                timestamp=now,
                battery=node["battery"],
                speed=speed,
                heading=node["heading"],
            )
            store.update(report)

        _maybe_send_message()
        time.sleep(interval)
