import time
import uuid

from pydantic import BaseModel, Field


class PositionReport(BaseModel):
    type: str = "position"
    callsign: str
    lat: float
    lon: float
    altitude: float = 0.0
    timestamp: float  # unix epoch
    battery: float | None = None  # 0-100 percent
    speed: float | None = None  # km/h
    heading: float | None = None  # degrees
    stale: bool = False


class Message(BaseModel):
    type: str = "message"
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = Field(default_factory=time.time)
    sender: str
    channel: str  # "BROADCAST", callsign for DM, or "HQ"
    body: str
    requires_ack: bool = False
    acked: bool = False
    acked_by: str | None = None
    acked_at: float | None = None


class Waypoint(BaseModel):
    type: str = "waypoint"
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = Field(default_factory=time.time)
    name: str
    lat: float
    lon: float
    waypoint_type: str = "checkpoint"  # rv, objective, danger, checkpoint, rally, trp
    icon: str = "✓"
    created_by: str = "HQ"
    description: str = ""


class SOSAlert(BaseModel):
    type: str = "sos"
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = Field(default_factory=time.time)
    callsign: str
    lat: float
    lon: float
    message: str = ""
    acknowledged: bool = False
    acknowledged_by: str | None = None
    acknowledged_at: float | None = None


class Geofence(BaseModel):
    type: str = "geofence"
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    geofence_type: str = "inclusion"  # inclusion or exclusion
    shape: str = "circle"  # circle or polygon
    center_lat: float | None = None
    center_lon: float | None = None
    radius_m: float | None = None
    polygon: list[list[float]] = []  # list of [lat, lon] pairs


class GeofenceAlert(BaseModel):
    type: str = "geofence_alert"
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = Field(default_factory=time.time)
    geofence_id: str
    geofence_name: str
    callsign: str
    alert_type: str  # entered, exited, violated
    lat: float
    lon: float


class Annotation(BaseModel):
    type: str = "annotation"
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    annotation_type: str  # line, polygon, marker, circle
    label: str = ""
    color: str = "#ff0000"
    coordinates: list[list[float]] = []  # for line/polygon: list of [lat, lon]
    lat: float | None = None  # for marker/circle center
    lon: float | None = None
    radius_m: float | None = None  # for circle
    created_by: str = "HQ"


class Report(BaseModel):
    type: str = "report"
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = Field(default_factory=time.time)
    report_type: str  # "9liner", "sitrep", "contact", "methane"
    sender: str = "HQ"
    fields: dict[str, str]  # keyed by field name
    status: str = "submitted"  # submitted, acknowledged, actioned
    acknowledged_by: str | None = None


class Route(BaseModel):
    type: str = "route"
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    waypoints: list[dict]  # [{lat, lon, name, order}]
    created_by: str = "HQ"
    color: str = "#00ff00"
    active: bool = True


class ControlMeasure(BaseModel):
    type: str = "control_measure"
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str  # e.g., "PL ALPHA", "FEBA", "LOD"
    measure_type: str  # phase_line, boundary, feba, lod, fup, start_line, axis_of_advance
    coordinates: list[list[float]]  # [[lat, lon], ...]
    color: str = "#ffff00"
    line_style: str = "solid"  # solid, dashed, dotted
    created_by: str = "HQ"


class MeshLink(BaseModel):
    type: str = "mesh_link"
    from_node: str
    to_node: str
    snr: float | None = None
    rssi: int | None = None
    hop_count: int | None = None
    last_seen: float = Field(default_factory=time.time)
