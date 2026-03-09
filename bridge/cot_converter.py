"""Pure conversion functions: Meshtastic packets ↔ CoT XML."""

import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# Stale time defaults (seconds)
PLI_STALE = 120
CHAT_STALE = 600
MARKER_STALE = 86400

UID_PREFIX = "MESH-"


def _isotime(ts: float | None = None) -> str:
    dt = datetime.fromtimestamp(ts or time.time(), tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_cot_event(
    cot_type: str,
    uid: str,
    lat: float,
    lon: float,
    hae: float = 0.0,
    detail_xml: str = "",
    stale_seconds: int = PLI_STALE,
) -> str:
    now = time.time()
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<event version="2.0" type="{cot_type}" uid="{uid}"'
        f' time="{_isotime(now)}" start="{_isotime(now)}"'
        f' stale="{_isotime(now + stale_seconds)}" how="m-g">'
        f'<point lat="{lat}" lon="{lon}" hae="{hae}" ce="10" le="10"/>'
        f"<detail>{detail_xml}</detail>"
        f"</event>"
    )


def parse_cot_event(xml_str: str) -> dict | None:
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return None

    point = root.find("point")
    if point is None:
        return None

    result = {
        "type": root.get("type", ""),
        "uid": root.get("uid", ""),
        "lat": float(point.get("lat", 0)),
        "lon": float(point.get("lon", 0)),
        "hae": float(point.get("hae", 0)),
    }

    detail = root.find("detail")
    if detail is not None:
        contact = detail.find("contact")
        if contact is not None:
            result["callsign"] = contact.get("callsign", "")

        remarks = detail.find("remarks")
        if remarks is not None:
            result["remarks"] = remarks.text or ""

        chat = detail.find("__chat")
        if chat is not None:
            result["chatroom"] = chat.get("chatroom", "")
            result["sender_callsign"] = chat.get("senderCallsign", "")

        emergency = detail.find("emergency")
        if emergency is not None:
            result["emergency"] = True
            result["emergency_type"] = emergency.get("type", "")

    return result


def meshtastic_to_cot_xml(
    packet: dict, battery_cache: dict[str, float] | None = None
) -> str | None:
    decoded = packet.get("decoded", {})
    portnum = decoded.get("portnum")
    from_id = str(packet.get("fromId", packet.get("from", "unknown")))
    callsign = packet.get("_callsign", from_id)

    if portnum == "POSITION_APP":
        return _position_to_cot(decoded, callsign, battery_cache)
    elif portnum == "TEXT_MESSAGE_APP":
        text = decoded.get("text", "")
        if not text:
            return None
        upper = text.strip().upper()
        if upper.startswith("SOS") or upper.startswith("PANIC"):
            return _sos_to_cot(text, callsign, packet)
        return _chat_to_cot(text, callsign, packet)
    elif portnum == "WAYPOINT_APP":
        return _waypoint_to_cot(decoded, callsign)

    return None


def _position_to_cot(
    decoded: dict, callsign: str, battery_cache: dict[str, float] | None
) -> str | None:
    pos = decoded.get("position", {})

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
        return None

    alt = pos.get("altitude", 0.0)
    uid = f"{UID_PREFIX}{callsign}"

    detail_parts = [f'<contact callsign="{callsign}"/>']

    # Speed (Meshtastic: km/h → CoT: m/s) and heading
    speed = pos.get("groundSpeed")
    heading = pos.get("groundTrack")
    if heading is not None:
        heading = heading / 1e5
    if speed is not None or heading is not None:
        speed_ms = (speed / 3.6) if speed is not None else 0
        hdg = heading if heading is not None else 0
        detail_parts.append(f'<track speed="{speed_ms:.1f}" course="{hdg:.1f}"/>')

    # Battery
    battery = None
    if battery_cache:
        battery = battery_cache.get(callsign)
    if battery is not None:
        detail_parts.append(f'<status battery="{battery:.0f}"/>')

    detail_xml = "".join(detail_parts)
    return build_cot_event("a-f-G-U-C", uid, lat, lon, alt, detail_xml, PLI_STALE)


def _chat_to_cot(text: str, callsign: str, packet: dict) -> str:
    uid = f"{UID_PREFIX}chat-{uuid.uuid4().hex[:8]}"
    to_id = str(packet.get("toId", packet.get("to", "")))

    if to_id in ("^all", "4294967295", "0xffffffff"):
        chatroom = "All Chat Rooms"
    else:
        chatroom = "TeamChat"

    detail_xml = (
        f'<__chat chatroom="{chatroom}" senderCallsign="{callsign}">'
        f"<chatgrp uid0=\"{UID_PREFIX}{callsign}\"/>"
        f"</__chat>"
        f"<remarks>{_xml_escape(text)}</remarks>"
    )
    return build_cot_event("b-t-f", uid, 0.0, 0.0, 0.0, detail_xml, CHAT_STALE)


def _sos_to_cot(text: str, callsign: str, packet: dict) -> str:
    uid = f"{UID_PREFIX}sos-{callsign}"
    detail_xml = (
        f'<contact callsign="{callsign}"/>'
        f"<remarks>{_xml_escape(text)}</remarks>"
        f'<emergency type="911 Alert">{_xml_escape(callsign)}</emergency>'
    )
    # Try to get last known position
    return build_cot_event("b-a-o-tbl", uid, 0.0, 0.0, 0.0, detail_xml, CHAT_STALE)


def _waypoint_to_cot(decoded: dict, callsign: str) -> str | None:
    wp = decoded.get("waypoint", {})
    lat = wp.get("latitudeI", 0) / 1e7
    lon = wp.get("longitudeI", 0) / 1e7
    if not lat or not lon:
        return None
    name = wp.get("name", "Mesh Waypoint")
    uid = f"{UID_PREFIX}wp-{uuid.uuid4().hex[:8]}"
    detail_xml = (
        f'<contact callsign="{name}"/>'
        f'<remarks>From {callsign}: {wp.get("description", "")}</remarks>'
    )
    return build_cot_event("b-m-p-c", uid, lat, lon, 0.0, detail_xml, MARKER_STALE)


def cot_xml_to_meshtastic(xml_str: str) -> dict | None:
    parsed = parse_cot_event(xml_str)
    if parsed is None:
        return None

    cot_type = parsed["type"]

    # GeoChat → TEXT_MESSAGE_APP
    if cot_type == "b-t-f":
        text = parsed.get("remarks", "")
        if not text:
            return None
        return {"portnum": "TEXT_MESSAGE_APP", "text": text}

    # Emergency → TEXT_MESSAGE_APP with SOS prefix
    if cot_type.startswith("b-a-o-"):
        text = "SOS: " + parsed.get("remarks", "Emergency alert")
        return {"portnum": "TEXT_MESSAGE_APP", "text": text}

    # Marker → WAYPOINT_APP
    if cot_type.startswith("b-m-p-"):
        name = parsed.get("callsign", "Waypoint")
        return {
            "portnum": "WAYPOINT_APP",
            "waypoint": {
                "latitudeI": int(parsed["lat"] * 1e7),
                "longitudeI": int(parsed["lon"] * 1e7),
                "name": name,
                "description": parsed.get("remarks", ""),
            },
        }

    return None


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
