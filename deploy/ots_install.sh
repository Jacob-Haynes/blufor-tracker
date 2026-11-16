#!/usr/bin/env bash
# Run the OpenTAKServer Raspberry Pi installer non-interactively, safely.
#
# Called by deploy/install.sh step 4 (as root). It can also be run alone:
#     sudo bash deploy/ots_install.sh
#
# Why not `curl ... | bash`: the OTS installer reads its answers from /dev/tty, not stdin,
# and doesn't honour environment variables for them (it sets INSTALL_ZEROTIER="" and
# INSTALL_MUMBLE="" just before prompting). With no controlling terminal (pi-profiles bootstrap,
# systemd, cron) every `read < /dev/tty` fails at once and its "[y/n]" loops spin forever:
# about 645k log lines in 5 minutes on a real Pi.
#
# So this script:
#   1. downloads the installer to a temp file (never piped to bash) and prints its sha256;
#      set OTS_INSTALLER_SHA256 to require a specific hash;
#   2. runs it as the OTS user under a pseudo-terminal (`script`) with scripted answers:
#      "n" to ZeroTier, "n" to Mumble Server. If PostgreSQL already has an `ots` role (a
#      half-finished earlier install), the installer asks for its password: give it in
#      OTS_PG_PASSWORD, otherwise this script stops before starting;
#   3. watches the run and aborts it (exit 3) if it prints more than OTS_MAX_LINES lines, repeats
#      "Invalid input" (a prompt that didn't get an answer), goes OTS_STALL_MIN minutes with no new
#      output, or runs longer than OTS_MAX_MIN minutes. It never loops.
#
# Env: OTS_USER (default $SUDO_USER), OTS_INSTALLER_URL, OTS_INSTALLER_SHA256, OTS_PG_PASSWORD,
#      OTS_LOG (default /var/log/blufor-tracker/ots-install.log), OTS_MAX_LINES (100000),
#      OTS_STALL_MIN (20), OTS_MAX_MIN (90), OTS_INVALID_MAX (5).
#
# The installer runs `apt upgrade -y`, which can install a new kernel: reboot afterwards.
set -euo pipefail

OTS_USER="${OTS_USER:-${SUDO_USER:-$(whoami)}}"
OTS_INSTALLER_URL="${OTS_INSTALLER_URL:-https://i.opentakserver.io/raspberry_pi_installer}"
OTS_LOG="${OTS_LOG:-/var/log/blufor-tracker/ots-install.log}"
OTS_MAX_LINES="${OTS_MAX_LINES:-100000}"
OTS_STALL_MIN="${OTS_STALL_MIN:-20}"
OTS_MAX_MIN="${OTS_MAX_MIN:-90}"
OTS_INVALID_MAX="${OTS_INVALID_MAX:-5}"
POLL_S="${OTS_POLL_S:-2}"
STALL_S="${OTS_STALL_S:-$((OTS_STALL_MIN * 60))}"  # OTS_STALL_S: seconds, for tests

die() { echo "ERROR (OpenTAKServer install): $*" >&2; exit "${2:-1}"; }

if [ "$OTS_USER" = "root" ]; then
    die "the OTS installer must run as the normal user OTS will run as, not root. Run: sudo -u <user> ... or set OTS_USER"
fi
command -v script >/dev/null || die "'script' (util-linux/bsdutils) is missing, so the installer can't get a pseudo-terminal. Run step 4 by hand in a terminal: curl -Ls $OTS_INSTALLER_URL -o /tmp/ots.sh && bash /tmp/ots.sh"

# ── answers ──────────────────────────────────────────────────────
ANSWERS=""
if command -v psql >/dev/null && id postgres >/dev/null 2>&1; then
    role=$(su postgres -c "psql -tXAc \"SELECT 1 FROM pg_roles WHERE rolname='ots'\"" 2>/dev/null || true)
    if [ "$role" = "1" ]; then
        if [ -z "${OTS_PG_PASSWORD:-}" ]; then
            die "PostgreSQL already has an 'ots' role (an earlier OTS install), and the installer would ask for its password. Set OTS_PG_PASSWORD to it and re-run, or run the OTS installer by hand in a terminal."
        fi
        ANSWERS="${OTS_PG_PASSWORD}"$'\n'
    fi
fi
ANSWERS+=$'n\nn\n'  # ZeroTier? n. Mumble Server? n.

# ── download, never pipe ─────────────────────────────────────────
WORK=$(mktemp -d /tmp/ots-installer.XXXXXX)
chmod 755 "$WORK"
INSTALLER="$WORK/raspberry_pi_installer.sh"
ANSWER_FILE="$WORK/answers"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

curl -fLsS --retry 3 --max-time 120 "$OTS_INSTALLER_URL" -o "$INSTALLER" || die "could not download $OTS_INSTALLER_URL"
chmod 644 "$INSTALLER"
SHA=$( (sha256sum "$INSTALLER" 2>/dev/null || shasum -a 256 "$INSTALLER") | awk '{print $1}')
echo "OTS installer: $OTS_INSTALLER_URL"
echo "OTS installer sha256: $SHA ($(wc -l <"$INSTALLER") lines)"
if [ -n "${OTS_INSTALLER_SHA256:-}" ] && [ "$SHA" != "$OTS_INSTALLER_SHA256" ]; then
    die "installer sha256 $SHA does not match OTS_INSTALLER_SHA256=$OTS_INSTALLER_SHA256"
