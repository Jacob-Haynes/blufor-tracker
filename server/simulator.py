import logging
import math
import random
import time

from server.models import MeshLink, Message, PositionReport, Report, SOSAlert, Waypoint
from server.state import (
    mesh_topology_store,
    message_store,
    report_store,
    sos_store,
    store,
    waypoint_store,
)

logger = logging.getLogger(__name__)

CALLSIGNS = ["ALPHA-1", "BRAVO-2", "CHARLIE-3", "DELTA-4", "ECHO-5"]

# Start near Honourable Artillery Company, EC1Y London
BASE_LAT = 51.5225
BASE_LON = -0.0865

CANNED_MESSAGES = [
    "ROGER",
    "WILCO",
    "ACK",
    "SAY AGAIN",
    "WAIT OUT",
    "NOTHING HEARD",
    "RADIO CHECK",
    "LOUD AND CLEAR",
    "MOVING NOW",
    "IN POSITION",
    "SET",
    "COMPLETE",
    "SITREP FOLLOWS",
    "ALL CLEAR",
    "CONTACT - WAIT OUT",
    "AMMO STATE AMBER",
    "OUT",
]

SOS_REASONS = [
    "SOS - Taking fire, need immediate support",
    "SOS - Vehicle disabled, requesting extraction",
    "PANIC - Man down, need medic",
    "SOS - Lost comms with squad, need assistance",
]

STARTUP_WAYPOINTS = [
    {
        "name": "RP Alpha",
        "lat": BASE_LAT + 0.002,
        "lon": BASE_LON - 0.001,
        "waypoint_type": "rv",
        "icon": "🔵",
        "description": "Rally point Alpha - primary RV",
    },
    {
        "name": "OBJ Hotel",
        "lat": BASE_LAT - 0.001,
        "lon": BASE_LON + 0.002,
        "waypoint_type": "objective",
        "icon": "🎯",
        "description": "Objective Hotel - secure building",
    },
    {
        "name": "CKP 1",
        "lat": BASE_LAT + 0.001,
        "lon": BASE_LON + 0.001,
        "waypoint_type": "checkpoint",
        "icon": "✓",
        "description": "Checkpoint 1 - route marker",
    },
    {
        "name": "DANGER",
        "lat": BASE_LAT - 0.0015,
        "lon": BASE_LON - 0.0015,
        "waypoint_type": "danger",
        "icon": "⚠️",
        "description": "Known hazard area - avoid",
    },
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


def _maybe_send_sos(nodes: list[dict]) -> None:
    if random.random() > 0.02:
        return

    node = random.choice(nodes)
    alert = SOSAlert(
        callsign=node["callsign"],
        lat=node["lat"],
        lon=node["lon"],
        message=random.choice(SOS_REASONS),
    )
    sos_store.add(alert)
    logger.info("SOS generated from %s: %s", node["callsign"], alert.message)


def _maybe_send_report() -> None:
    """~1% chance per tick: generate a random SITREP, Contact Report, or Siting Report."""
    if random.random() > 0.01:
        return

    sender = random.choice(CALLSIGNS)
    roll = random.random()
    dtg = time.strftime("%d%H%MZ %b %y", time.gmtime()).upper()
    grid = f"{BASE_LAT + random.uniform(-0.003, 0.003):.5f}, {BASE_LON + random.uniform(-0.003, 0.003):.5f}"

    if roll < 0.4:
        # SITREP
        report = Report(
            report_type="sitrep",
            sender=sender,
            fields={
                "dtg": dtg,
                "unit": sender,
                "location": grid,
                "activity": random.choice(["Patrolling AO", "Holding position", "Moving to objective", "Conducting framework patrol"]),
                "enemy": random.choice(["No enemy seen", "Possible enemy patrol 500m N", "Enemy observed withdrawing E"]),
                "friendly": random.choice(["All callsigns accounted for", "Section strength, no casualties", "Plt(-) in position"]),
                "casualties": random.choice(["Nil", "1 x WIA T3", "Nil own, 2 x enemy KIA"]),
                "ammo": random.choice(["Green", "Amber", "Red"]),
                "actions": random.choice(["Continue patrol", "Hold position", "Request resupply", "Withdraw to PL ALPHA"]),
                "remarks": random.choice(["Nil", "Request CASEVAC standby", "Terrain difficult - dismounted only"]),
            },
        )
    elif roll < 0.7:
        # Contact Report
        report = Report(
            report_type="contact",
            sender=sender,
            fields={
                "dtg": dtg,
                "grid": grid,
                "size": str(random.randint(2, 12)) + " pax",
                "activity": random.choice(["Attacking from N", "Defending compound", "Withdrawing E", "Patrolling S"]),
                "weapon": random.choice(["Small arms", "RPG + small arms", "PKM + AK", "Technical w/ DSHK"]),
                "direction": random.choice(["Moving N to S", "Moving E", "Static", "Withdrawing W"]),
                "own_action": random.choice(["Returning fire", "Taking cover", "Flanking left", "Fire and manoeuvre"]),
                "own_cas": random.choice(["Nil", "1 x WIA", "2 x WIA"]),
                "request": random.choice(["Fire support", "QRF", "CASEVAC", "None"]),
            },
        )
    else:
        # Siting Report
        report = Report(
            report_type="siting",
            sender=sender,
            fields={
                "dtg": dtg,
                "grid": grid,
                "what": random.choice(["2 x pax digging", "Vehicle convoy 3 x trucks", "Possible OP on high ground", "4 x pax with packs moving along treeline"]),
                "size": random.choice(["2 pax", "3 vehicles", "4-6 pax", "1 x technical"]),
                "activity": random.choice(["Stationary", "Moving E to W", "Digging in", "Observing from ridgeline"]),
                "direction": random.choice(["N/A - static", "Moving east", "Moving south", "Withdrawing north"]),
                "weapons": random.choice(["Not visible", "Small arms", "Unknown", "RPG sighted"]),
                "remarks": random.choice(["Possible recce", "Low threat", "Assess as enemy patrol", "Civilian - confirm"]),
            },
        )

    report_store.add(report)
    logger.info("Report generated from %s: %s", sender, report.report_type)


def _update_mesh_topology(nodes: list[dict]) -> None:
    """Generate fake link data between simulated nodes."""
    for i, n1 in enumerate(nodes):
        for j, n2 in enumerate(nodes):
            if i >= j:
                continue
            link = MeshLink(
                from_node=n1["callsign"],
                to_node=n2["callsign"],
                snr=random.uniform(-5.0, 12.0),
                rssi=random.randint(-120, -60),
                hop_count=random.choice([1, 1, 1, 2, 2, 3]),
                last_seen=time.time(),
            )
            mesh_topology_store.update_link(link)


def _create_startup_waypoints() -> None:
    for wp_data in STARTUP_WAYPOINTS:
        wp = Waypoint(**wp_data)
        waypoint_store.add(wp)
    logger.info("Created %d startup waypoints", len(STARTUP_WAYPOINTS))


def run_simulator(interval: float = 2.0) -> None:
    _create_startup_waypoints()

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
    topology_tick = 0

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
        _maybe_send_sos(nodes)
        _maybe_send_report()

        # Update mesh topology every 10 ticks
        topology_tick += 1
        if topology_tick >= 10:
            _update_mesh_topology(nodes)
            topology_tick = 0

        time.sleep(interval)
