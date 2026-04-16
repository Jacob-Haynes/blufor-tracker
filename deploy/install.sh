#!/usr/bin/env bash
# Run this once on the Pi to install the Mesh-TAK bridge + OpenTAKServer.
# Usage: sudo bash deploy/install.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
APP_DIR="/opt/blufor-tracker"
USER="${SUDO_USER:-$(whoami)}"
USER_HOME=$(eval echo "~$USER")
HOTSPOT_SSID="${HOTSPOT_SSID:-BFT-TAK}"
HOTSPOT_PASS="${HOTSPOT_PASS:-bluforce24}"

echo "=== Mesh-TAK Bridge — Pi Install ==="
echo "Installing as user: $USER"

# 1. System packages
echo "[1/7] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl

# 2. Copy project files
echo "[2/7] Copying project to $APP_DIR..."
mkdir -p "$APP_DIR"
cp -r "$REPO_DIR/bridge" "$REPO_DIR/requirements.txt" "$REPO_DIR/deploy" "$APP_DIR/"
chown -R "$USER:$USER" "$APP_DIR"

# 3. Bridge venv + deps
echo "[3/7] Setting up bridge Python environment..."
sudo -u "$USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip -q
sudo -u "$USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

# 4. OpenTAKServer
echo "[4/7] Installing OpenTAKServer..."
if systemctl is-active --quiet opentakserver 2>/dev/null; then
    echo "OpenTAKServer already installed and running — skipping."
else
    echo "Running OpenTAKServer Pi installer as $USER..."
    echo "This installs PostgreSQL, RabbitMQ, nginx, and OpenTAKServer."
    sudo -u "$USER" bash -c 'curl https://i.opentakserver.io/raspberry_pi_installer -Ls | bash -'

    # Configure OTS to listen on port 8087 (standard TAK port)
    OTS_CONFIG="$USER_HOME/ots/config.yml"
    if [ -f "$OTS_CONFIG" ]; then
        echo "Configuring OpenTAKServer TCP port to 8087..."
        if grep -q "OTS_TCP_STREAMING_PORT" "$OTS_CONFIG"; then
            sed -i 's/OTS_TCP_STREAMING_PORT:.*/OTS_TCP_STREAMING_PORT: 8087/' "$OTS_CONFIG"
        else
            echo "OTS_TCP_STREAMING_PORT: 8087" >> "$OTS_CONFIG"
        fi
    else
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

# 5. OTS patch: fix device_callsign |UUID suffix causing KeyError
echo "[5/7] Applying OTS meshtastic_controller patch..."
OTS_CTL=$(find "$USER_HOME/.opentakserver_venv" -path "*/controllers/meshtastic_controller.py" 2>/dev/null | head -1)
if [ -n "$OTS_CTL" ] && [ -f "$OTS_CTL" ]; then
    if grep -q 'uid = uid.split("|")' "$OTS_CTL"; then
        echo "OTS patch already applied — skipping."
    else
        sed -i '/uid = unishox2.decompress(pb.contact.device_callsign/a\            uid = uid.split("|")[0]  # BFT patch: strip ATAK pipe-UUID suffix' "$OTS_CTL"
        echo "Patched: $OTS_CTL"
    fi
else
    echo "WARNING: Could not find meshtastic_controller.py — apply patch manually after OTS starts."
    echo "See deploy/ots_patches/meshtastic_device_callsign.patch"
fi

# 6. WiFi hotspot (NetworkManager on Bookworm+)
echo "[6/7] Configuring WiFi hotspot..."
rm -f /etc/network/interfaces.d/wlan0 2>/dev/null || true
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
nmcli connection modify bft-hotspot connection.autoconnect-priority 100

# 7. Systemd service + permissions
echo "[7/7] Installing systemd service..."

# Template the service file with the correct user
sed "s/User=.*/User=$USER/" "$APP_DIR/deploy/mesh-bridge.service" > /etc/systemd/system/mesh-bridge.service
systemctl daemon-reload
systemctl enable mesh-bridge

# Serial port access for Meshtastic
usermod -aG dialout "$USER"

# Make start script executable
chmod +x "$APP_DIR/deploy/start.sh"

# Show Pi's IP addresses for the user
PI_IPS=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -v '^$' | head -5)

echo ""
echo "============================================"
echo "  Install complete!"
echo "============================================"
echo ""
echo "NEXT STEPS:"
echo ""
echo "  1. Plug in your bridge radio via USB"
echo "  2. Configure it:"
echo "       source $APP_DIR/.venv/bin/activate"
echo "       meshtastic --port /dev/ttyUSB0 --set device.role TAK"
echo "       meshtastic --port /dev/ttyUSB0 --set lora.region EU_868"
echo "       meshtastic --port /dev/ttyUSB0 --ch-set name YOUR_CHANNEL --ch-index 0"
echo "       meshtastic --port /dev/ttyUSB0 --ch-set psk random --ch-index 0"
echo "  3. Reboot: sudo reboot"
echo ""
echo "After reboot, both services start automatically."
echo ""
echo "WiFi hotspot: $HOTSPOT_SSID / $HOTSPOT_PASS"
echo "OTS Web UI:   http://192.168.4.1"
echo "TAK Server:   192.168.4.1:8087 (TCP)"
echo ""
if [ -n "$PI_IPS" ]; then
    echo "Pi IP addresses:"
    echo "$PI_IPS" | while read -r ip; do echo "  $ip"; done
    echo ""
fi
echo "See README.md for full setup instructions."
