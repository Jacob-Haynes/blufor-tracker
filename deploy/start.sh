#!/usr/bin/env bash
set -euo pipefail

cd /opt/blufor-tracker
source .venv/bin/activate

# Auto-detect Meshtastic serial port
PORT=""
for dev in /dev/ttyUSB* /dev/ttyACM*; do
    [ -e "$dev" ] && PORT="$dev" && break
done

# Build upstream args from env vars (set via upstream.env)
UPSTREAM_ARGS=""
if [ -n "${UPSTREAM_HOST:-}" ]; then
    UPSTREAM_ARGS="--upstream-host $UPSTREAM_HOST --upstream-port ${UPSTREAM_PORT:-8087}"
    [ "${UPSTREAM_TLS:-}" = "1" ] && UPSTREAM_ARGS="$UPSTREAM_ARGS --upstream-tls"
    [ -n "${UPSTREAM_CERTFILE:-}" ] && UPSTREAM_ARGS="$UPSTREAM_ARGS --upstream-certfile $UPSTREAM_CERTFILE"
    [ -n "${UPSTREAM_CAFILE:-}" ] && UPSTREAM_ARGS="$UPSTREAM_ARGS --upstream-cafile $UPSTREAM_CAFILE"
fi

if [ -z "$PORT" ]; then
    echo "No Meshtastic device found — starting in simulation mode"
    exec python -m bridge --simulate $UPSTREAM_ARGS
else
    echo "Found Meshtastic device at $PORT"
    exec python -m bridge --port "$PORT" $UPSTREAM_ARGS
fi
