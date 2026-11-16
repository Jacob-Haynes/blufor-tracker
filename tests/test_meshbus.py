import json

import pytest

from bridge import meshbus


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _tx(**kw):
    msg = {"text": "hello", "to": None, "channel": 0, "source": "test"}
    msg.update(kw)
    return json.dumps(msg)


def test_parse_tx_valid_broadcast():
    req = meshbus.parse_tx(_tx())
    assert (req.text, req.to, req.channel, req.source) == ("hello", None, 0, "test")


def test_parse_tx_dm_lowercases_node_id():
    assert meshbus.parse_tx(_tx(to="!ABCDEF01")).to == "!abcdef01"


def test_parse_tx_truncates_long_text():
    req = meshbus.parse_tx(_tx(text="x" * 500))
    assert len(req.text) == meshbus.MAX_TEXT_LEN
    assert req.text.endswith("...")


@pytest.mark.parametrize("payload", [
    b"not json",
    json.dumps([1, 2]),
    _tx(text=""),
    _tx(text=None),
    _tx(to="alpha"),
    _tx(channel=9),
    _tx(channel="0"),
    _tx(channel=True),
])
def test_parse_tx_rejects_invalid(payload):
    with pytest.raises(ValueError):
        meshbus.parse_tx(payload)


def test_parse_tx_defaults_source_and_channel():
    req = meshbus.parse_tx(json.dumps({"text": "hi"}))
    assert req.source == "unknown" and req.channel == 0 and req.to is None


def test_build_rx_text_with_node_names():
    packet = {"from": 0xA1A1A1A1, "fromId": "!a1a1a1a1", "channel": 1, "rxTime": 1700000000,
              "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": " ADVISOR: sitrep "}}
    nodes = {"!a1a1a1a1": {"user": {"shortName": "A1", "longName": "Alpha 1"}}}
    msg = meshbus.build_rx(packet, nodes=nodes)
    assert msg == {"from": "!a1a1a1a1", "text": "ADVISOR: sitrep", "channel": 1, "ts": 1700000000,
                   "from_short": "A1", "from_long": "Alpha 1"}


def test_build_rx_derives_from_id_and_skips_non_text():
    packet = {"from": 0x0000BEEF, "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "hi"}}
    assert meshbus.build_rx(packet)["from"] == "!0000beef"
    assert meshbus.build_rx({"from": 1, "decoded": {"portnum": "POSITION_APP"}}) is None


def test_build_rx_skips_own_node():
    packet = {"from": 42, "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "hi"}}
    assert meshbus.build_rx(packet, my_node_num=42) is None


def test_gateway_per_source_token_bucket():
    clock = FakeClock()
    sent = []
    gw = meshbus.MeshbusGateway(lambda *a: sent.append(a), clock=clock, global_interval=0, max_queue=50)
    for _ in range(meshbus.PER_SOURCE_BURST):
        assert gw.submit(_tx(source="a"))  # burst allowed
    assert not gw.submit(_tx(source="a"))  # bucket empty
    assert gw.submit(_tx(source="b"))  # other source unaffected
    clock.t += meshbus.PER_SOURCE_INTERVAL + 0.1  # one token back
    assert gw.submit(_tx(source="a"))
    assert not gw.submit(_tx(source="a"))
    while gw.send_next(timeout=0):
        pass
    assert len(sent) == meshbus.PER_SOURCE_BURST + 2
    assert gw.stats["rate_limited"] == 2


def test_gateway_drops_oldest_when_full():
    clock = FakeClock()
    sent = []
    gw = meshbus.MeshbusGateway(lambda t, to, ch: sent.append(t), clock=clock, max_queue=2,
                                per_source_interval=0, global_interval=0)
    for i in range(3):
        gw.submit(_tx(text=f"m{i}", source=f"s{i}"))
    while gw.send_next(timeout=0):
        pass
    assert sent == ["m1", "m2"]
    assert gw.stats["dropped"] == 1


def test_gateway_send_failure_is_counted_not_raised():
    def boom(*a):
        raise OSError("serial gone")

    gw = meshbus.MeshbusGateway(boom, global_interval=0)
    gw.submit(_tx())
    assert gw.send_next(timeout=0)
    assert gw.stats["failed"] == 1
