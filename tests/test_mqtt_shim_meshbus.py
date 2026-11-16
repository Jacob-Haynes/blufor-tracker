"""MqttShim meshbus wiring, with fake radio and MQTT client (no hardware)."""

import json
from types import SimpleNamespace

from bridge import meshbus
from bridge.mqtt_shim import MqttShim


class FakeInterface:
    def __init__(self):
        self.texts = []
        self.data = []
        self.nodes = {"!a1a1a1a1": {"user": {"shortName": "A1", "longName": "Alpha 1"}}}

    def sendText(self, text, destinationId, wantAck, channelIndex):
        self.texts.append((text, destinationId, wantAck, channelIndex))

    def sendData(self, payload, portNum, wantAck):
        self.data.append((payload, portNum))


class FakeMqtt:
    def __init__(self):
        self.published = []

    def is_connected(self):
        return True

    def publish(self, topic, payload):
        self.published.append((topic, payload))


def _shim():
    shim = MqttShim(port="auto")
    shim._interface = FakeInterface()
    shim._mqtt = FakeMqtt()
    shim._my_node_num = 0x12345678
    shim._gateway_id = "!12345678"
    shim._channel_id = "LongFast"
    return shim


def test_meshbus_tx_reaches_radio():
    shim = _shim()
    msg = SimpleNamespace(topic=meshbus.TOPIC_TX, payload=json.dumps(
        {"text": "DRONE ALERT", "to": "!a1a1a1a1", "channel": 0, "source": "drone-detector"}).encode())
    shim._on_mqtt_message(None, None, msg)
    assert shim._meshbus.send_next(timeout=0)
    assert shim._interface.texts == [("DRONE ALERT", "!a1a1a1a1", True, 0)]


def test_meshbus_tx_broadcast_uses_all():
    shim = _shim()
    msg = SimpleNamespace(topic=meshbus.TOPIC_TX, payload=b'{"text": "hi", "source": "x"}')
    shim._on_mqtt_message(None, None, msg)
    shim._meshbus.send_next(timeout=0)
    assert shim._interface.texts[0][1] == "^all"


def test_inbound_text_published_to_meshbus_rx_even_from_atak_node():
    shim = _shim()
    shim._atak_nodes.add(0xA1A1A1A1)  # ATAK node: OTS uplink is deduped, meshbus must still see it
    packet = {"from": 0xA1A1A1A1, "fromId": "!a1a1a1a1", "to": 0xFFFFFFFF, "id": 7,
              "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "ADVISOR: sitrep", "payload": b"ADVISOR: sitrep"}}
    shim._on_mesh_receive(packet)
    rx = [json.loads(p) for t, p in shim._mqtt.published if t == meshbus.TOPIC_RX]
    assert rx and rx[0]["from"] == "!a1a1a1a1" and rx[0]["from_short"] == "A1"
    assert rx[0]["text"] == "ADVISOR: sitrep"
