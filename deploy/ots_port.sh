#!/usr/bin/env bash
# Make OpenTAKServer's plain-TCP CoT port the kit's TAK port (8087), idempotently.
#
#     bash deploy/ots_port.sh [CONFIG] [PORT]
#     CONFIG defaults to ~/ots/config.yml, PORT to $OTS_TAK_PORT or 8087.
#
# OTS ships with OTS_TCP_STREAMING_PORT 8088; every kit tool sends CoT to TAK_PORT 8087.
# This sets the top-level OTS_TCP_STREAMING_PORT key in config.yml:
#   * already right: changes nothing                                   -> exit 0
#   * different or missing: writes a timestamped backup
#     (config.yml.bak-YYYYmmdd-HHMMSS), then sets it in place
#     (owner and mode are kept)                                        -> exit 2 ("changed")
#   * no config.yml: changes nothing, prints how to fix it             -> exit 1
# It prints what it did. deploy/install.sh runs it on every install and restarts the
# running OTS units only on exit 2.
set -euo pipefail

CONFIG="${1:-$HOME/ots/config.yml}"
PORT="${2:-${OTS_TAK_PORT:-8087}}"
KEY="OTS_TCP_STREAMING_PORT"

case "$PORT" in ''|*[!0-9]*) echo "ERROR: port '$PORT' is not a number" >&2; exit 1;; esac

if [ ! -f "$CONFIG" ]; then
    echo "WARNING: $CONFIG not found, so the OTS CoT port could not be set to $PORT." >&2
    echo "  OTS writes it on first start (or: cd ~/.opentakserver_venv/lib/python3.*/site-packages/opentakserver && flask ots generate-config)," >&2
    echo "  then re-run: bash deploy/ots_port.sh $CONFIG $PORT" >&2
    exit 1
fi

# Top-level "KEY: value" lines, value optionally quoted, optional trailing comment
pattern="^${KEY}:[[:space:]]*['\"]?([0-9]*)['\"]?[[:space:]]*(#.*)?$"
current=$(grep -E "^${KEY}:" "$CONFIG" | sed -E "s/${pattern}/\\1/" || true)
count=$(printf '%s\n' "$current" | grep -c . || true)

if [ "$count" = "1" ] && [ "$current" = "$PORT" ]; then
    echo "OTS CoT port: $KEY is already $PORT in $CONFIG (no change)."
    exit 0
fi

backup="$CONFIG.bak-$(date +%Y%m%d-%H%M%S)"
cp -p "$CONFIG" "$backup"
tmp=$(mktemp "${TMPDIR:-/tmp}/ots-config.XXXXXX")
if [ "$count" = "0" ]; then
    cat "$CONFIG" > "$tmp"
    [ -s "$tmp" ] && [ -n "$(tail -c1 "$tmp")" ] && echo >> "$tmp"
    echo "$KEY: $PORT" >> "$tmp"
    what="added $KEY: $PORT (it was not set, so OTS used its default 8088)"
else
    # replace every top-level occurrence; a duplicate key would otherwise win unpredictably
    awk -v key="$KEY" -v port="$PORT" '
        $0 ~ "^" key ":" { print key ": " port; next } { print }' "$CONFIG" > "$tmp"
    what="changed $KEY from $(printf '%s' "$current" | tr '\n' ' ' | sed 's/ $//') to $PORT"
    [ "$count" -gt 1 ] && what="$what ($count duplicate lines collapsed to the same value)"
fi
cat "$tmp" > "$CONFIG"  # write in place: keeps owner, group and mode
rm -f "$tmp"
echo "OTS CoT port: $what in $CONFIG (backup: $backup)."
exit 2
