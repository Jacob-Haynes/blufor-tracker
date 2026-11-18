#!/usr/bin/env bash
# Manual launcher for the bridge (systemd runs mqtt_shim directly with the same defaults).
#   deploy/start.sh              # real radio, auto-detected
#   deploy/start.sh --simulate   # no radio/OTS: meshbus with fake mesh texts
set -euo pipefail

cd /opt/blufor-tracker
source .venv/bin/activate
[ -f /etc/blufor-tracker.env ] && set -a && . /etc/blufor-tracker.env && set +a

exec python -m bridge.mqtt_shim "$@"