fi
head -1 "$INSTALLER" | grep -q '^#!.*bash' || die "downloaded file does not look like a bash script"
umask 077
printf '%s' "$ANSWERS" > "$ANSWER_FILE"
chown "$OTS_USER" "$ANSWER_FILE"
umask 022

mkdir -p "$(dirname "$OTS_LOG")"
: > "$OTS_LOG"
chmod 640 "$OTS_LOG"

# ── run under a pseudo-terminal ──────────────────────────────────
# util-linux script: script -qec CMD FILE.  BSD/macOS script: script -q FILE CMD...
CMD="bash $(printf %q "$INSTALLER")"
if script --version 2>/dev/null | grep -q util-linux; then
    RUN=(script -qec "$CMD" /dev/null)
else
    RUN=(script -q /dev/null bash "$INSTALLER")
fi
echo "Running the OTS installer as $OTS_USER under a pseudo-terminal (log: $OTS_LOG)."
echo "Scripted answers: ZeroTier n, Mumble n. This takes 10-30 minutes."

# setsid: own process group, so an abort kills the installer and everything it started
if [ "$(id -u)" = "0" ] && [ "$OTS_USER" != "$(id -un)" ]; then
    setsid sudo -H -u "$OTS_USER" "${RUN[@]}" <"$ANSWER_FILE" >>"$OTS_LOG" 2>&1 &
else
    if command -v setsid >/dev/null; then
        setsid "${RUN[@]}" <"$ANSWER_FILE" >>"$OTS_LOG" 2>&1 &
    else
        "${RUN[@]}" <"$ANSWER_FILE" >>"$OTS_LOG" 2>&1 &
    fi
fi
PID=$!

abort() {
    echo "ERROR (OpenTAKServer install): $1. Stopping the installer." >&2
    kill -TERM -- "-$PID" 2>/dev/null || kill -TERM "$PID" 2>/dev/null || true
    sleep 3
    kill -KILL -- "-$PID" 2>/dev/null || kill -KILL "$PID" 2>/dev/null || true
    echo "Last lines of $OTS_LOG:" >&2
    tail -n 15 "$OTS_LOG" >&2 || true
    echo "Run step 4 by hand in a terminal instead: sudo -u $OTS_USER bash -c 'curl -Ls $OTS_INSTALLER_URL -o /tmp/ots.sh && bash /tmp/ots.sh'" >&2
    exit 3
}

start=$(date +%s)
last_change=$start
last_size=0
shown=0
while kill -0 "$PID" 2>/dev/null; do
    sleep "$POLL_S"
    now=$(date +%s)
    size=$(wc -c <"$OTS_LOG" | tr -d ' ')
    lines=$(wc -l <"$OTS_LOG" | tr -d ' ')
    if [ "$size" != "$last_size" ]; then
        last_size=$size
        last_change=$now
    fi
    # progress for the caller's log, without copying a runaway loop into it
    if [ "$lines" -gt "$shown" ]; then
        sed -n "$((shown + 1)),$((shown + 20))p" "$OTS_LOG" | tr -d '\r' | sed 's/^/  ots| /'
        [ "$((lines - shown))" -gt 20 ] && echo "  ots| ... ($((lines - shown - 20)) more lines in $OTS_LOG)"
        shown=$lines
    fi
    invalid=$(grep -c "Invalid input" "$OTS_LOG" 2>/dev/null || true)
    if [ "${invalid:-0}" -ge "$OTS_INVALID_MAX" ]; then
        abort "the installer asked a question this script did not answer ('Invalid input' x$invalid)"
    fi
    if [ "$lines" -gt "$OTS_MAX_LINES" ]; then
        abort "more than $OTS_MAX_LINES lines of output (runaway loop?)"
    fi
    if [ $((now - last_change)) -ge "$STALL_S" ]; then
        abort "no output for $((STALL_S / 60)) minutes ($STALL_S s)"
    fi
    if [ $((now - start)) -ge $((OTS_MAX_MIN * 60)) ]; then
        abort "still running after $OTS_MAX_MIN minutes"
    fi
done
set +e
wait "$PID"
rc=$?
set -e
tail -n +"$((shown + 1))" "$OTS_LOG" | tail -n 20 | tr -d '\r' | sed 's/^/  ots| /'
if [ "$rc" -ne 0 ]; then
    die "the installer exited with status $rc (see $OTS_LOG)" "$rc"
fi
if grep -q "Invalid input" "$OTS_LOG"; then
    echo "WARNING: the installer printed 'Invalid input' (an unexpected prompt); check $OTS_LOG" >&2
fi
echo "OpenTAKServer installer finished. It ran 'apt upgrade', which may have installed a new kernel: reboot when this install is done."
