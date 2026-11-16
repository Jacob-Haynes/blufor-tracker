import grp
import logging
import os
import stat

from bridge import channel_url

SECRET_URL = "https://meshtastic.org/e/#CgcSAQE6AggNEg8IATgDQANIAVAeaAHAB-SECRETPSK"


class FakeNode:
    def __init__(self, url):
        self.url = url

    def getURL(self):
        return self.url


class FakeIface:
    def __init__(self, url):
        self.localNode = FakeNode(url)


def test_writes_atomically_with_mode_0640_and_never_logs(tmp_path, caplog):
    path = tmp_path / "channel-url"
    with caplog.at_level(logging.DEBUG):
        assert channel_url.update_from_interface(FakeIface(SECRET_URL), str(path))
    assert path.read_text() == SECRET_URL + "\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert [p.name for p in tmp_path.iterdir()] == ["channel-url"]  # no temp file left behind
    assert "SECRETPSK" not in caplog.text and "meshtastic.org/e" not in caplog.text


def test_rewrites_only_on_change(tmp_path):
    path = str(tmp_path / "channel-url")
    assert channel_url.write_channel_url(SECRET_URL, path)
    inode = os.stat(path).st_ino
    assert not channel_url.write_channel_url(SECRET_URL, path)  # unchanged
    assert os.stat(path).st_ino == inode
    assert channel_url.write_channel_url(SECRET_URL + "x", path)  # channel changed
    assert open(path).read().strip() == SECRET_URL + "x"


def test_sets_group_when_it_exists(tmp_path):
    mine = grp.getgrgid(os.getgid()).gr_name
    path = tmp_path / "channel-url"
    channel_url.write_channel_url(SECRET_URL, str(path), group=mine)
    assert path.stat().st_gid == os.getgid()


def test_missing_group_or_radio_errors_are_harmless(tmp_path, caplog):
    assert channel_url.write_channel_url(SECRET_URL, str(tmp_path / "f"), group="no-such-group-xyz")

    class Broken:
        @property
        def localNode(self):
            raise RuntimeError(SECRET_URL)

    assert channel_url.update_from_interface(Broken(), str(tmp_path / "g")) is False
    assert channel_url.update_from_interface(FakeIface(""), str(tmp_path / "h")) is False
    assert channel_url.update_from_interface(FakeIface(SECRET_URL), str(tmp_path / "missing" / "x")) is False
    assert "SECRETPSK" not in caplog.text


def test_disabled_by_empty_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CHANNEL_URL_FILE", "")
    assert channel_url.update_from_interface(FakeIface(SECRET_URL)) is False
