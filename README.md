# Mesh-TAK Bridge

Bidirectional bridge between Meshtastic mesh radios and ATAK via OpenTAKServer. A Raspberry Pi runs everything — field operators use ATAK with Meshtastic radios on airplane mode, HQ connects ATAK to the Pi over WiFi. No internet required.

## How It Works

```
Field (airplane mode)                          Pi (bridge + TAK server)
[ATAK] <-BT-> [Radio] ~~LoRa~~ [Radio] <-USB-> [mqtt_shim.py]
                                                      |
                                                [RabbitMQ / MQTT]
                                                      |
                                                [OpenTAKServer]
                                                      |
                                                [WiFi Hotspot]
                                                      |
                                         [ATAK on tablet] <-WiFi-> HQ
```

- **Field operators** run ATAK with the Meshtastic plugin. Their positions and messages travel over LoRa mesh radio.
- **The Pi** receives all mesh traffic via a USB-connected radio, converts it, and feeds it into OpenTAKServer.
- **HQ operators** connect ATAK to the Pi over WiFi. They see field positions and can chat back.
- **Chat goes both ways** — HQ messages are converted to TAKPacket protobufs and transmitted over the mesh.
- **All data persists** — OTS stores positions, chat history, and contacts in a PostgreSQL database that survives reboots.

## What You Need

- **Raspberry Pi** (tested on Pi 5) with power supply and microSD card
- **Meshtastic radios** (e.g. Heltec V3) — one per person, plus one plugged into the Pi via USB
- **Android phones/tablets** — one per person, with ATAK-CIV installed
- **USB cable** — to connect one radio to the Pi

## Step 1: Set Up the Pi

### Flash the SD Card

