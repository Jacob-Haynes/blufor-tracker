import argparse
import asyncio
import json
import logging
import os
import threading
import time
import urllib.request
import urllib.error
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from server.models import (
    Annotation,
    ControlMeasure,
    Geofence,
    Message,
    MeshLink,
    PositionReport,
    Report,
    Route,
    SOSAlert,
    Waypoint,
)
from server.state import (
    annotation_store,
    control_measure_store,
    geofence_store,
    mesh_topology_store,
    message_store,
    report_store,
    route_store,
    session_recorder,
    sos_store,
    store,
    waypoint_store,
)
from server.llm_context import SYSTEM_PROMPT, build_tactical_context
from server.llm_engine import llm_engine

_mesh_send = None  # set to mesh_listener.send_text when running with real hardware
_mesh_send_waypoint = None  # set to mesh_listener.send_waypoint when running with real hardware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

update_queue: asyncio.Queue[PositionReport] = asyncio.Queue(maxsize=256)
message_queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=256)
waypoint_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
sos_queue: asyncio.Queue[SOSAlert] = asyncio.Queue(maxsize=256)
geofence_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
report_queue: asyncio.Queue[Report] = asyncio.Queue(maxsize=256)
route_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
control_measure_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
connected_clients: set[WebSocket] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.set_queue(update_queue)
    message_store.set_queue(message_queue)
    waypoint_store.set_queue(waypoint_queue)
    sos_store.set_queue(sos_queue)
    geofence_store.set_queue(geofence_queue)
    report_store.set_queue(report_queue)
    route_store.set_queue(route_queue)
    control_measure_store.set_queue(control_measure_queue)
    asyncio.create_task(_broadcaster())
    asyncio.create_task(_message_broadcaster())
    asyncio.create_task(_waypoint_broadcaster())
    asyncio.create_task(_sos_broadcaster())
    asyncio.create_task(_geofence_broadcaster())
    asyncio.create_task(_report_broadcaster())
    asyncio.create_task(_route_broadcaster())
    asyncio.create_task(_control_measure_broadcaster())
    # Load LLM model in background thread (non-blocking)
    if (Path(__file__).resolve().parent.parent / "models").exists():
        threading.Thread(target=llm_engine.load, daemon=True).start()
    yield


