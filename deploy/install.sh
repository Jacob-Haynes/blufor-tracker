#!/usr/bin/env bash
# Run this once on the Pi to install the Mesh↔TAK bridge + OpenTAKServer.
# Usage: sudo bash deploy/install.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
APP_DIR="/opt/blufor-tracker"
USER="jhh-pi"
USER_HOME=$(eval echo "~$USER")
HOTSPOT_SSID="${HOTSPOT_SSID:-BFT-TAK}"
HOTSPOT_PASS="${HOTSPOT_PASS:-bluforce24}"

echo "=== Mesh↔TAK Bridge — Pi Install ==="

# 1. System packages
echo "[1/6] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl

# 2. Copy project files
echo "[2/6] Copying project to $APP_DIR..."
mkdir -p "$APP_DIR"
cp -r "$REPO_DIR/bridge" "$REPO_DIR/requirements.txt" "$REPO_DIR/deploy" "$APP_DIR/"
chown -R "$USER:$USER" "$APP_DIR"

# 3. Bridge venv + deps
echo "[3/6] Setting up bridge Python environment..."
sudo -u "$USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip -q
sudo -u "$USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

# 4. OpenTAKServer
echo "[4/6] Installing OpenTAKServer..."
if systemctl is-active --quiet opentakserver 2>/dev/null; then
    echo "OpenTAKServer already installed and running — skipping."
else
    echo "Running OpenTAKServer Pi installer as $USER..."
    echo "This installs PostgreSQL, RabbitMQ, nginx, and OpenTAKServer."
    sudo -u "$USER" bash -c 'curl https://i.opentakserver.io/raspberry_pi_installer -Ls | bash -'

    # Configure OTS to listen on port 8087 (standard TAK port, matches bridge default)
    OTS_CONFIG="$USER_HOME/ots/config.yml"
    if [ -f "$OTS_CONFIG" ]; then
        echo "Configuring OpenTAKServer TCP port to 8087..."
        if grep -q "OTS_TCP_STREAMING_PORT" "$OTS_CONFIG"; then
            sed -i 's/OTS_TCP_STREAMING_PORT:.*/OTS_TCP_STREAMING_PORT: 8087/' "$OTS_CONFIG"
        else
            echo "OTS_TCP_STREAMING_PORT: 8087" >> "$OTS_CONFIG"
        fi
    else
        # Config doesn't exist yet — OTS creates it on first start
        # Start once to generate config, then modify
        echo "Starting OpenTAKServer to generate config..."
        systemctl start opentakserver || true
        sleep 5
        systemctl stop opentakserver || true
        if [ -f "$OTS_CONFIG" ]; then
            if grep -q "OTS_TCP_STREAMING_PORT" "$OTS_CONFIG"; then
                sed -i 's/OTS_TCP_STREAMING_PORT:.*/OTS_TCP_STREAMING_PORT: 8087/' "$OTS_CONFIG"
            else
                echo "OTS_TCP_STREAMING_PORT: 8087" >> "$OTS_CONFIG"
            fi
        else
            mkdir -p "$USER_HOME/ots"
            cat > "$OTS_CONFIG" <<OTSEOF
OTS_TCP_STREAMING_PORT: 8087
OTSEOF
            chown "$USER:$USER" "$OTS_CONFIG"
        fi
    fi
fi

# 5. WiFi hotspot (NetworkManager on Trixie/Bookworm)
echo "[5/6] Configuring WiFi hotspot..."

# Remove any old-style config
rm -f /etc/network/interfaces.d/wlan0 2>/dev/null || true

# Create hotspot using NetworkManager
nmcli connection delete bft-hotspot 2>/dev/null || true
nmcli connection add type wifi ifname wlan0 con-name bft-hotspot \
    autoconnect yes \
    wifi.mode ap \
    wifi.ssid "$HOTSPOT_SSID" \
    wifi.band bg \
    wifi.channel 7 \
    ipv4.addresses 192.168.4.1/24 \
    ipv4.method shared \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "$HOTSPOT_PASS"

# Ensure hotspot starts on boot with higher priority
nmcli connection modify bft-hotspot connection.autoconnect-priority 100

# 6. Systemd + permissions
echo "[6/6] Installing systemd services and permissions..."
cp "$APP_DIR/deploy/mesh-bridge.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable mesh-bridge

# Serial port access for Meshtastic
usermod -aG dialout "$USER"

# Make start script executable
chmod +x "$APP_DIR/deploy/start.sh"

echo ""
echo "=== Install complete ==="
echo ""
echo "Services:"
echo "  OpenTAKServer  → opentakserver.service (CoT TCP :8087)"
echo "  Mesh bridge    → mesh-bridge.service"
echo "  OTS Web UI     → http://192.168.4.1 (via nginx)"
echo ""
echo "WiFi hotspot: $HOTSPOT_SSID / $HOTSPOT_PASS"
echo "  ATAK devices connect to WiFi, add TAK server at 192.168.4.1:8087"
echo "  Web UI: http://192.168.4.1"
echo ""
echo "Commands:"
echo "  sudo systemctl start opentakserver mesh-bridge   # start now"
echo "  journalctl -u mesh-bridge -f                     # bridge logs"
echo "  journalctl -u opentakserver -f                   # OTS logs"
echo ""
echo "Reboot to start all services."
