# Mesh↔TAK Bridge

Connects Meshtastic mesh radios to ATAK via OpenTAKServer. A Raspberry Pi runs the bridge and TAK server — field operators use ATAK with Meshtastic radios on airplane mode, HQ connects ATAK to the Pi over WiFi.

## Architecture

```
Field (airplane mode)                    HQ
[ATAK] ←BT→ [Radio] ~~LoRa~~ [Radio] ←USB→ [Pi]
                                              ├── OpenTAKServer (TAK server)
                                              ├── mesh_bridge.py (CoT relay)
                                              └── WiFi Hotspot (for HQ ATAK)

                                         [ATAK on tablet] ←WiFi→ [Pi]
```

- Field operators run ATAK with the Meshtastic plugin — positions and messages go over LoRa
- A Pi with a USB Meshtastic radio receives all mesh traffic
- The bridge converts Meshtastic packets to CoT XML and feeds them into OpenTAKServer
- HQ ATAK connects to OpenTAKServer over the Pi's WiFi hotspot
- CoT events from HQ (chat, markers) are relayed back out over the mesh

## What Gets Bridged

| Meshtastic → CoT | CoT → Meshtastic |
|---|---|
| Position → PLI (`a-f-G-U-C`) | GeoChat → Text message |
| Text message → GeoChat (`b-t-f`) | Emergency → SOS text |
| SOS/PANIC → Emergency (`b-a-o-tbl`) | Marker → Waypoint |
| Waypoint → Marker (`b-m-p-c`) | |
| ATAK plugin (portnum 72) → passthrough | |

## Quick Start (No Hardware)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m bridge --simulate
```

Generates fake mesh traffic, converts to CoT, and logs output. Connect OpenTAKServer separately to see positions in ATAK.

## Quick Start (Live)

```bash
source .venv/bin/activate
python -m bridge --port /dev/ttyUSB0
```

## CLI Options

```
python -m bridge [OPTIONS]

  --port PORT       Meshtastic serial port (default: /dev/ttyUSB0)
  --tak-host HOST   TAK server host (default: 127.0.0.1)
  --tak-port PORT   TAK server TCP port (default: 8087)
  --simulate        Simulate mesh traffic (no hardware needed)
```

## Pi Deployment

Flash Raspberry Pi OS, then:

```bash
git clone https://github.com/Jacob-Haynes/blufor-tracker.git
cd blufor-tracker
sudo bash deploy/install.sh
sudo reboot
```

This installs:
- OpenTAKServer (via official Pi installer)
- Mesh bridge + dependencies
- WiFi hotspot (`BFT-TAK` / `bluforce24`)
- Systemd services for everything

### Networking

| Interface | IP | Purpose |
|---|---|---|
| wlan0 (hotspot) | 192.168.4.1 | ATAK devices connect here |
| eth0 (direct cable) | 192.168.1.1 | SSH admin (laptop at 192.168.1.2) |

Both work simultaneously. If the Pi is on an existing network instead, OTS binds `0.0.0.0` — ATAK connects on whatever IP the Pi has.

### Commands

```bash
sudo systemctl start opentakserver mesh-bridge   # start services
sudo systemctl stop mesh-bridge                  # stop bridge
journalctl -u mesh-bridge -f                     # bridge logs
journalctl -u opentakserver -f                   # OTS logs
```

### Updating

```bash
cd blufor-tracker && git pull
sudo cp -r bridge deploy /opt/blufor-tracker/
sudo systemctl restart mesh-bridge
```

## ATAK Setup

### Field Operators (Mesh)

1. Install ATAK-CIV on Android
2. Install Meshtastic app + [ATAK Meshtastic plugin](https://github.com/meshtastic/ATAK-Plugin)
3. Pair Meshtastic radio via Bluetooth
4. In Meshtastic app: set channel + encryption key (must match all radios)
5. In ATAK: enable Meshtastic plugin, configure channel
6. Phone goes on airplane mode — all comms over LoRa

### HQ (WiFi to Pi)

1. Install ATAK-CIV on tablet
2. Connect to Pi WiFi hotspot (`BFT-TAK`)
3. In ATAK → Settings → Network → TAK Servers → Add:
   - Host: `192.168.4.1`
   - Port: `8087`
   - Protocol: TCP
4. Field positions appear on the HQ ATAK map

## Related Projects

These repos run alongside blufor-tracker on the same Pi, managed by [pi-profiles](https://github.com/Jacob-Haynes/pi-profiles):

| Repo | Description |
|---|---|
| [drone-detector](https://github.com/Jacob-Haynes/drone-detector) | WiFi-based drone detection (DJI DroneID, Remote ID) with CoT output to TAK |
| [mesh-deadrop](https://github.com/Jacob-Haynes/mesh-deadrop) | Meshtastic store-and-forward message relay — encrypted dead drop for mesh nodes |
| [tac-advisor](https://github.com/Jacob-Haynes/tac-advisor) | Local LLM tactical advisor — reads CoT context, answers queries via web UI and Meshtastic |
| [pi-profiles](https://github.com/Jacob-Haynes/pi-profiles) | Systemd profile switcher — run multiple projects on one Pi, switch with a single command |

## Project Structure

```
bridge/
  __main__.py       # entry point
  mesh_bridge.py    # Meshtastic serial ↔ OTS TCP, simulator
  cot_converter.py  # CoT XML ↔ Meshtastic packet conversion
deploy/
  install.sh        # Pi installer (OTS + bridge + hotspot)
  start.sh          # launcher with auto-detection
  mesh-bridge.service
archive/
  server/           # original BFT web server (archived)
  frontend/         # original BFT web UI (archived)
```
