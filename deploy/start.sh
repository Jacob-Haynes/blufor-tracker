#!/usr/bin/env bash
set -euo pipefail

cd /opt/blufor-tracker
source .venv/bin/activate

# Auto-detect Meshtastic serial port
PORT=""
for dev in /dev/ttyUSB* /dev/ttyACM*; do
    [ -e "$dev" ] && PORT="$dev" && break
done

if [ -z "$PORT" ]; then
    echo "No Meshtastic device found — starting in simulation mode"
    exec python -m bridge --simulate
else
    echo "Found Meshtastic device at $PORT"
    exec python -m bridge --port "$PORT"
fi