1. Download [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on your computer
2. Insert the microSD card
3. In Imager, choose:
   - **OS:** Raspberry Pi OS (64-bit) — select **Bookworm** (not Trixie). Bookworm ships Python 3.12 which OpenTAKServer requires. Trixie uses Python 3.13 which has a known bug that prevents messages reaching ATAK clients.
   - **Storage:** your microSD card
4. Click the gear icon to set:
   - Hostname (e.g. `bft-pi`)
   - Username and password (e.g. `pi` / your password)
   - WiFi (your home network, for initial setup — the Pi will create its own hotspot later)
   - Enable SSH
5. Flash the card, insert it into the Pi, and boot

### Install Everything

SSH into the Pi (or connect a keyboard), then:

```bash
git clone https://github.com/Jacob-Haynes/blufor-tracker.git
cd blufor-tracker
sudo bash deploy/install.sh
```

This takes 10-20 minutes and installs:
- OpenTAKServer (TAK server with PostgreSQL database)
- The mesh bridge service
- WiFi hotspot: **BFT-TAK** (password: `bluforce24`)
- All Python dependencies
- The OTS device_callsign patch (fixes a known bug)

To use a different hotspot name or password:
```bash
HOTSPOT_SSID="MyTAK" HOTSPOT_PASS="mypassword" sudo -E bash deploy/install.sh
```

**Do not reboot yet** — configure the radios first (Step 2).

## Step 2: Configure the Radios

All radios must be on the **same channel** with the **same encryption key**. One radio plugs into the Pi (the "bridge radio"), the rest pair to phones via Bluetooth.

### Bridge Radio (USB to Pi)

Plug the bridge radio into the Pi via USB, then:

```bash
source /opt/blufor-tracker/.venv/bin/activate

# Set device role to TAK (required — ATAK ignores packets from non-TAK nodes)
meshtastic --port /dev/ttyUSB0 --set device.role TAK

# Create a channel with a random encryption key
meshtastic --port /dev/ttyUSB0 --ch-set name Bullecourt --ch-index 0
meshtastic --port /dev/ttyUSB0 --ch-set psk random --ch-index 0

# Set your region (required — use your local region)
meshtastic --port /dev/ttyUSB0 --set lora.region EU_868
```

**Write down the channel key** — you'll need it for every other radio:

```bash
meshtastic --port /dev/ttyUSB0 --ch-get --ch-index 0
```

The PSK is shown in base64 format (e.g. `base64:abc123...`). Copy it exactly.

If the radio isn't at `/dev/ttyUSB0`, find it with:
```bash
ls /dev/ttyUSB* /dev/ttyACM*
```

### Field Radios (Bluetooth to Phones)

For each field radio, use the **Meshtastic app** on the phone it will pair with:

1. Open the Meshtastic app and connect to the radio via Bluetooth
2. Go to **Channel settings** → set the same channel name and PSK as the bridge radio
3. Go to **Device settings** → set role to **TAK**
4. Go to **LoRa settings** → set the same region as the bridge radio (e.g. `EU_868`)
5. Check the node list — the bridge radio should appear

### Recommended Radio Settings

These settings apply to **all radios** (bridge and field). Set via the Meshtastic app or CLI.

| Setting | Value | Why |
|---|---|---|
| Device role | TAK | Required for ATAK plugin compatibility |
| LoRa region | Your region (e.g. EU_868) | Must match all radios, sets legal frequency |
| LoRa modem preset | LONG_MODERATE | Good balance of range and throughput. Use LONG_SLOW for max range |
| Hop limit | 3 (default) | How many times a packet can be relayed across the mesh |
| Smart position broadcast | Enabled | Only broadcasts position when moving — saves airtime |

### Now Reboot the Pi

```bash
sudo reboot
```

After reboot, both services start automatically:
- **opentakserver** — the TAK server (takes ~30 seconds to start)
- **mesh-bridge** — the Meshtastic bridge (starts after OTS)

The WiFi hotspot (`BFT-TAK`) will be available within 1-2 minutes.

## Step 3: Set Up OpenTAKServer

Connect to the Pi's WiFi hotspot (`BFT-TAK`, password `bluforce24`) and open a browser.

1. Browse to **http://192.168.4.1**
2. **Create an account** — OTS will prompt you to register on first visit. This becomes the admin account.
3. Go to **Integrations** → **Meshtastic**
4. Enable the Meshtastic integration and set:
   - MQTT broker: `127.0.0.1`
   - Topic root: `opentakserver`
   - Channel name: same as your radios (e.g. `Bullecourt`)
   - Channel PSK: the base64 key from Step 2
5. Save and restart OTS: `sudo systemctl restart opentakserver`

OTS stores all data (positions, chat messages, contacts) in PostgreSQL. Everything persists across reboots — you won't lose history when the Pi restarts.

## Step 4: Set Up ATAK Clients

There are two ways to connect — **WiFi** (for users near the Pi) or **mesh** (for users in the field with a radio). Both see the same shared picture.

### WiFi Users (near the Pi)

No radio needed. Connect directly to the Pi's TAK server over WiFi.

**Install:**
1. Install [ATAK-CIV](https://tak.gov/products/atak-civ) on your Android device

**Connect:**
1. Connect to WiFi: **BFT-TAK** (password: `bluforce24`)
2. Open ATAK
3. Tap the three-dot menu → **Settings** → **Network Preferences** → **TAK Server connections** → **Add**
4. Enter:
   - Description: `BFT` (or any name)
   - Address: `192.168.4.1`
   - Port: `8087`
   - Protocol: **TCP**
   - Leave SSL disabled
5. Save and connect

You should see field operators' positions and be able to chat with them.

### Mesh Users (field, no WiFi)

Requires a Meshtastic radio paired via Bluetooth. Works fully offline — all comms go over LoRa.

**Install (do this before going to the field, while you have internet):**
1. Install [ATAK-CIV](https://tak.gov/products/atak-civ) on your Android phone
2. Install the [Meshtastic app](https://play.google.com/store/apps/details?id=com.geeksville.mesh) from the Play Store
3. Download the [ATAK Meshtastic plugin APK](https://github.com/meshtastic/ATAK-Plugin/releases) and open it — ATAK will prompt to install the plugin

**Configure (one-time setup):**
1. Open ATAK → **Settings** → **My Preferences** → set your **Callsign** (e.g. TIER, ALPHA-1)
2. Open ATAK → **Plugins** → enable the **Meshtastic** plugin
3. In the Meshtastic plugin settings, select your paired radio

**Field use:**
1. Turn on your Meshtastic radio
2. Put your phone into **airplane mode** (Bluetooth stays on — this ensures all traffic goes over LoRa, not WiFi/cellular)
3. Open ATAK — your position and messages flow over the mesh

**What you can do:**
- Your position shows up on all other ATAK users' maps (mesh and WiFi)
- Send messages via the ATAK chat tool — they reach WiFi users through the bridge
- WiFi users' replies appear in your ATAK chat
- Text messages also appear in the Meshtastic app (the bridge sends both formats)

## Troubleshooting

### Check service status
```bash
sudo systemctl status mesh-bridge       # is the bridge running?
sudo systemctl status opentakserver     # is OTS running?
journalctl -u mesh-bridge -f            # bridge logs (live)
journalctl -u opentakserver -f          # OTS logs (live)
```

### No messages from field to HQ
- Check the bridge radio is plugged in via USB
- Check `journalctl -u mesh-bridge -f` shows `Serial→MQTT` lines when field users send messages
- Verify all radios are on the same channel name and PSK
- Verify all radios have the same LoRa region set

### No messages from HQ to field
- Check `journalctl -u mesh-bridge -f` for `Firehose→Serial` lines when HQ sends a message
- Verify the bridge radio role is `TAK`: `meshtastic --port /dev/ttyUSB0 --get device.role`
- Check the OTS patch is applied (see below)

### ATAK can't connect to OTS
- Make sure you're on the `BFT-TAK` WiFi network
- Try `http://192.168.4.1` in a browser — you should see the OTS web UI
- Check OTS is running: `sudo systemctl status opentakserver`

### Duplicate messages on HQ
The first message after a bridge restart may arrive twice — this is normal. The bridge learns which nodes are ATAK devices and deduplicates from then on.

### Bridge service won't start
- Check a radio is plugged into USB: `ls /dev/ttyUSB*`
- Check serial permissions: your user should be in the `dialout` group (`groups $USER`)
- Check logs: `journalctl -u mesh-bridge -n 50`

### After an OTS upgrade
Re-apply the device_callsign patch:
```bash
OTS_CTL=~/.opentakserver_venv/lib/python3.*/site-packages/opentakserver/controllers/meshtastic_controller.py
sed -i '/uid = unishox2.decompress(pb.contact.device_callsign/a\            uid = uid.split("|")[0]  # BFT patch: strip ATAK pipe-UUID suffix' "$OTS_CTL"
sudo systemctl restart opentakserver
```
See `deploy/ots_patches/meshtastic_device_callsign.patch` for details on what this fixes.

## Updating the Bridge

With internet:
```bash
cd /opt/blufor-tracker && git pull
sudo systemctl restart mesh-bridge
```

Without internet (from your dev machine):
```bash
scp bridge/mqtt_shim.py USER@PI_IP:/opt/blufor-tracker/bridge/mqtt_shim.py
ssh USER@PI_IP 'sudo systemctl restart mesh-bridge'
```

## Admin Commands

```bash
sudo systemctl start mesh-bridge        # start the bridge
sudo systemctl stop mesh-bridge         # stop the bridge
sudo systemctl restart mesh-bridge      # restart after changes
sudo systemctl start opentakserver      # start OTS
journalctl -u mesh-bridge -f            # live bridge logs
journalctl -u opentakserver -f          # live OTS logs
```

## Development

### Simulation Mode (No Hardware)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m bridge --simulate
```

Generates fake mesh traffic at Salisbury Plain coordinates for testing CoT conversion without hardware. Uses the legacy `mesh_bridge.py` — the production entry point is `mqtt_shim.py`.

### Run mqtt_shim Locally

```bash
source .venv/bin/activate
python -m bridge.mqtt_shim --port /dev/ttyUSB0
```

Requires a Meshtastic radio on USB and RabbitMQ running locally.

## Project Structure

```
bridge/
  mqtt_shim.py        # production entry point: serial <-> MQTT bridge with firehose + self-SA
  cot_converter.py    # CoT XML <-> Meshtastic packet conversion
  mesh_bridge.py      # legacy: direct PyTAK TCP bridge with simulation mode
  upstream_relay.py   # optional upstream TAK server relay (used by mesh_bridge)
  __main__.py         # `python -m bridge` entry point (legacy/simulation)
deploy/
  install.sh          # Pi installer (OTS + bridge + hotspot + patch)
  start.sh            # manual launcher with serial port auto-detection
  mesh-bridge.service # systemd unit
  ots_patches/        # patches for known OTS bugs
  fix_firehose.py     # diagnostic: manual firehose queue binding (not needed on Python 3.12)
```

## Related Projects

| Repo | Description |
|---|---|
| [drone-detector](https://github.com/Jacob-Haynes/drone-detector) | WiFi-based drone detection (DJI DroneID, Remote ID) with CoT output to TAK |
| [mesh-deadrop](https://github.com/Jacob-Haynes/mesh-deadrop) | Meshtastic store-and-forward message relay |
| [tac-advisor](https://github.com/Jacob-Haynes/tac-advisor) | Local LLM tactical advisor with CoT context |
| [pi-profiles](https://github.com/Jacob-Haynes/pi-profiles) | Systemd profile switcher for multi-project Pi |