app = FastAPI(title="Blue Force Tracker", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


async def _broadcast_json(data: str):
    stale_clients = []
    for ws in connected_clients:
        try:
            await ws.send_text(data)
        except Exception:
            stale_clients.append(ws)
    for ws in stale_clients:
        connected_clients.discard(ws)


async def _broadcaster():
    while True:
        report = await update_queue.get()
        await _broadcast_json(report.model_dump_json())


async def _message_broadcaster():
    while True:
        msg = await message_queue.get()
        await _broadcast_json(msg.model_dump_json())


async def _waypoint_broadcaster():
    while True:
        event = await waypoint_queue.get()
        await _broadcast_json(json.dumps(event))


async def _sos_broadcaster():
    while True:
        alert = await sos_queue.get()
        await _broadcast_json(alert.model_dump_json())


async def _geofence_broadcaster():
    while True:
        event = await geofence_queue.get()
        await _broadcast_json(json.dumps(event))


async def _report_broadcaster():
    while True:
        report = await report_queue.get()
        await _broadcast_json(report.model_dump_json())


async def _route_broadcaster():
    while True:
        event = await route_queue.get()
        await _broadcast_json(json.dumps(event))


async def _control_measure_broadcaster():
    while True:
        event = await control_measure_queue.get()
        await _broadcast_json(json.dumps(event))


@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


# --- Position endpoints ---

@app.get("/api/positions")
async def get_positions():
    return store.get_all()


# --- Message endpoints ---

@app.get("/api/messages")
async def get_messages(channel: str | None = None):
    messages = message_store.get_all()
    if channel:
        messages = [m for m in messages if m.channel == channel]
    return messages


class SendMessageRequest(BaseModel):
    channel: str
    body: str
    requires_ack: bool = False


@app.post("/api/messages")
async def post_message(req: SendMessageRequest):
    msg = Message(sender="HQ", channel=req.channel, body=req.body, requires_ack=req.requires_ack)
    message_store.add(msg)
    if _mesh_send:
        _mesh_send(req.channel, req.body)
    return msg


@app.post("/api/messages/{msg_id}/acknowledge")
async def acknowledge_message(msg_id: str):
    msg = message_store.acknowledge(msg_id)
    if msg:
        return msg
    return {"status": "not_found"}


# --- Trail endpoints ---

@app.get("/api/trails")
async def get_trails(max_age: float = 300):
    trails = store.get_all_trails(max_age)
    return {
        cs: [p.model_dump() for p in trail] for cs, trail in trails.items()
    }


@app.get("/api/trails/{callsign}")
async def get_trail(callsign: str, max_age: float = 300):
    trail = store.get_trail(callsign, max_age)
    return [p.model_dump() for p in trail]


# --- Waypoint endpoints ---

@app.get("/api/waypoints")
async def get_waypoints():
    return waypoint_store.get_all()


class CreateWaypointRequest(BaseModel):
    name: str
    lat: float
    lon: float
    waypoint_type: str = "checkpoint"
    icon: str = "✓"
    description: str = ""


@app.post("/api/waypoints")
async def create_waypoint(req: CreateWaypointRequest):
    wp = Waypoint(
        name=req.name,
        lat=req.lat,
        lon=req.lon,
        waypoint_type=req.waypoint_type,
        icon=req.icon,
        description=req.description,
    )
    waypoint_store.add(wp)
    if _mesh_send_waypoint:
        _mesh_send_waypoint(wp)
    return wp


@app.delete("/api/waypoints/{waypoint_id}")
async def delete_waypoint(waypoint_id: str):
    if waypoint_store.delete(waypoint_id):
        return {"status": "deleted"}
    return {"status": "not_found"}


# --- SOS endpoints ---

@app.get("/api/sos")
async def get_sos():
    return sos_store.get_all()


@app.post("/api/sos/{alert_id}/acknowledge")
async def acknowledge_sos(alert_id: str):
    alert = sos_store.acknowledge(alert_id, by="HQ")
    if alert:
        return alert
    return {"status": "not_found"}


# --- Geofence endpoints ---

@app.get("/api/geofences")
async def get_geofences():
    return geofence_store.get_all()


class CreateGeofenceRequest(BaseModel):
    name: str
    geofence_type: str = "inclusion"
    shape: str = "circle"
    center_lat: float | None = None
    center_lon: float | None = None
    radius_m: float | None = None
    polygon: list[list[float]] = []


@app.post("/api/geofences")
async def create_geofence(req: CreateGeofenceRequest):
    gf = Geofence(
        name=req.name,
        geofence_type=req.geofence_type,
        shape=req.shape,
        center_lat=req.center_lat,
        center_lon=req.center_lon,
        radius_m=req.radius_m,
        polygon=req.polygon,
    )
    geofence_store.add(gf)
    return gf


@app.delete("/api/geofences/{geofence_id}")
async def delete_geofence(geofence_id: str):
    if geofence_store.delete(geofence_id):
        return {"status": "deleted"}
    return {"status": "not_found"}


# --- Annotation endpoints ---

@app.get("/api/annotations")
async def get_annotations():
    return annotation_store.get_all()


class CreateAnnotationRequest(BaseModel):
    annotation_type: str
    label: str = ""
    color: str = "#ff0000"
    coordinates: list[list[float]] = []
    lat: float | None = None
    lon: float | None = None
    radius_m: float | None = None


@app.post("/api/annotations")
async def create_annotation(req: CreateAnnotationRequest):
    ann = Annotation(
        annotation_type=req.annotation_type,
        label=req.label,
        color=req.color,
        coordinates=req.coordinates,
        lat=req.lat,
        lon=req.lon,
        radius_m=req.radius_m,
    )
    annotation_store.add(ann)
    return ann


@app.delete("/api/annotations/{annotation_id}")
async def delete_annotation(annotation_id: str):
    if annotation_store.delete(annotation_id):
        return {"status": "deleted"}
    return {"status": "not_found"}


# --- Report endpoints ---

@app.get("/api/reports")
async def get_reports(type: str | None = None):
    if type:
        return report_store.get_by_type(type)
    return report_store.get_all()


class CreateReportRequest(BaseModel):
    report_type: str
    sender: str = "HQ"
    fields: dict[str, str]


@app.post("/api/reports")
async def create_report(req: CreateReportRequest):
    report = Report(
        report_type=req.report_type,
        sender=req.sender,
        fields=req.fields,
    )
    report_store.add(report)
    # Send as structured text over mesh
    if _mesh_send:
        prefix = req.report_type.upper() + ":"
        field_text = "|".join(f"{k}={v}" for k, v in req.fields.items())
        _mesh_send("BROADCAST", prefix + field_text)
    return report


@app.post("/api/reports/{report_id}/acknowledge")
async def acknowledge_report(report_id: str):
    report = report_store.acknowledge(report_id)
    if report:
        return report
    return {"status": "not_found"}


# --- Route endpoints ---

@app.get("/api/routes")
async def get_routes():
    return route_store.get_all()


class CreateRouteRequest(BaseModel):
    name: str
    waypoints: list[dict]
    color: str = "#00ff00"


@app.post("/api/routes")
async def create_route(req: CreateRouteRequest):
    route = Route(
        name=req.name,
        waypoints=req.waypoints,
        color=req.color,
    )
    route_store.add(route)
    return route


@app.delete("/api/routes/{route_id}")
async def delete_route(route_id: str):
    if route_store.delete(route_id):
        return {"status": "deleted"}
    return {"status": "not_found"}


# --- Control Measure endpoints ---

@app.get("/api/control-measures")
async def get_control_measures():
    return control_measure_store.get_all()


class CreateControlMeasureRequest(BaseModel):
    name: str
    measure_type: str
    coordinates: list[list[float]]
    color: str = "#ffff00"
    line_style: str = "solid"


@app.post("/api/control-measures")
async def create_control_measure(req: CreateControlMeasureRequest):
    cm = ControlMeasure(
        name=req.name,
        measure_type=req.measure_type,
        coordinates=req.coordinates,
        color=req.color,
        line_style=req.line_style,
    )
    control_measure_store.add(cm)
    return cm


@app.delete("/api/control-measures/{measure_id}")
async def delete_control_measure(measure_id: str):
    if control_measure_store.delete(measure_id):
        return {"status": "deleted"}
    return {"status": "not_found"}


# --- Mesh Topology endpoint ---

@app.get("/api/mesh/topology")
async def get_mesh_topology():
    return mesh_topology_store.get_all()


# --- Weather endpoint (Met Office DataHub proxy) ---

_weather_cache: dict[str, tuple[float, dict]] = {}
WEATHER_CACHE_TTL = 1800  # 30 minutes


@app.get("/api/weather")
async def get_weather(lat: float, lon: float):
    api_key = os.environ.get("METOFFICE_API_KEY")
    if not api_key:
        return {"error": "Met Office API key not configured", "status": 503}

    # Round to 0.1° for caching
    cache_key = f"{round(lat, 1)},{round(lon, 1)}"
    now = time.time()
    if cache_key in _weather_cache:
        cached_time, cached_data = _weather_cache[cache_key]
        if now - cached_time < WEATHER_CACHE_TTL:
            return cached_data

    url = f"https://data.hub.api.metoffice.gov.uk/sitespecific/v0/point/hourly?latitude={lat}&longitude={lon}"
    req = urllib.request.Request(url)
    req.add_header("apikey", api_key)
    req.add_header("accept", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        # Extract current/next hour forecast
        features = data.get("features", [])
        if not features:
            return {"error": "No forecast data available"}

        props = features[0].get("properties", {})
        timeseries = props.get("timeSeries", [])
        if not timeseries:
            return {"error": "No timeseries data"}

        current = timeseries[0]
        result = {
            "temperature_c": current.get("screenTemperature"),
            "wind_speed_kmh": round((current.get("windSpeed10m", 0) or 0) * 3.6, 1),
            "wind_direction_deg": current.get("windDirectionFrom10m"),
            "precip_probability_pct": current.get("probOfPrecipitation"),
            "weather_type": current.get("significantWeatherCode"),
            "visibility_m": current.get("visibility"),
        }
        _weather_cache[cache_key] = (now, result)
        return result
    except urllib.error.URLError as e:
        return {"error": f"Met Office API error: {e}"}
    except Exception as e:
        return {"error": str(e)}


# --- Elevation endpoint (Open-Meteo proxy) ---

class ElevationRequest(BaseModel):
    points: list[list[float]]  # [[lat, lon], ...]


@app.post("/api/elevation")
async def get_elevation(req: ElevationRequest):
    if not req.points:
        return {"elevations": []}

    lats = ",".join(str(p[0]) for p in req.points)
    lons = ",".join(str(p[1]) for p in req.points)
    url = f"https://api.open-meteo.com/v1/elevation?latitude={lats}&longitude={lons}"

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        elevations = data.get("elevation", [])
        return {"elevations": elevations}
    except Exception as e:
        return {"error": str(e), "elevations": []}


# --- LLM Advisor endpoints ---

@app.get("/api/llm/status")
async def llm_status():
    return {"loaded": llm_engine.is_loaded, "model": llm_engine.model_name}


class LLMSuggestRequest(BaseModel):
    query: str = "Summarise the current tactical situation"


@app.post("/api/llm/suggest")
async def llm_suggest(req: LLMSuggestRequest):
    if not llm_engine.is_loaded:
        return {
            "error": "LLM not loaded. Place a GGUF model file in the /models directory."
        }
    context = build_tactical_context()
    prompt = f"Current tactical picture:\n{context}\n\nQuery: {req.query}"
    result = await asyncio.to_thread(llm_engine.generate, SYSTEM_PROMPT, prompt)
    return {"suggestion": result}


# --- Session recording endpoints ---

@app.get("/api/sessions")
async def list_sessions():
    return session_recorder.list_sessions()


@app.get("/api/sessions/{name}")
async def get_session(name: str):
    return session_recorder.load_session(name)


class StartRecordingRequest(BaseModel):
    name: str


@app.post("/api/recording/start")
async def start_recording(req: StartRecordingRequest):
    session_recorder.start_recording(req.name)
    return session_recorder.get_status()


@app.post("/api/recording/stop")
async def stop_recording():
    session_recorder.stop_recording()
    return session_recorder.get_status()


@app.get("/api/recording/status")
async def recording_status():
    return session_recorder.get_status()


# --- WebSocket ---

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connected_clients.add(ws)
    logger.info("WebSocket client connected (%d total)", len(connected_clients))
    try:
        # Send current state
        for pos in store.get_all():
            await ws.send_text(pos.model_dump_json())
        for wp in waypoint_store.get_all():
            await ws.send_text(
                json.dumps({"action": "add", "waypoint": wp.model_dump()})
            )
        for alert in sos_store.get_active():
            await ws.send_text(alert.model_dump_json())
        for gf in geofence_store.get_all():
            await ws.send_text(
                json.dumps({"action": "add", "geofence": gf.model_dump()})
            )
        # Send reports
        for report in report_store.get_all():
            await ws.send_text(report.model_dump_json())
        # Send routes
        for route in route_store.get_all():
            await ws.send_text(
                json.dumps({"action": "add", "route": route.model_dump()})
            )
        # Send control measures
        for cm in control_measure_store.get_all():
            await ws.send_text(
                json.dumps({"action": "add", "control_measure": cm.model_dump()})
            )

        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg_type = data.get("type")
            if msg_type == "message":
                channel = data.get("channel", "BROADCAST")
                body = data.get("body", "")
                requires_ack = data.get("requires_ack", False)
                msg = Message(sender="HQ", channel=channel, body=body, requires_ack=requires_ack)
                message_store.add(msg)
                if _mesh_send:
                    _mesh_send(channel, body)
            elif msg_type == "message_ack":
                msg_id = data.get("id", "")
                message_store.acknowledge(msg_id, by=data.get("by", "HQ"))
            elif msg_type == "waypoint":
                wp = Waypoint(
                    name=data.get("name", "Waypoint"),
                    lat=data["lat"],
                    lon=data["lon"],
                    waypoint_type=data.get("waypoint_type", "checkpoint"),
                    icon=data.get("icon", "✓"),
                    description=data.get("description", ""),
                )
                waypoint_store.add(wp)
                if _mesh_send_waypoint:
                    _mesh_send_waypoint(wp)
            elif msg_type == "waypoint_delete":
                waypoint_store.delete(data.get("id", ""))
            elif msg_type == "report":
                report = Report(
                    report_type=data.get("report_type", "sitrep"),
                    sender=data.get("sender", "HQ"),
                    fields=data.get("fields", {}),
                )
                report_store.add(report)
                if _mesh_send:
                    prefix = report.report_type.upper() + ":"
                    field_text = "|".join(f"{k}={v}" for k, v in report.fields.items())
                    _mesh_send("BROADCAST", prefix + field_text)
            elif msg_type == "report_ack":
                report_store.acknowledge(data.get("id", ""), by=data.get("by", "HQ"))
    except WebSocketDisconnect:
        pass
    finally:
        connected_clients.discard(ws)
        logger.info(
            "WebSocket client disconnected (%d remaining)", len(connected_clients)
        )


def main():
    parser = argparse.ArgumentParser(description="Blue Force Tracker Server")
    parser.add_argument(
        "--simulate", action="store_true", help="Run with simulated nodes"
    )
    parser.add_argument(
        "--port",
        type=str,
        default="/dev/ttyUSB0",
        help="Serial port for Meshtastic node",
    )
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Bind address")
    parser.add_argument("--web-port", type=int, default=8000, help="HTTP port")
    args = parser.parse_args()

    if args.simulate:
        from server.simulator import run_simulator

        logger.info("Starting in simulation mode")
        t = threading.Thread(target=run_simulator, daemon=True)
        t.start()
    else:
        from server.mesh_listener import send_text, send_waypoint, start_listener

        global _mesh_send, _mesh_send_waypoint
        _mesh_send = send_text
        _mesh_send_waypoint = send_waypoint
        start_listener(args.port)

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.web_port, log_level="info")


if __name__ == "__main__":
    main()
