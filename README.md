# Blue Force Tracker

Real-time tactical tracker built on [Meshtastic](https://meshtastic.org/) mesh radios. A Raspberry Pi acts as the HQ node — plug in a Meshtastic radio via USB and all field positions, messages, reports, and mesh health appear on a web map. An optional on-device LLM provides tactical analysis without cloud connectivity.

## How It Works

```
[Phone + Radio]  ~~~mesh~~~  [Phone + Radio]
        \                        /
         ~~~~ mesh radio ~~~~
                  |
           [Pi + USB Node]
           + GGUF LLM (optional)
                  |
          Web UI (port 8000)
```

- Field operators carry a Meshtastic radio paired to their phone via Bluetooth
- The Meshtastic app broadcasts GPS positions and text messages over the mesh
- A Pi with a USB-connected node receives all mesh traffic
- The web UI displays positions on a map with real-time messaging, reports, and tactical overlays
- An optional local LLM (GGUF format) analyses the operational picture and provides tactical advice

## Features

### Mapping & Tracking
- **Live map** — satellite, topographic (OpenTopoMap), and dark street base layers
- **Position tracking** — callsign markers, heading, speed, altitude, battery, stale detection
- **Movement trails** — configurable-age breadcrumb trails for all callsigns
- **Coordinate systems** — MGRS (primary), BNG (OS Grid), DMS, decimal degrees, mils toggle
- **Terrain profile** — elevation cross-sections along routes via Open-Meteo API

### Tactical Overlays
- **Control measures** — phase lines, boundaries, FEBA, LOD, FUP, start lines, axes of advance
- **Route planning** — multi-waypoint routes with distance/bearing legs
- **Geofencing** — circle and polygon zones with enter/exit/violation alerts
- **Waypoints** — RV, objective, danger, checkpoint, rally, TRP markers
- **Annotations** — freehand lines, polygons, markers, circles (via Leaflet.draw)

### Reports & Messages
- **Structured reports** — 9-Liner MEDEVAC, AT MIST, SITREP, Contact (SALUTE), METHANE forms
- **Messaging** — broadcast channel, direct messages, HQ channel, message acknowledgement
- **Canned messages** — pre-set tactical messages for quick sending
- **SOS alerts** — automatic detection of SOS/PANIC messages with acknowledgement workflow
- **Mesh bridge** — messages and reports flow bidirectionally between web UI and mesh network

### Fire Support
- **Fire mission calculator** — mils-based bearings, target grid, adjustment panel

### Mesh Network
- **Topology overlay** — SNR/RSSI colour-coded links between nodes
- **Signal quality** — live mesh health monitoring per link

### LLM Tactical Advisor
- **On-device inference** — runs a GGUF model locally via llama-cpp-python (no cloud required)
- **Tactical context** — automatically assembles current positions, reports, messages, mesh health
- **Quick queries** — SITREP summary, threat assessment, mesh health, unit dispersion, movement analysis
- **Custom queries** — freeform questions about the operational picture

### Offline & PWA
- **Service worker** — app shell caching, tile caching (OpenTopoMap priority), API response caching
- **Offline queue** — messages and reports queue locally and sync when connectivity returns
- **Installable** — PWA manifest for add-to-homescreen on mobile devices

### Weather
- **Met Office DataHub** — hourly forecast proxy (temperature, wind, precipitation, visibility)

### Session Recording
- **Record/replay** — capture all events to JSONL for after-action review

## Quick Start (Development)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m server --simulate
```

Open [http://localhost:8000](http://localhost:8000). Five simulated nodes will move around the City of London area.

## Quick Start (Live)

```bash
source .venv/bin/activate
python -m server --port /dev/ttyUSB0
```

Plug a Meshtastic node into the Pi via USB. Field nodes will appear on the map as they broadcast positions.

## LLM Tactical Advisor

The advisor runs a small quantised model on-device for air-gapped operation.

### Setup

1. Create a `models/` directory in the project root
2. Download a GGUF model (e.g. Qwen3 0.6B Q5_K_M):
   ```bash
   mkdir -p models
   # Download your preferred GGUF model into models/
   ```
3. Install the inference library:
   ```bash
   pip install llama-cpp-python
   ```
4. Start the server — the model loads automatically in the background
5. Click the **Advisor** button in the web UI to open the panel

The advisor automatically includes current unit positions, active SOS alerts, recent reports, messages, routes, control measures, geofence status, and mesh network health in its context.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `METOFFICE_API_KEY` | Met Office DataHub API key for weather forecasts |

## Project Structure

```
server/
  __main__.py       # entry point
  main.py           # FastAPI app, WebSocket, REST API, weather/elevation proxies
  models.py         # Pydantic models (Position, Message, Waypoint, SOS, Geofence, Report, Route, ControlMeasure, MeshLink)
  state.py          # thread-safe stores with async broadcast queues
  mesh_listener.py  # Meshtastic serial interface, topology extraction, structured report parsing
  simulator.py      # simulated nodes, messages, SOS, reports, mesh topology
  llm_engine.py     # GGUF model loader and inference via llama-cpp-python
  llm_context.py    # tactical context assembler for LLM prompts
frontend/
  index.html        # map + UI markup, CDN imports (Leaflet, mgrs.js, proj4.js)
  style.css         # dark theme, responsive layout
  app.js            # main app: map, WebSocket, routes, control measures, coord toggle, weather, terrain, canned messages, offline queue, tile caching
  advisor.js        # LLM tactical advisor panel
  reports.js        # 9-Liner MEDEVAC, AT MIST, SITREP, Contact (SALUTE), METHANE forms
  firemission.js    # fire mission calculator, mils-based bearings, adjustment panel
  coords.js         # MGRS, BNG, DMS, mils coordinate conversions
  topology.js       # mesh network topology overlay
  sw.js             # service worker: app shell, tile, and API caching
  manifest.json     # PWA manifest
deploy/
  install.sh        # one-shot Pi installer
  start.sh          # launcher with auto-detection
  bft.service       # systemd unit file
models/             # GGUF model files (gitignored)
sessions/           # recorded session JSONL files (gitignored)
```

## Pi Deployment

Flash Raspberry Pi OS onto an SD card, then clone and install:

```bash
git clone https://github.com/Jacob-Haynes/blufor-tracker.git
cd blufor-tracker
sudo bash deploy/install.sh
sudo reboot
```

The BFT server starts automatically on boot. Plug in a Meshtastic node and open `http://<pi-ip>:8000`.

### Accessing the Pi

**Ethernet cable (recommended for field use)**

Plug an Ethernet cable directly between the Pi and your laptop. No extra config needed if SSH is already enabled.

```bash
ssh pi@raspberrypi.local
```

Then access the BFT UI from your laptop by port-forwarding:

```bash
ssh -L 8000:localhost:8000 pi@raspberrypi.local
```

Open `http://localhost:8000` in your browser.

**WiFi SSH**

If the Pi is on the same WiFi network as your laptop, `ssh pi@raspberrypi.local` works directly. Open the BFT UI at `http://raspberrypi.local:8000`.

**Phone tethering note:** `.local` hostname resolution (mDNS) does not work over phone hotspots — the phone blocks multicast between connected devices. You have to do some fun stuff working out the ip of the phone and pi and ssh that way...

### Commands

```bash
sudo systemctl start bft       # start the service
sudo systemctl stop bft        # stop
sudo systemctl restart bft     # restart (e.g. after plugging in a node)
sudo systemctl status bft      # check status
journalctl -u bft -f           # live logs
```

## CLI Options

```
python -m server [OPTIONS]

  --simulate          Run with simulated nodes (no hardware needed)
  --port PORT         Serial port for Meshtastic node (default: /dev/ttyUSB0)
  --host HOST         Bind address (default: 0.0.0.0)
  --web-port PORT     HTTP port (default: 8000)
```

## Field Setup

Each operator needs:

1. A **Meshtastic radio** (T-Beam, Heltec, RAK, etc.)
2. The **Meshtastic app** (Android / iOS) paired via Bluetooth
3. **Position sharing enabled** in the app (on by default)
4. The node's **short name** set to their callsign (e.g. "ALPHA-1")

All nodes must be on the **same mesh channel and encryption key**.

### Messaging from the Field

Operators send messages using the Meshtastic app's built-in chat:

- **Channel message** — appears as BROADCAST in the web UI
- **Direct message to the Pi's node** — appears in the HQ channel
- **Direct message to another node** — appears in that callsign's DM channel

Messages sent from the web UI as HQ are transmitted back out over the mesh.

### Structured Reports over Mesh

Field operators can send structured reports as text messages using the format:

```
9LINER:line_1=value|line_2=value|...
SITREP:field=value|field=value|...
CONTACT:field=value|field=value|...
MIST:field=value|field=value|...
```

These are automatically parsed and displayed in the reports panel.
