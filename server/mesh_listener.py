import logging
import threading
import time

from pubsub import pub

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


def _extract_topology(packet, callsign: str) -> None:
    """Extract SNR, RSSI, hop count from packet metadata and store as mesh link."""
    try:
        snr = packet.get("rxSnr") or packet.get("snr")
        rssi = packet.get("rxRssi") or packet.get("rssi")
        hop_count = packet.get("hopLimit") or packet.get("hopStart")

        if snr is not None or rssi is not None:
            my_callsign = "HQ"
            my_node = _get_my_node_id()
            if my_node:
                my_callsign = _node_id_to_callsign(str(my_node))

            link = MeshLink(
                from_node=callsign,
                to_node=my_callsign,
                snr=float(snr) if snr is not None else None,
                rssi=int(rssi) if rssi is not None else None,
                hop_count=int(hop_count) if hop_count is not None else None,
                last_seen=time.time(),
            )
            mesh_topology_store.update_link(link)
    except Exception:
        logger.debug("Could not extract topology from packet", exc_info=True)


def _parse_structured_report(text: str, callsign: str) -> Report | None:
    """Detect structured report messages over mesh text channel."""
    text_upper = text.strip().upper()
    prefixes = {
        "9LINER:": "9liner",
        "MIST:": "mist",
        "SITREP:": "sitrep",
        "CONTACT:": "contact",
        "SITING:": "siting",
        "CPERS:": "cpers",
    }

    for prefix, report_type in prefixes.items():
        if text_upper.startswith(prefix):
            field_str = text[len(prefix):].strip()
            fields = {}
            for part in field_str.split("|"):
                part = part.strip()
                if "=" in part:
                    key, _, val = part.partition("=")
                    fields[key.strip()] = val.strip()
                elif part:
                    # Numbered field without key
                    fields[f"field_{len(fields) + 1}"] = part

            return Report(
                report_type=report_type,
                sender=callsign,
                fields=fields,
            )

    return None


def _on_receive(packet, interface=None):
    try:
        decoded = packet.get("decoded", {})
        portnum = decoded.get("portnum")
        from_id = packet.get("fromId", packet.get("from", "unknown"))
        callsign = _node_id_to_callsign(str(from_id))

        # Extract topology from every packet
        _extract_topology(packet, callsign)

        if portnum == "POSITION_APP":
            pos = decoded.get("position", {})
            logger.debug("Raw position data for %s: %s", callsign, pos)

            # Prefer decoded float fields; fall back to raw integer fields
            lat = pos.get("latitude")
            if lat is None or lat == 0:
                lat_i = pos.get("latitudeI")
                if lat_i is not None:
                    lat = lat_i / 1e7
            lon = pos.get("longitude")
            if lon is None or lon == 0:
                lon_i = pos.get("longitudeI")
                if lon_i is not None:
                    lon = lon_i / 1e7

            if lat is None or lon is None:
                logger.warning("Skipping position with no lat/lon for %s", callsign)
                return

            altitude = pos.get("altitude", 0.0)
            speed = pos.get("groundSpeed", None)
            heading = pos.get("groundTrack", None)
            if heading is not None:
                heading = heading / 1e5
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

            # Detect SOS/PANIC messages
            text_upper = text.strip().upper()
            if text_upper.startswith("SOS") or text_upper.startswith("PANIC"):
                pos = store.get_position(callsign)
                lat = pos.lat if pos else 0.0
                lon = pos.lon if pos else 0.0
                alert = SOSAlert(
                    callsign=callsign,
                    lat=lat,
                    lon=lon,
                    message=text,
                )
                sos_store.add(alert)
                logger.info("SOS alert from %s: %s", callsign, text)

            # Detect structured reports
            structured_report = _parse_structured_report(text, callsign)
            if structured_report:
                report_store.add(structured_report)
                logger.info("Structured report from %s: %s", callsign, structured_report.report_type)

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

        elif portnum == "WAYPOINT_APP":
            wp_data = decoded.get("waypoint", {})
            lat = wp_data.get("latitudeI", 0) / 1e7
            lon = wp_data.get("longitudeI", 0) / 1e7
            name = wp_data.get("name", "Mesh Waypoint")
            description = wp_data.get("description", "")
            if lat and lon:
                wp = Waypoint(
                    name=name,
                    lat=lat,
                    lon=lon,
                    waypoint_type="checkpoint",
                    created_by=callsign,
                    description=description,
                )
                waypoint_store.add(wp)
                logger.info("Waypoint from mesh: %s @ %.6f, %.6f", name, lat, lon)

    except Exception:
        logger.exception("Error processing packet")


def send_text(channel: str, body: str) -> bool:
    """Send a text message from HQ out over the mesh."""
    if not _interface:
        logger.warning("Cannot send message: no Meshtastic interface")
        return False

    def _do_send():
        try:
            if channel == "BROADCAST":
                _interface.sendText(body, wantAck=True)
                logger.info("Sent broadcast over mesh: %s", body)
            elif channel == "HQ":
                return
            else:
                dest = _callsign_to_node_id(channel)
                if dest is None:
                    logger.warning(
                        "Cannot resolve callsign '%s' to node ID. Known nodes: %s",
                        channel,
                        list(_interface.nodes.keys()) if _interface.nodes else "none",
                    )
                    return
                _interface.sendText(body, destinationId=dest, wantAck=True)
                logger.info("Sent DM to %s (%s) over mesh: %s", channel, dest, body)
        except Exception:
            logger.exception("Failed to send message over mesh")

    threading.Thread(target=_do_send, daemon=True).start()
    return True


def send_waypoint(waypoint: Waypoint) -> bool:
    """Send a waypoint out over the mesh using WAYPOINT_APP portnum."""
    if not _interface:
        logger.warning("Cannot send waypoint: no Meshtastic interface")
        return False

    def _do_send():
        try:
            from meshtastic.protobuf import mesh_pb2

            wp_pb = mesh_pb2.Waypoint()
            wp_pb.latitudeI = int(waypoint.lat * 1e7)
            wp_pb.longitudeI = int(waypoint.lon * 1e7)
            wp_pb.name = waypoint.name
            wp_pb.description = waypoint.description
            _interface.sendData(
                wp_pb.SerializeToString(),
                portNum=mesh_pb2.PortNum.WAYPOINT_APP,
                wantAck=True,
            )
            logger.info("Sent waypoint over mesh: %s", waypoint.name)
        except Exception:
            logger.exception("Failed to send waypoint over mesh")

    threading.Thread(target=_do_send, daemon=True).start()
    return True


def start_listener(port: str) -> None:
    global _interface
    from meshtastic.serial_interface import SerialInterface

    logger.info("Connecting to Meshtastic node on %s", port)
    _interface = SerialInterface(devPath=port)
    pub.subscribe(_on_receive, "meshtastic.receive")
    logger.info("Meshtastic listener started")
