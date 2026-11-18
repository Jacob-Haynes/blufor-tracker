"""Publish the radio's channel URL for the launcher's PIN-gated onboarding QR.

The URL (``localNode.getURL()``, e.g. ``https://meshtastic.org/e/#...``) contains the
channel PSK. It is written to ``/var/lib/blufor-tracker/channel-url`` with mode 0640 and
group ``isr-launcher``, atomically (temp file in the same directory, then rename), and it is
**never logged**.

``CHANNEL_URL_FILE`` overrides the path; set it to empty to disable.
"""

from __future__ import annotations

import grp
import logging
import os
import tempfile

logger = logging.getLogger("channel_url")

DEFAULT_PATH = "/var/lib/blufor-tracker/channel-url"
GROUP = "isr-launcher"
MODE = 0o640


def target_path() -> str:
    return os.environ.get("CHANNEL_URL_FILE", DEFAULT_PATH)


def write_channel_url(url: str, path: str | None = None, group: str = GROUP) -> bool:
    """Write ``url`` atomically if it differs from what is on disk. Returns True if written."""
    path = target_path() if path is None else path
    if not path or not url:
        return False
    try:
        with open(path) as f:
            if f.read().strip() == url:
                return False
    except OSError:
        pass

    directory = os.path.dirname(path) or "."
    try:
        gid = grp.getgrnam(group).gr_gid
    except KeyError:
        gid = -1
        logger.warning("Group %s does not exist; channel URL file will keep the default group", group)

    fd, tmp = tempfile.mkstemp(prefix=".channel-url.", dir=directory)
    try:
        os.fchmod(fd, MODE)
        if gid != -1:
            try:
                os.fchown(fd, -1, gid)
            except PermissionError:
                logger.warning("Cannot set group %s on the channel URL file (is this user in it?)", group)
        with os.fdopen(fd, "w") as f:
            fd = -1
            f.write(url + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        tmp = None
    finally:
        if fd != -1:
            os.close(fd)
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    logger.info("Channel URL written to %s (contents not logged: contains the channel key)", path)
    return True


def update_from_interface(interface, path: str | None = None) -> bool:
    """Read the URL from a meshtastic interface and write it. Never raises, never logs the URL."""
    try:
        node = getattr(interface, "localNode", None)
        url = node.getURL() if node is not None else ""
    except Exception as e:
        logger.warning("Could not read channel URL from the radio (%s)", e.__class__.__name__)
        return False
    if not url:
        return False
    try:
        return write_channel_url(url, path)
    except OSError as e:
        logger.warning("Could not write channel URL file (%s: %s)", e.__class__.__name__, e.strerror)
        return False
