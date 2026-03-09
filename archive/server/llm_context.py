"""Assemble current BFT state into a concise text block for LLM context."""

import time

from server.state import (
    control_measure_store,
    geofence_store,
    mesh_topology_store,
    message_store,
    report_store,
    route_store,
    sos_store,
    store,
)

SYSTEM_PROMPT = (
    "You are a tactical advisor for a UK Army Blue Force Tracker system. You provide concise, "
    "actionable suggestions using UK military terminology and doctrine. You have access to the "
    "current operational picture including unit positions, reports, messages, and mesh network "
    "status. Keep responses brief and formatted for quick reading. Use MGRS grid references "
    "where provided. Refer to units by callsign. Prioritise safety and force protection."
)


def _relative_time(ts: float) -> str:
    diff = int(time.time() - ts)
    if diff < 60:
        return f"{diff}s ago"
    if diff < 3600:
        return f"{diff // 60}min ago"
    return f"{diff // 3600}h ago"


def build_tactical_context() -> str:
    """Assemble current BFT state into a concise text block for LLM context."""
    sections: list[str] = []
    now = time.time()

    # 1. Current positions & status
    positions = store.get_all()
    if positions:
        lines = ["UNIT POSITIONS:"]
        for p in positions:
            status = "STALE" if p.stale else "ACTIVE"
            parts = [f"  {p.callsign}: lat={p.lat:.5f} lon={p.lon:.5f}"]
            if p.speed is not None:
                parts.append(f"speed={p.speed:.1f}km/h")
            if p.heading is not None:
                parts.append(f"hdg={p.heading:.0f}")
            if p.battery is not None:
                parts.append(f"batt={p.battery:.0f}%")
            parts.append(status)
            parts.append(_relative_time(p.timestamp))
            lines.append(", ".join(parts))
        sections.append("\n".join(lines))

    # 2. Active SOS alerts
    sos_alerts = sos_store.get_active()
    if sos_alerts:
        lines = ["ACTIVE SOS ALERTS:"]
        for a in sos_alerts:
            msg = f": {a.message}" if a.message else ""
            lines.append(
                f"  SOS {a.callsign} at lat={a.lat:.5f} lon={a.lon:.5f}{msg} ({_relative_time(a.timestamp)})"
            )
        sections.append("\n".join(lines))

    # 3. Recent reports (last 10)
    reports = report_store.get_all()
    recent_reports = sorted(reports, key=lambda r: r.timestamp, reverse=True)[:10]
    if recent_reports:
        lines = ["RECENT REPORTS:"]
        for r in recent_reports:
            field_summary = ", ".join(f"{k}={v}" for k, v in list(r.fields.items())[:5])
            lines.append(
                f"  {r.report_type.upper()} from {r.sender} ({_relative_time(r.timestamp)}): {field_summary}"
            )
        sections.append("\n".join(lines))

    # 4. Recent messages (last 20)
    messages = message_store.get_all()
    recent_msgs = messages[-20:] if len(messages) > 20 else messages
    if recent_msgs:
        lines = ["RECENT MESSAGES:"]
        for m in recent_msgs:
            lines.append(
                f"  {m.sender} -> {m.channel}: {m.body} ({_relative_time(m.timestamp)})"
            )
        sections.append("\n".join(lines))

    # 5. Active routes
    routes = route_store.get_all()
    if routes:
        lines = ["ACTIVE ROUTES:"]
        for rt in routes:
            wp_count = len(rt.waypoints)
            lines.append(f"  Route '{rt.name}': {wp_count} waypoints")
        sections.append("\n".join(lines))

    # 6. Control measures
    measures = control_measure_store.get_all()
    if measures:
        lines = ["CONTROL MEASURES:"]
        for cm in measures:
            lines.append(f"  {cm.name} ({cm.measure_type})")
        sections.append("\n".join(lines))

    # 7. Mesh health
    links = mesh_topology_store.get_all()
    if links:
        lines = ["MESH NETWORK:"]
        for link in links:
            snr_str = f"SNR {link.snr:.1f}dB" if link.snr is not None else "SNR unknown"
            quality = "good" if link.snr is not None and link.snr > 5 else (
                "fair" if link.snr is not None and link.snr > 0 else "poor"
            )
            age = _relative_time(link.last_seen)
            lines.append(f"  {link.from_node} <-> {link.to_node}: {snr_str} ({quality}, {age})")
        sections.append("\n".join(lines))

    # 8. Geofences
    geofences = geofence_store.get_all()
    if geofences:
        lines = ["GEOFENCES:"]
        for gf in geofences:
            lines.append(f"  {gf.name} ({gf.geofence_type}, {gf.shape})")
        sections.append("\n".join(lines))

    if not sections:
        return "No tactical data available."

    return "\n\n".join(sections)
