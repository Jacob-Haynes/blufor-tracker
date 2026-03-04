import logging
import time

from pubsub import pub

from server.models import Message, PositionReport
from server.state import message_store, store

logger = logging.getLogger(__name__)

_interface = None
_battery_cache: dict[str, float] = {}


def _node_id_to_callsign(node_id: str) -> str:
    if _interface and _interface.nodes:
        node = _interface.nodes.get(node_id)
        if node:
            user = node.get("user", {})
            return user.get("shortName") or user.get("longName") or node_id
    return node_id


def _callsign_to_node_id(callsign: str) -> str | None:
    if not _interface or not _interface.nodes:
        return None
    for node_id, node in _interface.nodes.items():
        user = node.get("user", {})
        if user.get("shortName") == callsign or user.get("longName") == callsign:
            return node_id
    return None


def _get_my_node_id() -> str | None:
    if _interface and _interface.myInfo:
        return _interface.myInfo.my_node_num
    return None


def _on_receive(packet, interface=None):
    try:
        decoded = packet.get("decoded", {})
        portnum = decoded.get("portnum")
        from_id = packet.get("fromId", packet.get("from", "unknown"))
        callsign = _node_id_to_callsign(str(from_id))

        if portnum == "POSITION_APP":
            pos = decoded.get("position", {})
            lat = pos.get("latitude") or pos.get("latitudeI", 0) / 1e7
            lon = pos.get("longitude") or pos.get("longitudeI", 0) / 1e7
            altitude = pos.get("altitude", 0.0)
            speed = pos.get("groundSpeed", None)
            heading = pos.get("groundTrack", None)
            if heading is not None:
                heading = heading / 1e5  # raw value is scaled
            ts = pos.get("time", time.time())

            report = PositionReport(
                callsign=callsign,
                lat=lat,
                lon=lon,
                altitude=altitude,
                timestamp=ts,
                battery=_battery_cache.get(callsign),
                speed=speed,
                heading=heading,
            )
            store.update(report)
            logger.info("Position update: %s @ %.6f, %.6f", callsign, lat, lon)

        elif portnum == "TEXT_MESSAGE_APP":
            text = decoded.get("text", "")
            if not text:
                return
            to_id = packet.get("toId", packet.get("to", ""))
            # Determine channel: broadcast, DM to HQ, or DM to another node
            to_str = str(to_id)
            if to_str in ("^all", "4294967295", "0xffffffff"):
                channel = "BROADCAST"
            else:
                my_node_id = _get_my_node_id()
                if my_node_id is not None and str(to_id) == str(my_node_id):
                    channel = "HQ"
                else:
                    channel = _node_id_to_callsign(to_str)
            msg = Message(sender=callsign, channel=channel, body=text)
            message_store.add(msg)
            logger.info("Message from %s on %s: %s", callsign, channel, text)

        elif portnum == "TELEMETRY_APP":
            telemetry = decoded.get("telemetry", {})
            device_metrics = telemetry.get("deviceMetrics", {})
            battery = device_metrics.get("batteryLevel")
            if battery is not None:
                _battery_cache[callsign] = float(battery)
                logger.info("Battery update: %s = %.0f%%", callsign, battery)

    except Exception:
        logger.exception("Error processing packet")


def send_text(channel: str, body: str) -> bool:
    """Send a text message from HQ out over the mesh.

    Returns True if sent successfully, False otherwise.
    """
    if not _interface:
        logger.warning("Cannot send message: no Meshtastic interface")
        return False
    try:
        if channel == "BROADCAST":
            _interface.sendText(body)
            logger.info("Sent broadcast: %s", body)
        elif channel == "HQ":
            # Message to self — no need to transmit over mesh
            return True
        else:
            # DM to a specific callsign — resolve to node ID
            dest = _callsign_to_node_id(channel)
            if dest is None:
                logger.warning("Cannot resolve callsign %s to node ID", channel)
                return False
            _interface.sendText(body, destinationId=dest)
            logger.info("Sent DM to %s: %s", channel, body)
        return True
    except Exception:
        logger.exception("Failed to send message over mesh")
        return False


def start_listener(port: str) -> None:
    global _interface
    from meshtastic.serial_interface import SerialInterface

    logger.info("Connecting to Meshtastic node on %s", port)
    _interface = SerialInterface(devPath=port)
    pub.subscribe(_on_receive, "meshtastic.receive")
    logger.info("Meshtastic listener started")
