import asyncio
import json
import math
import os
import threading
import time

from server.models import (
    Annotation,
    ControlMeasure,
    Geofence,
    GeofenceAlert,
    MeshLink,
    Message,
    PositionReport,
    Report,
    Route,
    SOSAlert,
    Waypoint,
)

STALE_THRESHOLD_S = 60
TRAIL_MAX_POINTS = 50


class PositionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._positions: dict[str, PositionReport] = {}
        self._trails: dict[str, list[PositionReport]] = {}
        self._queue: asyncio.Queue[PositionReport] | None = None

    def set_queue(self, queue: asyncio.Queue[PositionReport]) -> None:
        self._queue = queue

    def update(self, report: PositionReport) -> None:
        with self._lock:
            self._positions[report.callsign] = report
            trail = self._trails.setdefault(report.callsign, [])
            trail.append(report)
            if len(trail) > TRAIL_MAX_POINTS:
                self._trails[report.callsign] = trail[-TRAIL_MAX_POINTS:]
        if self._queue is not None:
            try:
                self._queue.put_nowait(report)
            except asyncio.QueueFull:
                pass
        # Check geofences
        geofence_store.check_position(report)
        # Record session event
        session_recorder.record_event("position", report.model_dump())

    def get_all(self) -> list[PositionReport]:
        now = time.time()
        with self._lock:
            results = []
            for p in self._positions.values():
                report = p.model_copy(
                    update={"stale": (now - p.timestamp) > STALE_THRESHOLD_S}
                )
                results.append(report)
            return results

    def get_trail(
        self, callsign: str, max_age_seconds: float = 300
    ) -> list[PositionReport]:
        now = time.time()
        with self._lock:
            trail = self._trails.get(callsign, [])
            return [p for p in trail if (now - p.timestamp) <= max_age_seconds]

    def get_all_trails(
        self, max_age_seconds: float = 300
    ) -> dict[str, list[PositionReport]]:
        now = time.time()
        with self._lock:
            result = {}
            for cs, trail in self._trails.items():
                filtered = [
                    p for p in trail if (now - p.timestamp) <= max_age_seconds
                ]
                if filtered:
                    result[cs] = filtered
            return result

    def get_position(self, callsign: str) -> PositionReport | None:
        with self._lock:
            return self._positions.get(callsign)


store = PositionStore()


class MessageStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._messages: list[Message] = []
        self._messages_by_id: dict[str, int] = {}  # id -> index
        self._queue: asyncio.Queue[Message] | None = None

    def set_queue(self, queue: asyncio.Queue[Message]) -> None:
        self._queue = queue

    def add(self, msg: Message) -> None:
        with self._lock:
            self._messages_by_id[msg.id] = len(self._messages)
            self._messages.append(msg)
        if self._queue is not None:
            try:
                self._queue.put_nowait(msg)
            except asyncio.QueueFull:
                pass
        session_recorder.record_event("message", msg.model_dump())

    def acknowledge(self, msg_id: str, by: str = "HQ") -> Message | None:
        with self._lock:
            idx = self._messages_by_id.get(msg_id)
            if idx is None:
                # Linear search fallback
                for i, m in enumerate(self._messages):
                    if m.id == msg_id:
                        idx = i
                        break
            if idx is None:
                return None
            msg = self._messages[idx]
            updated = msg.model_copy(
                update={
                    "acked": True,
                    "acked_by": by,
                    "acked_at": time.time(),
                }
            )
            self._messages[idx] = updated
        if self._queue is not None:
            try:
                self._queue.put_nowait(updated)
            except asyncio.QueueFull:
                pass
        return updated

    def get_for_participant(self, callsign: str) -> list[Message]:
        """Return messages visible to a given callsign (or HQ)."""
        with self._lock:
            if callsign == "HQ":
                return list(self._messages)
            return [
                m
                for m in self._messages
                if m.channel == "BROADCAST"
                or m.channel == callsign
                or m.sender == callsign
                or m.channel == "HQ" and m.sender == callsign
            ]

    def get_all(self) -> list[Message]:
        with self._lock:
            return list(self._messages)


message_store = MessageStore()


class WaypointStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._waypoints: dict[str, Waypoint] = {}
        self._queue: asyncio.Queue[dict] | None = None

    def set_queue(self, queue: asyncio.Queue[dict]) -> None:
        self._queue = queue

    def add(self, waypoint: Waypoint) -> None:
        with self._lock:
            self._waypoints[waypoint.id] = waypoint
        if self._queue is not None:
            try:
                self._queue.put_nowait(
                    {"action": "add", "waypoint": waypoint.model_dump()}
                )
            except asyncio.QueueFull:
                pass
        session_recorder.record_event("waypoint", waypoint.model_dump())

    def delete(self, waypoint_id: str) -> bool:
        with self._lock:
            if waypoint_id not in self._waypoints:
                return False
            del self._waypoints[waypoint_id]
        if self._queue is not None:
            try:
                self._queue.put_nowait(
                    {
                        "action": "delete",
                        "type": "waypoint_delete",
                        "id": waypoint_id,
                    }
                )
            except asyncio.QueueFull:
                pass
        return True

    def get_all(self) -> list[Waypoint]:
        with self._lock:
            return list(self._waypoints.values())


