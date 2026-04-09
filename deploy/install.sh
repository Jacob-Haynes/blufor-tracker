#!/usr/bin/env bash
# Run this once on the Pi to install the Mesh↔TAK bridge.
# Usage: sudo bash deploy/install.sh
set -euo pipefail

APP_DIR="/opt/blufor-tracker"
USER="jhh-pi"
HOTSPOT_SSID="${HOTSPOT_SSID:-BFT-TAK}"
HOTSPOT_PASS="${HOTSPOT_PASS:-bluforce24}"

echo "=== Mesh↔TAK Bridge — Pi Install ==="

# 1. System packages
echo "[1/8] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git hostapd dnsmasq

# 2. Copy project files
echo "[2/8] Copying project to $APP_DIR..."
mkdir -p "$APP_DIR"
cp -r bridge requirements.txt deploy "$APP_DIR/"
chown -R "$USER:$USER" "$APP_DIR"

# 3. Bridge venv + deps
echo "[3/8] Setting up bridge Python environment..."
sudo -u "$USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip -q
sudo -u "$USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

# 4. FreeTAKServer (separate venv — needs Python <=3.12)
echo "[4/8] Installing FreeTAKServer..."
FTS_VENV="$APP_DIR/.fts-venv"
FTS_PYTHON=""
for py in python3.11 python3.12; do
    if command -v "$py" &>/dev/null; then
        FTS_PYTHON="$py"
        break
    fi
done
if [ -z "$FTS_PYTHON" ]; then
    echo "ERROR: FreeTAKServer requires Python 3.11 or 3.12."
    echo "Install with: sudo apt install python3.11 python3.11-venv"
    exit 1
fi
echo "Using $FTS_PYTHON for FreeTAKServer..."
sudo -u "$USER" "$FTS_PYTHON" -m venv "$FTS_VENV"
sudo -u "$USER" "$FTS_VENV/bin/pip" install --upgrade pip -q
sudo -u "$USER" "$FTS_VENV/bin/pip" install FreeTAKServer -q || {
    echo "WARNING: FreeTAKServer install failed. You may need Python 3.11."
    echo "Install manually: $FTS_VENV/bin/pip install FreeTAKServer"
}

# 5. FreeTAKServer-UI (web dashboard)
echo "[5/8] Installing FreeTAKServer-UI..."
sudo -u "$USER" "$FTS_VENV/bin/pip" install freetakserver-ui -q || {
    echo "WARNING: FreeTAKServer-UI install failed."
    echo "Install manually: $FTS_VENV/bin/pip install freetakserver-ui"
}

# 6. WiFi hotspot
echo "[6/8] Configuring WiFi hotspot..."
sed "s/^ssid=.*/ssid=$HOTSPOT_SSID/" "$APP_DIR/deploy/hostapd.conf" > /etc/hostapd/hostapd.conf
sed "s/^wpa_passphrase=.*/wpa_passphrase=$HOTSPOT_PASS/" -i /etc/hostapd/hostapd.conf
cp "$APP_DIR/deploy/dnsmasq.conf" /etc/dnsmasq.d/bft-hotspot.conf

# Configure static IP on wlan0
cat > /etc/network/interfaces.d/wlan0 <<EOF
auto wlan0
iface wlan0 inet static
    address 192.168.4.1
    netmask 255.255.255.0
EOF

# Point hostapd to config
sed -i 's|^#\?DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd 2>/dev/null || true

# 7. Systemd units
echo "[7/8] Installing systemd services..."
cp "$APP_DIR/deploy/fts.service" /etc/systemd/system/
cp "$APP_DIR/deploy/fts-ui.service" /etc/systemd/system/
cp "$APP_DIR/deploy/mesh-bridge.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable hostapd dnsmasq fts fts-ui mesh-bridge

# 8. Serial port access
echo "[8/8] Granting serial port access..."
usermod -aG dialout "$USER"

# Make start script executable
chmod +x "$APP_DIR/deploy/start.sh"

echo ""
echo "=== Install complete ==="
echo ""
echo "Services:"
echo "  FreeTAKServer  → fts.service"
echo "  FTS Web UI     → fts-ui.service (http://192.168.4.1:5000)"
echo "  Mesh bridge    → mesh-bridge.service"
echo "  WiFi hotspot   → hostapd (SSID: $HOTSPOT_SSID)"
echo ""
echo "WiFi hotspot: $HOTSPOT_SSID / $HOTSPOT_PASS"
echo "  ATAK devices connect to WiFi, add FTS server at 192.168.4.1:8087"
echo "  Web UI: http://192.168.4.1:5000 (admin/password)"
echo ""
echo "Ethernet admin: set laptop to 192.168.1.2/24, ssh $USER@192.168.1.1"
echo ""
echo "Commands:"
echo "  sudo systemctl start fts mesh-bridge   # start now"
echo "  journalctl -u mesh-bridge -f            # bridge logs"
echo "  journalctl -u fts -f                    # FTS logs"
echo ""
echo "Reboot to start all services."
