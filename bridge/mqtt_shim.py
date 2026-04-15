"""Serial-to-MQTT shim: bridges Meshtastic serial to RabbitMQ for OTS native Meshtastic support.

The Heltec V3's WiFi cannot establish TCP connections to the Pi 5's hotspot,
so this shim reads packets from B10 via USB serial and publishes them to
RabbitMQ MQTT on localhost in the ServiceEnvelope format OTS expects.
"""

import argparse
import logging
import struct
import threading
import time

import paho.mqtt.client as mqtt
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from meshtastic.protobuf import atak_pb2, mesh_pb2, mqtt_pb2, portnums_pb2
from meshtastic.serial_interface import SerialInterface
from pubsub import pub

logger = logging.getLogger("mqtt_shim")

MQTT_ROOT = "opentakserver"

# Meshtastic default AES key (used when PSK is a single byte)
_DEFAULT_KEY = bytearray([
    0xd4, 0xf1, 0xbb, 0x3a, 0x20, 0x29, 0x07, 0x59,
    0xf0, 0xbc, 0xff, 0xab, 0xcf, 0x4e, 0x69, 0x01,
])


class MqttShim:
    def __init__(self, port: str, mqtt_host: str = "127.0.0.1", mqtt_port: int = 1883,
                 mqtt_user: str = "guest", mqtt_pass: str = "guest"):
        self.port = port
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.mqtt_user = mqtt_user
        self.mqtt_pass = mqtt_pass

        self._interface: SerialInterface | None = None
        self._mqtt: mqtt.Client | None = None
        self._gateway_id = ""
        self._channel_id = ""
        self._channel_key: bytes | None = None
        self._running = False
        self._mesh_atak_uids: set[str] = set()  # ATAK UIDs seen from mesh (echo prevention)

    def start(self):
        self._running = True

        # Connect to Meshtastic serial
        logger.info("Connecting to Meshtastic on %s", self.port)
        self._interface = SerialInterface(devPath=self.port)

        # Get gateway ID from radio
        node_num = self._interface.myInfo.my_node_num
        self._gateway_id = f"!{node_num:08x}"
        logger.info("Gateway ID: %s", self._gateway_id)

        # Get primary channel name
        local_node = self._interface.localNode
        if local_node and local_node.channels:
            ch = local_node.channels[0]
            self._channel_id = ch.settings.name if ch.settings.name else "LongFast"
        else:
            self._channel_id = "LongFast"
        logger.info("Channel ID: %s", self._channel_id)

        # Get channel encryption key for re-encrypting packets
        self._channel_key = self._get_channel_key()
        if self._channel_key:
            logger.info("Channel key loaded (%d-bit AES)", len(self._channel_key) * 8)
        else:
            logger.warning("No channel key — packets will be sent unencrypted")

        # Subscribe to mesh receive events
        pub.subscribe(self._on_mesh_receive, "meshtastic.receive")

        # Connect to MQTT
        self._mqtt = mqtt.Client(client_id="mesh-shim", protocol=mqtt.MQTTv311)
        self._mqtt.username_pw_set(self.mqtt_user, self.mqtt_pass)
        self._mqtt.on_connect = self._on_mqtt_connect
        self._mqtt.on_message = self._on_mqtt_message
        self._mqtt.connect(self.mqtt_host, self.mqtt_port, keepalive=60)
        self._mqtt.loop_start()

        logger.info("Shim started — serial ↔ MQTT bridge active")
        try:
            while self._running:
                time.sleep(5)
        except KeyboardInterrupt:
            logger.info("Shutting down")
        finally:
            self._running = False
            if self._mqtt:
                self._mqtt.loop_stop()
                self._mqtt.disconnect()
            if self._interface:
                self._interface.close()

    def _get_channel_key(self) -> bytes | None:
        """Derive the AES key from the primary channel's PSK."""
        local_node = self._interface.localNode
        if not local_node or not local_node.channels:
            return None

        psk = bytes(local_node.channels[0].settings.psk)
        if not psk:
            return None

        if len(psk) == 1:
            if psk[0] == 0:
                return None  # No encryption
            # XOR first byte of default key with the PSK value
            key = bytearray(_DEFAULT_KEY)
            key[0] ^= psk[0]
            return bytes(key)

        if len(psk) in (16, 32):
            return psk

        return psk[:16].ljust(16, b"\x00")

    def _reencrypt(self, mp: mesh_pb2.MeshPacket) -> mesh_pb2.MeshPacket:
        """Re-encrypt a decoded MeshPacket so OTS can decrypt it natively."""
        if self._channel_key is None or not mp.HasField("decoded"):
            return mp

        plaintext = mp.decoded.SerializeToString()

        # Nonce: packet_id (8 LE) + from_node (4 LE) + 4 zero bytes
        nonce = struct.pack("<Q", mp.id) + struct.pack("<I", getattr(mp, "from")) + b"\x00" * 4

        encryptor = Cipher(algorithms.AES(self._channel_key), modes.CTR(nonce)).encryptor()
        ciphertext = encryptor.update(plaintext) + encryptor.finalize()

        mp.encrypted = ciphertext  # Setting encrypted clears decoded (oneof)
        return mp

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        if rc == 0:
            topic = f"{MQTT_ROOT}/2/e/{self._channel_id}/+"
            client.subscribe(topic)
            logger.info("MQTT connected, subscribed to %s", topic)
        else:
            logger.error("MQTT connection failed: rc=%d", rc)

    # ── Serial → MQTT (uplink) ──────────────────────────────────────

    # Portnums that crash OTS's RabbitMQ connection (KeyError on ATAK UID
    # with pipe suffix).  Filter until the OTS bug is fixed upstream.
    _SKIP_PORTNUMS = {"ATAK_FORWARDER"}

    def _on_mesh_receive(self, packet, interface=None):
        """Called when a packet is received from the mesh via serial."""
        try:
            portnum = packet.get("decoded", {}).get("portnum", "")
            if portnum in self._SKIP_PORTNUMS:
                logger.debug("Skipping %s (OTS bug workaround)", portnum)
                return

            # Build a MeshPacket protobuf from the raw packet
            mesh_packet = self._packet_to_meshpacket(packet)
            if mesh_packet is None:
                return

            # Fix ATAK_PLUGIN device_callsign to prevent OTS KeyError
            if portnum == "ATAK_PLUGIN" and mesh_packet.HasField("decoded"):
                mesh_packet.decoded.payload = self._fix_atak_payload(
                    mesh_packet.decoded.payload
                )

            # Re-encrypt so OTS can decrypt natively
            mesh_packet = self._reencrypt(mesh_packet)

            # Wrap in ServiceEnvelope
            envelope = mqtt_pb2.ServiceEnvelope()
            envelope.packet.CopyFrom(mesh_packet)
            envelope.channel_id = self._channel_id
            envelope.gateway_id = self._gateway_id

            topic = f"{MQTT_ROOT}/2/e/{self._channel_id}/{self._gateway_id}"
            payload = envelope.SerializeToString()

            if self._mqtt and self._mqtt.is_connected():
                self._mqtt.publish(topic, payload)
                logger.info("Serial→MQTT: from=%s portnum=%s topic=%s",
                            packet.get("fromId", "?"),
                            packet.get("decoded", {}).get("portnum", "?"),
                            topic)
            else:
                logger.warning("MQTT not connected, dropping packet")

        except Exception:
            logger.exception("Error in serial→MQTT relay")

    def _packet_to_meshpacket(self, packet: dict) -> mesh_pb2.MeshPacket | None:
        """Convert meshtastic-python's decoded packet dict back to a MeshPacket protobuf."""
        decoded = packet.get("decoded", {})
        if not decoded:
            # Raw/encrypted packet — try to use the raw bytes
            raw = packet.get("raw")
            if raw and isinstance(raw, mesh_pb2.MeshPacket):
                return raw
            return None

        mp = mesh_pb2.MeshPacket()
        mp.id = packet.get("id", 0)

        from_id = packet.get("from", 0)
        if isinstance(from_id, str):
            from_id = int(from_id.replace("!", ""), 16) if from_id.startswith("!") else 0
        mp.rx_time = int(packet.get("rxTime", time.time()))
        mp.hop_start = packet.get("hopStart", 0)
        mp.hop_limit = packet.get("hopLimit", 0)

        # 'from' is a Python reserved word — use setattr
        if isinstance(from_id, int):
            setattr(mp, "from", from_id)
        to_id = packet.get("to", 0xFFFFFFFF)
        if isinstance(to_id, str):
            to_id = int(to_id.replace("!", ""), 16) if to_id.startswith("!") else 0xFFFFFFFF
        mp.to = to_id

        # Set the decoded data
        mp.decoded.portnum = portnums_pb2.PortNum.Value(decoded.get("portnum", "UNKNOWN_APP"))

        # Payload: use raw bytes if available, otherwise re-encode
        if "payload" in decoded:
            payload = decoded["payload"]
            if isinstance(payload, bytes):
                mp.decoded.payload = payload
            elif isinstance(payload, str):
                mp.decoded.payload = payload.encode("utf-8")
        elif "text" in decoded:
            mp.decoded.payload = decoded["text"].encode("utf-8")

        if decoded.get("requestId"):
            mp.decoded.request_id = decoded["requestId"]
        if decoded.get("wantResponse"):
            mp.decoded.want_response = True

        return mp

    # ── ATAK_PLUGIN payload fix ─────────────────────────────────────

    @staticmethod
    def _fix_atak_payload(payload: bytes) -> bytes:
        """Strip |UUID suffix from TAKPacket device_callsign to prevent OTS KeyError.

        OTS extracts contact.device_callsign from TAKPackets and uses it as a
        cache key. ATAK appends |UUID to the device_callsign which doesn't
        match the meshtastic_devices cache, causing a KeyError that tears down
        the entire RabbitMQ connection.
        """
        try:
            tak = atak_pb2.TAKPacket()
            tak.ParseFromString(payload)
        except Exception:
            return payload

        if tak.is_compressed:
            return MqttShim._fix_compressed_atak(payload)

        if tak.HasField("contact") and "|" in tak.contact.device_callsign:
            original = tak.contact.device_callsign
            tak.contact.device_callsign = original.split("|")[0]
            logger.info("Fixed device_callsign: %s → %s", original, tak.contact.device_callsign)
            return tak.SerializeToString()

        return payload

    @staticmethod
    def _fix_compressed_atak(payload: bytes) -> bytes:
        """Fix device_callsign in a compressed TAKPacket via wire-format parsing."""
        from bridge.cot_converter import (
            _decompress_str,
            _encode_field,
            _encode_varint,
            _parse_raw_fields,
        )

        try:
            top = _parse_raw_fields(payload)
            if 2 not in top:
                return payload

            contact_fields = _parse_raw_fields(top[2][0])
            if 2 not in contact_fields:
                return payload

            device_cs = _decompress_str(contact_fields[2][0])
            if "|" not in device_cs:
                return payload

            fixed = device_cs.split("|")[0]
            logger.info("Fixed compressed device_callsign: %s → %s", device_cs, fixed)

            # Re-encode device_callsign
            try:
                import unishox2

                new_dc, _ = unishox2.compress(fixed)
            except ImportError:
                new_dc = fixed.encode("utf-8")

            # Rebuild Contact submessage
            contact_parts = []
            if 1 in contact_fields:
                contact_parts.append(_encode_field(1, 2, contact_fields[1][0]))
            contact_parts.append(_encode_field(2, 2, new_dc))
            new_contact = b"".join(contact_parts)

            # Rebuild TAKPacket preserving all other fields
            result = []
            contact_done = False
            for fn in sorted(top.keys()):
                for val in top[fn]:
                    if fn == 1:  # is_compressed: varint
                        result.append(_encode_field(fn, 0, _encode_varint(val)))
                    elif fn == 2:  # Contact: replaced
                        if not contact_done:
                            result.append(_encode_field(fn, 2, new_contact))
                            contact_done = True
                    else:  # All other fields: length-delimited submessages
                        result.append(_encode_field(fn, 2, val))

            return b"".join(result)

        except Exception:
            logger.debug("Could not fix compressed ATAK payload", exc_info=True)
            return payload

    # ── MQTT → Serial (downlink) ────────────────────────────────────

    def _on_mqtt_message(self, client, userdata, msg):
        """Called when OTS publishes a message for the mesh."""
        try:
            envelope = mqtt_pb2.ServiceEnvelope()
            envelope.ParseFromString(msg.payload)

            # Don't echo our own packets
            if envelope.gateway_id == self._gateway_id:
                return

            mesh_packet = envelope.packet
            if not mesh_packet.decoded.payload:
                return

            pkt_from = getattr(mesh_packet, "from", 0)
            logger.info("MQTT→Serial: from=%s portnum=%s gateway=%s",
                        f"!{pkt_from:08x}" if pkt_from else "?",
                        portnums_pb2.PortNum.Name(mesh_packet.decoded.portnum),
                        envelope.gateway_id)

            # Send to mesh via serial
            self._interface.sendData(
                mesh_packet.decoded.payload,
                portNum=mesh_packet.decoded.portnum,
                wantAck=False,
            )

        except Exception:
            logger.exception("Error in MQTT→Serial relay")


def main():
    logging.basicConfig(
        force=True,
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Meshtastic Serial ↔ MQTT shim for OTS")
    parser.add_argument("--port", default="/dev/ttyUSB0", help="Meshtastic serial port")
    parser.add_argument("--mqtt-host", default="127.0.0.1", help="MQTT broker host")
    parser.add_argument("--mqtt-port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--mqtt-user", default="guest", help="MQTT username")
    parser.add_argument("--mqtt-pass", default="guest", help="MQTT password")
    args = parser.parse_args()

    shim = MqttShim(
        port=args.port,
        mqtt_host=args.mqtt_host,
        mqtt_port=args.mqtt_port,
        mqtt_user=args.mqtt_user,
        mqtt_pass=args.mqtt_pass,
    )
    shim.start()


if __name__ == "__main__":
    main()