waypoint_store = WaypointStore()


class SOSStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._alerts: dict[str, SOSAlert] = {}
        self._queue: asyncio.Queue[SOSAlert] | None = None

    def set_queue(self, queue: asyncio.Queue[SOSAlert]) -> None:
        self._queue = queue

    def add(self, alert: SOSAlert) -> None:
        with self._lock:
            self._alerts[alert.id] = alert
        if self._queue is not None:
            try:
                self._queue.put_nowait(alert)
            except asyncio.QueueFull:
                pass
        session_recorder.record_event("sos", alert.model_dump())

    def acknowledge(self, alert_id: str, by: str = "HQ") -> SOSAlert | None:
        with self._lock:
            alert = self._alerts.get(alert_id)
            if not alert:
                return None
            updated = alert.model_copy(
                update={
                    "acknowledged": True,
                    "acknowledged_by": by,
                    "acknowledged_at": time.time(),
                }
            )
            self._alerts[alert_id] = updated
        if self._queue is not None:
            try:
                self._queue.put_nowait(updated)
            except asyncio.QueueFull:
                pass
        return updated

    def get_active(self) -> list[SOSAlert]:
        with self._lock:
            return [a for a in self._alerts.values() if not a.acknowledged]

    def get_all(self) -> list[SOSAlert]:
        with self._lock:
            return list(self._alerts.values())


sos_store = SOSStore()


def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in meters between two lat/lon points."""
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _point_in_polygon(lat: float, lon: float, polygon: list[list[float]]) -> bool:
    """Ray-casting algorithm for point-in-polygon test."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        yi, xi = polygon[i]
        yj, xj = polygon[j]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


class GeofenceStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._geofences: dict[str, Geofence] = {}
        self._last_inside: dict[tuple[str, str], bool] = {}
        self._queue: asyncio.Queue[dict] | None = None

    def set_queue(self, queue: asyncio.Queue[dict]) -> None:
        self._queue = queue

    def add(self, geofence: Geofence) -> None:
        with self._lock:
            self._geofences[geofence.id] = geofence
        if self._queue is not None:
            try:
                self._queue.put_nowait(
                    {"action": "add", "geofence": geofence.model_dump()}
                )
            except asyncio.QueueFull:
                pass

    def delete(self, geofence_id: str) -> bool:
        with self._lock:
            if geofence_id not in self._geofences:
                return False
            del self._geofences[geofence_id]
            keys_to_remove = [
                k for k in self._last_inside if k[0] == geofence_id
            ]
            for k in keys_to_remove:
                del self._last_inside[k]
        if self._queue is not None:
            try:
                self._queue.put_nowait(
                    {
                        "action": "delete",
                        "type": "geofence_delete",
                        "id": geofence_id,
                    }
                )
            except asyncio.QueueFull:
                pass
        return True

    def get_all(self) -> list[Geofence]:
        with self._lock:
            return list(self._geofences.values())

    def check_position(self, report: PositionReport) -> None:
        with self._lock:
            geofences = list(self._geofences.values())

        for gf in geofences:
            if gf.shape == "circle" and gf.center_lat is not None and gf.center_lon is not None and gf.radius_m is not None:
                dist = _haversine_distance(
                    report.lat, report.lon, gf.center_lat, gf.center_lon
                )
                is_inside = dist <= gf.radius_m
            elif gf.shape == "polygon" and len(gf.polygon) >= 3:
                is_inside = _point_in_polygon(report.lat, report.lon, gf.polygon)
            else:
                continue

            key = (gf.id, report.callsign)
            with self._lock:
                was_inside = self._last_inside.get(key)
                self._last_inside[key] = is_inside

            if was_inside is None:
                continue

            alert = None
            if is_inside and not was_inside:
                alert_type = "entered" if gf.geofence_type == "inclusion" else "violated"
                alert = GeofenceAlert(
                    geofence_id=gf.id,
                    geofence_name=gf.name,
                    callsign=report.callsign,
                    alert_type=alert_type,
                    lat=report.lat,
                    lon=report.lon,
                )
            elif not is_inside and was_inside:
                alert_type = "exited" if gf.geofence_type == "inclusion" else "exited"
                alert = GeofenceAlert(
                    geofence_id=gf.id,
                    geofence_name=gf.name,
                    callsign=report.callsign,
                    alert_type=alert_type,
                    lat=report.lat,
                    lon=report.lon,
                )

            if alert and self._queue is not None:
                try:
                    self._queue.put_nowait(
                        {"action": "alert", "alert": alert.model_dump()}
                    )
                except asyncio.QueueFull:
                    pass


geofence_store = GeofenceStore()


class AnnotationStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._annotations: dict[str, Annotation] = {}

    def add(self, annotation: Annotation) -> None:
        with self._lock:
            self._annotations[annotation.id] = annotation

    def delete(self, annotation_id: str) -> bool:
        with self._lock:
            if annotation_id not in self._annotations:
                return False
            del self._annotations[annotation_id]
        return True

    def get_all(self) -> list[Annotation]:
        with self._lock:
            return list(self._annotations.values())


