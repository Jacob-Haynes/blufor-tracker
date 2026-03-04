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
    exec python -m server --simulate --host 0.0.0.0 --web-port 8000
else
    echo "Found Meshtastic device at $PORT"
    exec python -m server --port "$PORT" --host 0.0.0.0 --web-port 8000
fi
