#!/usr/bin/env bash
# Verify OTS SSL is active and guide through client certificate setup.
# Run this ON the Pi after the WireGuard tunnel is established.
# Usage: sudo bash deploy/remote/enable-tls.sh
set -euo pipefail

USER="${SUDO_USER:-$(whoami)}"
USER_HOME=$(eval echo "~$USER")
OTS_CONFIG="$USER_HOME/ots/config.yml"
OTS_CA="$USER_HOME/ots/ca"
SSL_PORT=8089
TCP_PORT=8087

echo "=== OTS TLS Verification ==="

# 1. Check OTS is running
echo "[1/3] Checking OpenTAKServer..."
if systemctl is-active --quiet opentakserver; then
    echo "OTS is running."
else
    echo "ERROR: OpenTAKServer is not running."
    echo "Start it: sudo systemctl start opentakserver"
    exit 1
fi

# 2. Check SSL port is listening
echo "[2/3] Checking SSL port $SSL_PORT..."
if ss -tlnp | grep -q ":$SSL_PORT "; then
    echo "OTS is listening on SSL port $SSL_PORT."
else
    echo "WARNING: OTS is not listening on port $SSL_PORT."
    echo ""
    echo "OTS should listen on $SSL_PORT by default for SSL connections."
    echo "Check your config: $OTS_CONFIG"
    echo "Restart OTS: sudo systemctl restart opentakserver"
    echo ""
    echo "If the port still isn't listening after restart, OTS may need"
    echo "certificates generated first. Visit the OTS web UI and go to"
    echo "the Certificates section."
    exit 1
fi

# 3. Check CA exists
echo "[3/3] Checking certificate authority..."
if [ -d "$OTS_CA" ]; then
    echo "CA directory exists at $OTS_CA"
    if [ -f "$OTS_CA/ca.pem" ] || [ -f "$OTS_CA/ca-do-not-delete.pem" ]; then
        echo "CA certificate found."
    else
        echo "WARNING: CA directory exists but no CA certificate found."
        echo "Visit the OTS web UI → Certificates to generate the CA."
    fi
else
    echo "WARNING: No CA directory at $OTS_CA"
    echo "OTS generates the CA on first start. Try restarting OTS:"
    echo "  sudo systemctl restart opentakserver"
fi

# Test WireGuard tunnel connectivity
RELAY_WG_IP="10.0.0.1"
echo ""
echo "Checking WireGuard tunnel..."
if ping -c 1 -W 3 "$RELAY_WG_IP" &>/dev/null; then
    echo "Tunnel is UP — relay reachable at $RELAY_WG_IP"
else
    echo "WARNING: Cannot reach relay at $RELAY_WG_IP"
    echo "Check WireGuard: sudo wg show"
fi

echo ""
echo "============================================"
echo "  OTS TLS is Active"
echo "============================================"
echo ""
echo "Connection details for remote ATAK clients:"
echo "  Server:   RELAY_PUBLIC_IP"
echo "  Port:     $SSL_PORT"
echo "  Protocol: SSL"
echo ""
echo "HOW TO CONNECT REMOTE ATAK CLIENTS:"
echo ""
echo "  Option A — Certificate Enrollment (easiest):"
echo "    1. Open OTS web UI: http://192.168.4.1"
echo "    2. Go to Certificates → Certificate Enrollment"
echo "    3. Create enrollment credentials (username + password)"
echo "    4. On the ATAK device:"
echo "       - Settings → Network → TAK Server connections → Add"
echo "       - Address: RELAY_PUBLIC_IP"
echo "       - Port: $SSL_PORT"
echo "       - Protocol: SSL"
echo "       - Enable 'Enroll for Client Certificate'"
echo "       - Enter the enrollment username and password"
echo "    5. ATAK will download and install the certificate automatically"
echo ""
echo "  Option B — Data Package (manual):"
echo "    1. Open OTS web UI: http://192.168.4.1"
echo "    2. Go to Certificates → generate a client certificate"
echo "    3. Download the data package (.zip)"
echo "    4. Transfer the .zip to the ATAK device"
echo "    5. Open the .zip — ATAK imports it automatically"
echo ""
echo "LOCAL ATAK CLIENTS (on BFT-TAK WiFi):"
echo "  Continue using 192.168.4.1:$TCP_PORT (TCP, no SSL)"
echo "  No certificates needed for local connections."
echo ""