#!/usr/bin/env bash
# Add the Pi as a WireGuard peer on the relay.
# Run this ON the GCP instance after pi-tunnel.sh outputs the Pi's public key.
# Usage: sudo bash add-peer.sh PI_PUBLIC_KEY
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo bash add-peer.sh PI_PUBLIC_KEY"
    exit 1
fi

PI_KEY="${1:-}"
if [ -z "$PI_KEY" ]; then
    echo "Usage: sudo bash add-peer.sh PI_PUBLIC_KEY"
    echo ""
    echo "The Pi's public key is shown at the end of pi-tunnel.sh output."
    exit 1
fi

PI_WG_IP="10.0.0.2"
WG_CONF="/etc/wireguard/wg0.conf"

# Remove placeholder comment and add real peer
sed -i '/^# Pi peer/d; /^# \[Peer\]/d; /^# PublicKey/d; /^# AllowedIPs/d' "$WG_CONF"

cat >> "$WG_CONF" <<EOF

[Peer]
PublicKey = $PI_KEY
AllowedIPs = $PI_WG_IP/32
EOF

# Restart WireGuard to pick up the new peer
systemctl restart wg-quick@wg0

echo "Pi peer added. Checking tunnel..."
sleep 2

if wg show wg0 | grep -q "$PI_KEY"; then
    echo "WireGuard peer configured."
    echo ""
    echo "Once the Pi's tunnel is up, verify with:"
    echo "  ping -c 3 $PI_WG_IP"
else
    echo "WARNING: Peer added to config but not yet connected."
    echo "Check that pi-tunnel.sh has been run on the Pi."
fi