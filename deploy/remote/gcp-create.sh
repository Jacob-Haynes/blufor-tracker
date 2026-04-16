#!/usr/bin/env bash
# Create a GCP e2-micro instance as a WireGuard relay for TAK remote access.
# Usage: bash deploy/remote/gcp-create.sh --project PROJECT_ID --zone ZONE
#
# Example:
#   bash deploy/remote/gcp-create.sh --project my-project --zone europe-west2-a
#   bash deploy/remote/gcp-create.sh --project my-project --zone europe-west2-a --configuration cathex
set -euo pipefail

NAME="tak-relay"
PROJECT=""
ZONE=""
GCLOUD_CONF=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --project)  PROJECT="$2";    shift 2 ;;
        --zone)     ZONE="$2";       shift 2 ;;
        --name)     NAME="$2";       shift 2 ;;
        --configuration) GCLOUD_CONF="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [ -z "$PROJECT" ] || [ -z "$ZONE" ]; then
    echo "Usage: $0 --project PROJECT_ID --zone ZONE [--name NAME] [--configuration GCLOUD_CONFIG]"
    echo ""
    echo "Required:"
    echo "  --project       GCP project ID"
    echo "  --zone          GCP zone (e.g. europe-west2-a)"
    echo ""
    echo "Optional:"
    echo "  --name          Instance name (default: tak-relay)"
    echo "  --configuration gcloud config to use (e.g. cathex)"
    exit 1
fi

# Build gcloud flags
GC_FLAGS="--project=$PROJECT"
if [ -n "$GCLOUD_CONF" ]; then
    GC_FLAGS="$GC_FLAGS --configuration=$GCLOUD_CONF"
fi

REGION="${ZONE%-*}"

echo "=== Creating TAK Relay on GCP ==="
echo "Project: $PROJECT"
echo "Zone:    $ZONE"
echo "Name:    $NAME"
echo ""

# 1. Reserve a static IP (survives instance recreation)
echo "[1/4] Reserving static IP..."
if gcloud compute addresses describe "$NAME-ip" --region="$REGION" $GC_FLAGS &>/dev/null; then
    echo "Static IP '$NAME-ip' already exists."
else
    gcloud compute addresses create "$NAME-ip" \
        --region="$REGION" \
        $GC_FLAGS
fi
RELAY_IP=$(gcloud compute addresses describe "$NAME-ip" --region="$REGION" --format='value(address)' $GC_FLAGS)
echo "Static IP: $RELAY_IP"

# 2. Create firewall rules
echo "[2/4] Creating firewall rules..."
for RULE_NAME in "${NAME}-wireguard" "${NAME}-tak-ssl"; do
    if gcloud compute firewall-rules describe "$RULE_NAME" $GC_FLAGS &>/dev/null; then
        echo "Firewall rule '$RULE_NAME' already exists."
    else
        if [ "$RULE_NAME" = "${NAME}-wireguard" ]; then
            gcloud compute firewall-rules create "$RULE_NAME" \
                --allow=udp:51820 \
                --target-tags="$NAME" \
                --description="WireGuard tunnel for TAK relay" \
                $GC_FLAGS
        else
            gcloud compute firewall-rules create "$RULE_NAME" \
                --allow=tcp:8089 \
                --target-tags="$NAME" \
                --description="TAK server SSL connections (OTS default port)" \
                $GC_FLAGS
        fi
    fi
done

# 3. Create the instance
echo "[3/4] Creating e2-micro instance..."
if gcloud compute instances describe "$NAME" --zone="$ZONE" $GC_FLAGS &>/dev/null; then
    echo "Instance '$NAME' already exists."
else
    gcloud compute instances create "$NAME" \
        --zone="$ZONE" \
        --machine-type=e2-micro \
        --image-family=ubuntu-2404-lts-amd64 \
        --image-project=ubuntu-os-cloud \
        --boot-disk-size=10GB \
        --tags="$NAME" \
        --address="$RELAY_IP" \
        --metadata=startup-script='#!/bin/bash
apt-get update -qq && apt-get install -y -qq wireguard iptables-persistent' \
        $GC_FLAGS
fi

# 4. Wait for instance to be ready
echo "[4/4] Waiting for instance to be ready..."
for i in $(seq 1 30); do
    if gcloud compute ssh "$NAME" --zone="$ZONE" --command="echo ready" $GC_FLAGS &>/dev/null; then
        break
    fi
    sleep 5
done

echo ""
echo "============================================"
echo "  GCP Relay Instance Created"
echo "============================================"
echo ""
echo "Instance: $NAME"
echo "Zone:     $ZONE"
echo "Public IP: $RELAY_IP (static — won't change)"
echo ""
echo "Next step — SSH in and run the relay setup script:"
echo ""
echo "  gcloud compute scp deploy/remote/relay-setup.sh $NAME:~ --zone=$ZONE $GC_FLAGS"
echo "  gcloud compute ssh $NAME --zone=$ZONE $GC_FLAGS"
echo "  sudo bash relay-setup.sh"
echo ""