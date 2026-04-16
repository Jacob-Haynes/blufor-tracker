#!/usr/bin/env bash
# Configure WireGuard tunnel on the Pi to connect to the GCP relay.
# Run this ON the Pi after relay-setup.sh has been run on the GCP instance.
# Usage: sudo bash deploy/remote/pi-tunnel.sh --relay-ip IP --relay-key KEY
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo bash $0 --relay-ip IP --relay-key KEY"
    exit 1
fi

RELAY_IP=""
RELAY_KEY=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --relay-ip)  RELAY_IP="$2";  shift 2 ;;
        --relay-key) RELAY_KEY="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [ -z "$RELAY_IP" ] || [ -z "$RELAY_KEY" ]; then
    echo "Usage: sudo bash $0 --relay-ip RELAY_PUBLIC_IP --relay-key RELAY_PUBLIC_KEY"
    echo ""
    echo "Both values are shown at the end of relay-setup.sh output."
    exit 1
fi

WG_DIR="/etc/wireguard"
WG_CONF="$WG_DIR/wg0.conf"
PI_WG_IP="10.0.0.2"
RELAY_WG_IP="10.0.0.1"
WG_PORT=51820

echo "=== Pi WireGuard Tunnel Setup ==="

# 1. Install WireGuard
echo "[1/4] Installing WireGuard..."
apt-get update -qq
apt-get install -y -qq wireguard

# 2. Generate keys
echo "[2/4] Generating WireGuard keys..."
if [ -f "$WG_DIR/pi_private.key" ]; then
    echo "Keys already exist — reusing."
else
    wg genkey | tee "$WG_DIR/pi_private.key" | wg pubkey > "$WG_DIR/pi_public.key"
    chmod 600 "$WG_DIR/pi_private.key"
fi

PI_PRIVATE=$(cat "$WG_DIR/pi_private.key")
PI_PUBLIC=$(cat "$WG_DIR/pi_public.key")

# 3. Create WireGuard config
echo "[3/4] Creating WireGuard config..."
cat > "$WG_CONF" <<EOF
[Interface]
Address = $PI_WG_IP/24
PrivateKey = $PI_PRIVATE

[Peer]
PublicKey = $RELAY_KEY
Endpoint = $RELAY_IP:$WG_PORT
AllowedIPs = $RELAY_WG_IP/32
PersistentKeepalive = 25
EOF
chmod 600 "$WG_CONF"

# 4. Enable and start
echo "[4/4] Starting WireGuard tunnel..."
systemctl enable wg-quick@wg0
systemctl restart wg-quick@wg0

sleep 2
echo ""

# Test connectivity
if ping -c 1 -W 3 "$RELAY_WG_IP" &>/dev/null; then
    echo "Tunnel is UP — relay is reachable at $RELAY_WG_IP"
else
    echo "Tunnel created but relay not responding yet."
    echo "Make sure you run add-peer.sh on the relay with this Pi's public key."
fi

echo ""
echo "============================================"
echo "  Pi Tunnel Setup Complete"
echo "============================================"
echo ""
echo "Pi WireGuard IP: $PI_WG_IP"
echo "Pi public key:   $PI_PUBLIC"
echo ""
echo "IMPORTANT — go back to the relay and add this Pi as a peer:"
echo ""
echo "  sudo bash add-peer.sh $PI_PUBLIC"
echo ""
echo "After that, the tunnel will be active. Verify with:"
echo "  ping $RELAY_WG_IP"
echo "  sudo wg show"
echo ""
echo "The tunnel starts automatically on boot."
echo ""
echo "Next step — enable TLS on OTS:"
echo "  sudo bash deploy/remote/enable-tls.sh"
echo ""
