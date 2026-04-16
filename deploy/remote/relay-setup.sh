#!/usr/bin/env bash
# Configure WireGuard relay on the GCP instance.
# Run this ON the GCP instance after creating it with gcp-create.sh.
# Usage: sudo bash relay-setup.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo bash relay-setup.sh"
    exit 1
fi

WG_DIR="/etc/wireguard"
WG_CONF="$WG_DIR/wg0.conf"
RELAY_WG_IP="10.0.0.1"
PI_WG_IP="10.0.0.2"
WG_PORT=51820
TAK_PORT=8089

echo "=== TAK Relay — WireGuard Setup ==="

# 1. Install WireGuard (may already be installed by startup script)
echo "[1/5] Installing WireGuard..."
apt-get update -qq
apt-get install -y -qq wireguard iptables-persistent

# 2. Generate keys
echo "[2/5] Generating WireGuard keys..."
if [ -f "$WG_DIR/relay_private.key" ]; then
    echo "Keys already exist — reusing."
else
    wg genkey | tee "$WG_DIR/relay_private.key" | wg pubkey > "$WG_DIR/relay_public.key"
    chmod 600 "$WG_DIR/relay_private.key"
fi

RELAY_PRIVATE=$(cat "$WG_DIR/relay_private.key")
RELAY_PUBLIC=$(cat "$WG_DIR/relay_public.key")

# 3. Create WireGuard config (Pi peer added later)
echo "[3/5] Creating WireGuard config..."
cat > "$WG_CONF" <<EOF
[Interface]
Address = $RELAY_WG_IP/24
ListenPort = $WG_PORT
PrivateKey = $RELAY_PRIVATE

# Pi peer — add PublicKey after running pi-tunnel.sh on the Pi
# [Peer]
# PublicKey = <PI_PUBLIC_KEY>
# AllowedIPs = $PI_WG_IP/32
EOF
chmod 600 "$WG_CONF"

# 4. Enable IP forwarding and iptables rules
echo "[4/5] Configuring IP forwarding and port forwarding..."

# Enable IP forwarding
sed -i 's/#net.ipv4.ip_forward=1/net.ipv4.ip_forward=1/' /etc/sysctl.conf
grep -q "^net.ipv4.ip_forward=1" /etc/sysctl.conf || echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf
sysctl -w net.ipv4.ip_forward=1 > /dev/null

# Get the public interface
PUB_IFACE=$(ip route get 8.8.8.8 | grep -oP 'dev \K\S+')

# Port forward: public:8443 -> Pi WireGuard IP:8443
iptables -t nat -C PREROUTING -i "$PUB_IFACE" -p tcp --dport "$TAK_PORT" -j DNAT --to-destination "$PI_WG_IP:$TAK_PORT" 2>/dev/null \
    || iptables -t nat -A PREROUTING -i "$PUB_IFACE" -p tcp --dport "$TAK_PORT" -j DNAT --to-destination "$PI_WG_IP:$TAK_PORT"

# Masquerade return traffic
iptables -t nat -C POSTROUTING -o wg0 -j MASQUERADE 2>/dev/null \
    || iptables -t nat -A POSTROUTING -o wg0 -j MASQUERADE

# Allow forwarding to WireGuard
iptables -C FORWARD -i "$PUB_IFACE" -o wg0 -p tcp --dport "$TAK_PORT" -j ACCEPT 2>/dev/null \
    || iptables -A FORWARD -i "$PUB_IFACE" -o wg0 -p tcp --dport "$TAK_PORT" -j ACCEPT
iptables -C FORWARD -i wg0 -o "$PUB_IFACE" -m state --state ESTABLISHED,RELATED -j ACCEPT 2>/dev/null \
    || iptables -A FORWARD -i wg0 -o "$PUB_IFACE" -m state --state ESTABLISHED,RELATED -j ACCEPT

# Save iptables rules (persist across reboot)
netfilter-persistent save

# 5. Start WireGuard (without peer for now — will restart after adding Pi)
echo "[5/5] Enabling WireGuard..."
systemctl enable wg-quick@wg0
systemctl start wg-quick@wg0 2>/dev/null || true

echo ""
echo "============================================"
echo "  Relay Setup Complete"
echo "============================================"
echo ""
echo "Relay WireGuard IP: $RELAY_WG_IP"
echo "Relay public key:   $RELAY_PUBLIC"
echo "WireGuard port:     $WG_PORT"
echo "TAK forward:        :$TAK_PORT -> $PI_WG_IP:$TAK_PORT"
echo ""
echo "SAVE THESE VALUES — you need them for the Pi setup:"
echo ""
echo "  Relay public key: $RELAY_PUBLIC"
echo "  Relay public IP:  (the static IP from gcp-create.sh)"
echo ""
echo "Next step — on the Pi, run:"
echo ""
echo "  sudo bash deploy/remote/pi-tunnel.sh \\"
echo "    --relay-ip RELAY_PUBLIC_IP \\"
echo "    --relay-key $RELAY_PUBLIC"
echo ""
echo "Then come back here and add the Pi's public key:"
echo ""
echo "  sudo bash add-peer.sh PI_PUBLIC_KEY"
echo ""