annotation_store = AnnotationStore()


class ReportStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reports: dict[str, Report] = {}
        self._queue: asyncio.Queue[Report] | None = None

    def set_queue(self, queue: asyncio.Queue[Report]) -> None:
        self._queue = queue

    def add(self, report: Report) -> None:
        with self._lock:
            self._reports[report.id] = report
        if self._queue is not None:
            try:
                self._queue.put_nowait(report)
            except asyncio.QueueFull:
                pass
        session_recorder.record_event("report", report.model_dump())

    def acknowledge(self, report_id: str, by: str = "HQ") -> Report | None:
        with self._lock:
            report = self._reports.get(report_id)
            if not report:
                return None
            updated = report.model_copy(
                update={
                    "status": "acknowledged",
                    "acknowledged_by": by,
                }
            )
            self._reports[report_id] = updated
        if self._queue is not None:
            try:
                self._queue.put_nowait(updated)
            except asyncio.QueueFull:
                pass
        return updated

    def get_all(self) -> list[Report]:
        with self._lock:
            return list(self._reports.values())

    def get_by_type(self, report_type: str) -> list[Report]:
        with self._lock:
            return [r for r in self._reports.values() if r.report_type == report_type]


report_store = ReportStore()


class RouteStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._routes: dict[str, Route] = {}
        self._queue: asyncio.Queue[dict] | None = None

    def set_queue(self, queue: asyncio.Queue[dict]) -> None:
        self._queue = queue

    def add(self, route: Route) -> None:
        with self._lock:
            self._routes[route.id] = route
        if self._queue is not None:
            try:
                self._queue.put_nowait({"action": "add", "route": route.model_dump()})
            except asyncio.QueueFull:
                pass

    def delete(self, route_id: str) -> bool:
        with self._lock:
            if route_id not in self._routes:
                return False
            del self._routes[route_id]
        if self._queue is not None:
            try:
                self._queue.put_nowait({"action": "delete", "type": "route_delete", "id": route_id})
            except asyncio.QueueFull:
                pass
        return True

    def get_all(self) -> list[Route]:
        with self._lock:
            return list(self._routes.values())


route_store = RouteStore()


class ControlMeasureStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._measures: dict[str, ControlMeasure] = {}
        self._queue: asyncio.Queue[dict] | None = None

    def set_queue(self, queue: asyncio.Queue[dict]) -> None:
        self._queue = queue

    def add(self, measure: ControlMeasure) -> None:
        with self._lock:
            self._measures[measure.id] = measure
        if self._queue is not None:
            try:
                self._queue.put_nowait({"action": "add", "control_measure": measure.model_dump()})
            except asyncio.QueueFull:
                pass

    def delete(self, measure_id: str) -> bool:
        with self._lock:
            if measure_id not in self._measures:
                return False
            del self._measures[measure_id]
        if self._queue is not None:
            try:
                self._queue.put_nowait({"action": "delete", "type": "control_measure_delete", "id": measure_id})
            except asyncio.QueueFull:
                pass
        return True

    def get_all(self) -> list[ControlMeasure]:
        with self._lock:
            return list(self._measures.values())


control_measure_store = ControlMeasureStore()


class MeshTopologyStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._links: dict[tuple[str, str], MeshLink] = {}

    def update_link(self, link: MeshLink) -> None:
        key = (link.from_node, link.to_node)
        with self._lock:
            self._links[key] = link

    def get_all(self) -> list[MeshLink]:
        with self._lock:
            return list(self._links.values())


mesh_topology_store = MeshTopologyStore()


SESSIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sessions")


class SessionRecorder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._recording = False
        self._session_name: str | None = None
        self._file = None

    def start_recording(self, name: str) -> str:
        os.makedirs(SESSIONS_DIR, exist_ok=True)
        with self._lock:
            if self._recording:
                self.stop_recording()
            self._session_name = name
            path = os.path.join(SESSIONS_DIR, f"{name}.jsonl")
            self._file = open(path, "a")
            self._recording = True
        return name

    def stop_recording(self) -> None:
        with self._lock:
            if self._file:
                self._file.close()
                self._file = None
            self._recording = False
            self._session_name = None

    def record_event(self, event_type: str, data: dict) -> None:
        with self._lock:
            if not self._recording or not self._file:
                return
            event = {
                "timestamp": time.time(),
                "event_type": event_type,
                "data": data,
            }
            self._file.write(json.dumps(event) + "\n")
            self._file.flush()

    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    def get_status(self) -> dict:
        with self._lock:
            return {
                "recording": self._recording,
                "session_name": self._session_name,
            }

    def list_sessions(self) -> list[str]:
        if not os.path.isdir(SESSIONS_DIR):
            return []
        return [
            f[:-6]
            for f in os.listdir(SESSIONS_DIR)
            if f.endswith(".jsonl")
        ]

    def load_session(self, name: str) -> list[dict]:
        path = os.path.join(SESSIONS_DIR, f"{name}.jsonl")
        if not os.path.isfile(path):
            return []
        events = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events


session_recorder = SessionRecorder()
