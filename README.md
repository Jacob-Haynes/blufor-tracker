# Blue Force Tracker

Real-time personnel tracker built on [Meshtastic](https://meshtastic.org/) mesh radios. A Raspberry Pi acts as the HQ node — plug in a Meshtastic radio via USB and all field positions appear on a web map with live messaging.

## How It Works

```
[Phone + Radio]  ~~~mesh~~~  [Phone + Radio]
        \                        /
         ~~~~ mesh radio ~~~~
                  |
           [Pi + USB Node]
                  |
          Web UI (port 8000)
```

- Field operators carry a Meshtastic radio paired to their phone via Bluetooth
- The Meshtastic app broadcasts GPS positions and text messages over the mesh
- A Pi with a USB-connected node receives all mesh traffic
- The web UI displays positions on a map with real-time chat

## Features

- **Live map** — satellite, topographic, and dark street base layers with layer switching
- **Position tracking** — callsign markers, heading, speed, altitude, battery, stale detection
- **Messaging** — broadcast channel, direct messages between callsigns, HQ channel
- **Mesh bridge** — messages typed in the web UI transmit over the mesh, and vice versa
- **Auto-detect** — falls back to simulation mode if no Meshtastic node is plugged in
- **Boot-ready** — systemd service starts automatically on Pi power-on

## Quick Start (Development)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m server --simulate
```

Open [http://localhost:8000](http://localhost:8000). Five simulated nodes will move around the HAC area (EC1Y, London).

## Quick Start (Live)

```bash
source .venv/bin/activate
python -m server --port /dev/ttyUSB0
```

Plug a Meshtastic node into the Pi via USB. Field nodes will appear on the map as they broadcast positions.

## Pi Deployment

Flash Raspberry Pi OS onto an SD card, copy the project, and run:

```bash
cd blufor-tracker
sudo bash deploy/install.sh
sudo reboot
```

The BFT server starts automatically on boot. Plug in a Meshtastic node and open `http://<pi-ip>:8000`.

### Commands

```bash
sudo systemctl start bft       # start the service
sudo systemctl stop bft        # stop
sudo systemctl restart bft     # restart (e.g. after plugging in a node)
sudo systemctl status bft      # check status
journalctl -u bft -f           # live logs
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

## Project Structure

```
server/
  __main__.py       # entry point
  main.py           # FastAPI app, WebSocket, REST API
  models.py         # PositionReport and Message models
  state.py          # thread-safe stores with async broadcast queues
  mesh_listener.py  # Meshtastic serial interface, inbound/outbound bridge
  simulator.py      # simulated nodes and messages for development
frontend/
  index.html        # map + chat panel markup
  style.css         # dark theme, chat sidebar, responsive layout
  app.js            # Leaflet map, WebSocket client, chat UI
deploy/
  install.sh        # one-shot Pi installer
  start.sh          # launcher with auto-detection
  bft.service       # systemd unit file
```

## CLI Options

```
python -m server [OPTIONS]

  --simulate          Run with simulated nodes (no hardware needed)
  --port PORT         Serial port for Meshtastic node (default: /dev/ttyUSB0)
  --host HOST         Bind address (default: 0.0.0.0)
  --web-port PORT     HTTP port (default: 8000)
```
