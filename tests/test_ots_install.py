"""deploy/ots_install.sh: the OTS installer runs under a pseudo-tty with scripted answers and is
aborted, never left looping, when it misbehaves (real-Pi bug BT-1). Uses fake installers."""

import getpass
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "ots_install.sh"
pytestmark = pytest.mark.skipif(shutil.which("script") is None or os.geteuid() == 0,
                                reason="needs 'script' and a non-root user")

PROMPTS = """#!/bin/bash
for q in ZeroTier "Mumble Server" EXTRA; do
  [ "$q" = EXTRA ] && [ -z "${ASK_EXTRA:-}" ] && break
  while :; do
    read -p "Would you like to install $q? [y/n]" A < /dev/tty
    if [[ "$A" =~ [nN] ]]; then echo "ANSWER $q=n"; break; else echo "Invalid input"; fi
  done
done
echo "OpenTAKServer Installed!"
"""


def run(tmp_path, body, **env):
    inst = tmp_path / "installer.sh"
    inst.write_text(body)
    log = tmp_path / "ots.log"
    e = dict(os.environ, OTS_USER=getpass.getuser(), OTS_INSTALLER_URL=f"file://{inst}", OTS_LOG=str(log),
             OTS_POLL_S="1", **env)
    p = subprocess.run(["bash", str(SCRIPT)], env=e, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       timeout=60)
    return p, log.read_text(errors="replace") if log.exists() else ""


def test_answers_no_to_zerotier_and_mumble_without_a_tty(tmp_path):
    p, log = run(tmp_path, PROMPTS)
    assert p.returncode == 0, p.stderr
    assert "ANSWER ZeroTier=n" in log and "ANSWER Mumble Server=n" in log
    assert "OTS installer sha256: " in p.stdout and "reboot" in p.stdout


def test_unanswered_prompt_is_aborted_not_looped(tmp_path):
    p, log = run(tmp_path, PROMPTS, ASK_EXTRA="1", OTS_STALL_S="4")
    assert p.returncode == 3
    assert "Stopping the installer" in p.stderr and log.count("\n") < 1000


def test_runaway_output_is_aborted(tmp_path):
    p, _ = run(tmp_path, "#!/bin/bash\nwhile :; do echo spam; done\n", OTS_MAX_LINES="2000")
    assert p.returncode == 3 and "more than 2000 lines" in p.stderr


def test_sha256_mismatch_refuses_to_run(tmp_path):
    p, log = run(tmp_path, PROMPTS, OTS_INSTALLER_SHA256="0" * 64)
    assert p.returncode == 1 and "does not match" in p.stderr and "ANSWER" not in log


def test_never_pipes_curl_to_bash():
    import re

    for f in (SCRIPT, SCRIPT.parent / "install.sh"):
        code = [ln for ln in f.read_text().splitlines() if not ln.lstrip().startswith("#")]
        assert not any(re.search(r"curl[^|]*\|\s*(sudo\s+)?bash", ln) for ln in code), f
