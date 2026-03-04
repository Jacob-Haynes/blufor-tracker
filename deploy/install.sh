#!/usr/bin/env bash
# Run this once on the Pi to install the Blue Force Tracker.
# Usage: sudo bash deploy/install.sh
set -euo pipefail

APP_DIR="/opt/blufor-tracker"
SERVICE_NAME="bft"
USER="pi"

echo "=== Blue Force Tracker — Pi Install ==="

# 1. Install system dependencies
echo "[1/6] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git

# 2. Copy project files
echo "[2/6] Copying project to $APP_DIR..."
mkdir -p "$APP_DIR"
cp -r server frontend requirements.txt deploy "$APP_DIR/"
chown -R "$USER:$USER" "$APP_DIR"

# 3. Create venv and install Python deps
echo "[3/6] Setting up Python virtual environment..."
sudo -u "$USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip -q
sudo -u "$USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

# 4. Make start script executable
chmod +x "$APP_DIR/deploy/start.sh"

# 5. Install and enable systemd service
echo "[4/6] Installing systemd service..."
cp "$APP_DIR/deploy/bft.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

# 6. Add pi user to dialout group (serial port access)
echo "[5/6] Granting serial port access..."
usermod -aG dialout "$USER"

echo "[6/6] Done!"
echo ""
echo "The BFT server will start automatically on boot."
echo "  - Plug in a Meshtastic node via USB and it auto-detects."
echo "  - No node plugged in? It falls back to simulation mode."
echo ""
echo "Commands:"
echo "  sudo systemctl start bft      # start now"
echo "  sudo systemctl stop bft       # stop"
echo "  sudo systemctl status bft     # check status"
echo "  journalctl -u bft -f          # live logs"
echo ""
echo "Access the tracker at http://<pi-ip>:8000"
echo ""
echo "Reboot or run 'sudo systemctl start bft' to get going."
