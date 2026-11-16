#!/usr/bin/env bash
# Install the Mesh-TAK bridge + OpenTAKServer on the Pi. Safe to re-run (idempotent,
# non-interactive). Does NOT enable mesh-bridge at boot: pi-profiles starts it.
# Usage: sudo bash deploy/install.sh
#        sudo HOTSPOT_SSID=MyTAK HOTSPOT_PASS=secret123 bash deploy/install.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
APP_DIR="/opt/blufor-tracker"
DATA_DIR="/var/lib/blufor-tracker"
ENV_FILE="/etc/blufor-tracker.env"
USER="${SUDO_USER:-$(whoami)}"
USER_HOME=$(eval echo "~$USER")

# Secrets live in $ENV_FILE, never in the repo. Create it from the example on first run.
if [ ! -f "$ENV_FILE" ]; then
    install -m 600 "$REPO_DIR/deploy/blufor-tracker.env.example" "$ENV_FILE"
fi
# Values from the environment win over the env file
_cli_ssid="${HOTSPOT_SSID:-}"
_cli_pass="${HOTSPOT_PASS:-}"
set -a; . "$ENV_FILE"; set +a
HOTSPOT_SSID="${_cli_ssid:-${HOTSPOT_SSID:-BFT-TAK}}"
HOTSPOT_PASS="${_cli_pass:-${HOTSPOT_PASS:-}}"
if [ -z "$HOTSPOT_PASS" ]; then
    HOTSPOT_PASS="$(tr -dc 'a-km-np-z2-9' </dev/urandom | head -c 12 || true)"
    echo "Generated a hotspot password (saved in $ENV_FILE)."
fi
if [ "${#HOTSPOT_PASS}" -lt 8 ]; then
    echo "ERROR: HOTSPOT_PASS must be at least 8 characters (WPA2)." >&2
    exit 1
fi
# Persist the effective hotspot settings (quoted; avoid a single quote in the password)
_tmp="$(mktemp)"
grep -v '^HOTSPOT_SSID=\|^HOTSPOT_PASS=' "$ENV_FILE" > "$_tmp" || true
printf "HOTSPOT_SSID='%s'\nHOTSPOT_PASS='%s'\n" "$HOTSPOT_SSID" "$HOTSPOT_PASS" >> "$_tmp"
install -m 600 "$_tmp" "$ENV_FILE"
rm -f "$_tmp"

echo "=== Mesh-TAK Bridge — Pi Install ==="
echo "Installing as user: $USER"

# 1. System packages
echo "[1/7] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl

# 2. Copy project files
echo "[2/7] Copying project to $APP_DIR..."
mkdir -p "$APP_DIR" "$DATA_DIR"
if [ "$REPO_DIR" != "$APP_DIR" ]; then
    rm -rf "$APP_DIR/bridge"
    cp -r "$REPO_DIR/bridge" "$REPO_DIR/requirements.txt" "$REPO_DIR/deploy" "$REPO_DIR/profiles.d" "$APP_DIR/"
fi
chown -R "$USER:$USER" "$APP_DIR" "$DATA_DIR"
# The launcher reads $DATA_DIR/channel-url (channel QR, contains the PSK) via group isr-launcher.
# setgid dir: files the bridge creates there inherit the group; others cannot read it.
getent group isr-launcher >/dev/null || groupadd --system isr-launcher
usermod -aG isr-launcher "$USER"
chgrp isr-launcher "$DATA_DIR"
chmod 2750 "$DATA_DIR"
[ -f "$DATA_DIR/channel-url" ] && chgrp isr-launcher "$DATA_DIR/channel-url" && chmod 0640 "$DATA_DIR/channel-url"

# 3. Bridge venv + deps
echo "[3/7] Setting up bridge Python environment..."
[ -x "$APP_DIR/.venv/bin/python" ] || sudo -u "$USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip -q
sudo -u "$USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

# 4. OpenTAKServer
echo "[4/7] Installing OpenTAKServer..."
OTS_JUST_INSTALLED=0
# Installed-but-stopped (e.g. pi-profiles switched it off) must not re-run the OTS installer
if systemctl list-unit-files opentakserver.service 2>/dev/null | grep -q '^opentakserver.service'; then
    echo "OpenTAKServer already installed — skipping."
else
    echo "Running OpenTAKServer Pi installer as $USER..."
    echo "This installs PostgreSQL, RabbitMQ, nginx, and OpenTAKServer, and runs apt upgrade."
    # Never `curl | bash`: the installer reads its answers from /dev/tty, which loops forever with
    # no terminal. ots_install.sh downloads it, prints its sha256, answers under a pseudo-tty and
    # aborts on a runaway/stalled run (see the script header).
    OTS_USER="$USER" bash "$SCRIPT_DIR/ots_install.sh"
    OTS_JUST_INSTALLED=1

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
# Modify in place when it exists: deleting it would drop an SSH session that
# came in over the hotspot and kill this script half-way.
HOTSPOT_SETTINGS=(
    autoconnect yes
    wifi.mode ap
    wifi.ssid "$HOTSPOT_SSID"
    wifi.band bg
    wifi.channel 7
    ipv4.addresses 192.168.4.1/24
    ipv4.method shared
    wifi-sec.key-mgmt wpa-psk
    wifi-sec.psk "$HOTSPOT_PASS"
    connection.autoconnect-priority 100
)
if nmcli -t -f NAME connection show | grep -qx bft-hotspot; then
    nmcli connection modify bft-hotspot "${HOTSPOT_SETTINGS[@]}"
    echo "Updated existing bft-hotspot (takes effect on next reconnect/reboot)."
else
    nmcli connection add type wifi ifname wlan0 con-name bft-hotspot "${HOTSPOT_SETTINGS[@]}"
fi

# 7. Systemd service + permissions
echo "[7/7] Installing systemd service (not enabled at boot — pi-profiles starts it)..."

# Template the service file with the correct user
sed "s/User=.*/User=$USER/" "$APP_DIR/deploy/mesh-bridge.service" > /etc/systemd/system/mesh-bridge.service
systemctl daemon-reload

# Drop our standalone profile into pi-profiles if it is installed
if [ -d "/opt/pi-profiles/profiles.d" ]; then
    cp "$REPO_DIR/profiles.d/"*.conf /opt/pi-profiles/profiles.d/
    echo "Installed pi-profiles profile: blufor-tracker"
fi

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
echo "  3. Start it:  sudo pi-profiles blufor-tracker"
echo "     (or without pi-profiles: sudo systemctl start mesh-bridge)"
echo ""
echo "WiFi hotspot: $HOTSPOT_SSID  (password in $ENV_FILE: sudo grep HOTSPOT_PASS $ENV_FILE)"
echo "OTS Web UI:   http://192.168.4.1"
echo "TAK Server:   192.168.4.1:8087 (TCP)"
echo ""
if [ "$OTS_JUST_INSTALLED" = 1 ] || [ -f /var/run/reboot-required ]; then
    echo "REBOOT NEEDED: the OpenTAKServer installer ran 'apt upgrade', which can install a new kernel."
    echo "  sudo reboot"
    echo ""
fi
if [ -n "$PI_IPS" ]; then
    echo "Pi IP addresses:"
    echo "$PI_IPS" | while read -r ip; do echo "  $ip"; done
    echo ""
fi
echo "See README.md for full setup instructions."
