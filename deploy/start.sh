#!/usr/bin/env bash
# Launcher for mqtt_shim with auto-detection of Meshtastic serial port.
# Used for manual runs — systemd service calls mqtt_shim directly.
set -euo pipefail

cd /opt/blufor-tracker
source .venv/bin/activate

# Auto-detect Meshtastic serial port
PORT=""
for dev in /dev/ttyUSB* /dev/ttyACM*; do
    [ -e "$dev" ] && PORT="$dev" && break
done

if [ -z "$PORT" ]; then
    echo "ERROR: No Meshtastic device found on /dev/ttyUSB* or /dev/ttyACM*"
    echo "Plug in the bridge radio and try again."
    exit 1
fi

echo "Found Meshtastic device at $PORT"
exec python -m bridge.mqtt_shim --port "$PORT"
