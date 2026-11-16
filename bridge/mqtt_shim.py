"""Serial-to-MQTT shim: bridges Meshtastic serial to RabbitMQ for OTS native Meshtastic support.

The Heltec V3's WiFi cannot establish TCP connections to the Pi 5's hotspot,
so this shim reads packets from B10 via USB serial and publishes them to
RabbitMQ MQTT on localhost in the ServiceEnvelope format OTS expects.

Downlink chat (WiFi ATAK → mesh) is handled by subscribing to the RabbitMQ
firehose for original CoT XML with sender metadata, then converting GeoChat
to ATAK_PLUGIN TAKPackets for the mesh.
"""

import argparse
import glob
import json
import logging
import os
import random
import struct
import sys
import threading
import time

import paho.mqtt.client as mqtt
import pika
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from meshtastic.protobuf import atak_pb2, mesh_pb2, mqtt_pb2, portnums_pb2
from meshtastic.serial_interface import SerialInterface
from pubsub import pub

from bridge import meshbus
from bridge.cot_converter import parse_cot_event

logger = logging.getLogger("mqtt_shim")

MQTT_ROOT = "opentakserver"

# Serial device patterns probed by --port auto, most specific first
_SERIAL_GLOBS = ("/dev/serial/by-id/*", "/dev/ttyUSB*", "/dev/ttyACM*")
_RADIO_WAIT_LOG_INTERVAL = 60  # seconds between "still waiting for radio" logs


def find_serial_port() -> str | None:
    """Return the first Meshtastic-looking serial device, or None."""
    for pattern in _SERIAL_GLOBS:
        for dev in sorted(glob.glob(pattern)):
            return os.path.realpath(dev)
    return None

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
        self._atak_nodes: set[int] = set()  # nodes that send ATAK_PLUGIN (dedup TEXT_MESSAGE_APP)
        self._uplinked_texts: dict[str, float] = {}  # text → timestamp (firehose echo prevention)
        self._my_node_num: int | None = None
        # The radio is shared by the self-SA, firehose, MQTT downlink and meshbus
        # threads; serialise writes so packets are not interleaved on the wire.
        self._send_lock = threading.Lock()
        self._meshbus = meshbus.MeshbusGateway(self._meshbus_send)

    def start(self):
        self._running = True

        # Connect to Meshtastic serial (waits for the radio to be plugged in)
        self.port = self._wait_for_radio(self.port)
        logger.info("Connecting to Meshtastic on %s", self.port)
        self._interface = SerialInterface(devPath=self.port)
        pub.subscribe(self._on_connection_lost, "meshtastic.connection.lost")

        # Get gateway ID from radio
        node_num = self._interface.myInfo.my_node_num
        self._my_node_num = node_num
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

        # Connect to MQTT (uplink: serial → OTS)
        self._mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="mesh-shim", protocol=mqtt.MQTTv311)
        self._mqtt.username_pw_set(self.mqtt_user, self.mqtt_pass)
        self._mqtt.on_connect = self._on_mqtt_connect
        self._mqtt.on_message = self._on_mqtt_message
        # connect_async + loop_start retries in the background, so the bridge
        # still runs (and serves the radio) while OpenTAKServer/RabbitMQ starts.
        self._mqtt.reconnect_delay_set(min_delay=1, max_delay=30)
        self._mqtt.connect_async(self.mqtt_host, self.mqtt_port, keepalive=60)
        self._mqtt.loop_start()

        # meshbus transmit worker (other tools -> radio)
        self._meshbus.start()

        # Start firehose consumer (downlink: OTS → serial for ATAK chat)
        self._start_firehose_consumer()

        # Start periodic self-SA so ATAK clients recognise B10 as a mesh contact
        self._start_self_sa()

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

    def _wait_for_radio(self, port: str) -> str:
        """Block until the serial device exists. ``auto`` probes common device paths."""
        last_log = 0.0
        while self._running:
            found = find_serial_port() if port == "auto" else (port if os.path.exists(port) else None)
            if found:
                return found
            now = time.time()
            if now - last_log > _RADIO_WAIT_LOG_INTERVAL:
                logger.warning("No Meshtastic radio at %s — plug in the bridge radio (still waiting)",
                               "/dev/ttyUSB*|/dev/ttyACM*" if port == "auto" else port)
                last_log = now
            time.sleep(2)
        raise SystemExit(0)

    def _on_connection_lost(self, interface=None, **kwargs):
        # Exit non-zero so systemd restarts us and we re-wait for the radio.
        logger.error("Meshtastic serial connection lost — exiting for restart")
        os._exit(1)

    def _send_data(self, payload: bytes, port_num: int):
        with self._send_lock:
            self._interface.sendData(payload, portNum=port_num, wantAck=False)

    def _meshbus_send(self, text: str, to: str | None, channel: int):
        with self._send_lock:
            self._interface.sendText(
                text,
                destinationId=to or "^all",
                wantAck=bool(to),
                channelIndex=channel,
            )

    def _publish_meshbus_rx(self, packet: dict):
        nodes = getattr(self._interface, "nodes", None) or {}
        msg = meshbus.build_rx(packet, nodes=nodes, my_node_num=self._my_node_num)
        if msg is None:
            return
        if self._mqtt and self._mqtt.is_connected():
            self._mqtt.publish(meshbus.TOPIC_RX, json.dumps(msg))
            logger.info("meshbus RX from=%s ch=%d: %s", msg["from"], msg["channel"], msg["text"][:80])
        else:
            logger.warning("MQTT not connected, meshbus/rx dropped")

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

    def _on_mqtt_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            topic = f"{MQTT_ROOT}/2/e/{self._channel_id}/+"
            client.subscribe(topic)
            client.subscribe(meshbus.TOPIC_TX)
            logger.info("MQTT connected, subscribed to %s and %s", topic, meshbus.TOPIC_TX)
        else:
            logger.error("MQTT connection failed: %s", reason_code)

    # ── Serial → MQTT (uplink) ──────────────────────────────────────

    _SKIP_PORTNUMS = {"ATAK_FORWARDER"}

    def _on_mesh_receive(self, packet, interface=None):
        """Called when a packet is received from the mesh via serial."""
        try:
            portnum = packet.get("decoded", {}).get("portnum", "")
            from_id = packet.get("from", 0)

            if portnum in self._SKIP_PORTNUMS:
                logger.debug("Skipping %s (OTS bug workaround)", portnum)
                return

            # Every inbound text goes to meshbus/rx, including from ATAK nodes
            # (before the ATAK dedup below, which only concerns the OTS uplink).
            if portnum == "TEXT_MESSAGE_APP":
                try:
                    self._publish_meshbus_rx(packet)
                except Exception:
                    logger.exception("meshbus/rx publish failed")

            # Track all uplinked TEXT_MESSAGE_APP for firehose echo prevention
            if portnum == "TEXT_MESSAGE_APP":
                text = packet.get("decoded", {}).get("text", "")
                if text:
                    self._uplinked_texts[text.strip()] = time.time()

            # Dedup: ATAK sends both ATAK_PLUGIN and TEXT_MESSAGE_APP for chats.
            # Any node that sends ATAK_PLUGIN is an ATAK device — suppress its
            # TEXT_MESSAGE_APP since ATAK_PLUGIN carries the proper CoT payload.
            if portnum == "ATAK_PLUGIN":
                self._atak_nodes.add(from_id)
            elif portnum == "TEXT_MESSAGE_APP" and from_id in self._atak_nodes:
                logger.info("Dedup: suppressing TEXT_MESSAGE_APP from %s (known ATAK node)",
                            packet.get("fromId", "?"))
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
        """Strip |UUID suffix from device_callsign in uncompressed TAKPackets.

        OTS uses device_callsign as a cache key, but ATAK appends |UUID per-message.
        Compressed packets are passed through — the OTS-side patch handles those
        (deploy/ots_patches/meshtastic_device_callsign.patch).
        """
        try:
            tak = atak_pb2.TAKPacket()
            tak.ParseFromString(payload)
        except Exception:
            return payload

        if tak.is_compressed:
            return payload

        if tak.HasField("contact") and "|" in tak.contact.device_callsign:
            original = tak.contact.device_callsign
            tak.contact.device_callsign = original.split("|")[0]
            logger.info("Fixed device_callsign: %s → %s", original, tak.contact.device_callsign)
            return tak.SerializeToString()

        return payload

    # ── Self-SA: register B10 as ATAK device on the mesh ──────────

    _SELF_SA_INTERVAL = 30  # seconds

    def _start_self_sa(self):
        """Periodically send a PLI TAKPacket so ATAK clients recognise B10."""
        def _sa_loop():
            while self._running:
                try:
                    self._send_self_sa()
                except Exception:
                    logger.exception("Self-SA send failed")
                time.sleep(self._SELF_SA_INTERVAL)

        thread = threading.Thread(target=_sa_loop, daemon=True, name="self-sa")
        thread.start()

    def _send_self_sa(self):
        tak = atak_pb2.TAKPacket()
        tak.contact.callsign = "BRIDGE"
        tak.contact.device_callsign = f"ANDROID-{self._gateway_id}"
        tak.pli.latitude_i = 0
        tak.pli.longitude_i = 0
        tak.pli.altitude = 0
        tak.pli.speed = 0
        tak.pli.course = 0
        tak.group.team = atak_pb2.Team.Value("Cyan")
        tak.group.role = atak_pb2.MemberRole.Value("HQ")

        raw = tak.SerializeToString()
        self._send_data(raw, portnums_pb2.PortNum.Value("ATAK_PLUGIN"))
        logger.debug("Self-SA sent (%d bytes)", len(raw))

    # ── MQTT → Serial (downlink: position/non-chat) ────────────────

    def _on_mqtt_message(self, client, userdata, msg):
        """Called when OTS publishes a message for the mesh, or a tool publishes to meshbus/tx."""
        if msg.topic == meshbus.TOPIC_TX:
            self._meshbus.submit(msg.payload)
            return
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
            self._send_data(mesh_packet.decoded.payload, mesh_packet.decoded.portnum)

        except Exception:
            logger.exception("Error in MQTT→Serial relay")

    # ── Firehose → Serial (downlink: ATAK chat with sender metadata) ─

    def _start_firehose_consumer(self):
        """Subscribe to RabbitMQ firehose for original CoT XML."""
        thread = threading.Thread(target=self._firehose_loop, daemon=True, name="firehose")
        thread.start()

    def _firehose_loop(self):
        while self._running:
            try:
                conn = pika.BlockingConnection(
                    pika.ConnectionParameters(
                        self.mqtt_host,
                        credentials=pika.PlainCredentials(self.mqtt_user, self.mqtt_pass),
                    )
                )
                ch = conn.channel()
                result = ch.queue_declare(queue="", exclusive=True)
                queue_name = result.method.queue
                ch.queue_bind(queue=queue_name, exchange="firehose")
                logger.info("Firehose consumer started on queue %s", queue_name)

                for method, properties, body in ch.consume(queue_name, auto_ack=True):
                    if not self._running:
                        break
                    self._on_firehose_cot(body)

                conn.close()
            except pika.exceptions.AMQPConnectionError as e:
                if self._running:
                    logger.warning("Firehose: RabbitMQ not reachable (%s), retrying in 5s", e.__class__.__name__)
                    time.sleep(5)
            except Exception:
                if self._running:
                    logger.exception("Firehose consumer error, reconnecting in 5s")
                    time.sleep(5)

    def _on_firehose_cot(self, body: bytes):
        """Process a CoT event from the firehose — relay GeoChat to mesh as ATAK_PLUGIN."""
        try:
            raw = body.decode("utf-8", errors="replace")

            # Firehose delivers JSON: {"uid": "...", "cot": "<event...>"}
            try:
                msg = json.loads(raw)
                xml_str = msg.get("cot", "")
                sender_uid = msg.get("uid", "")
            except (json.JSONDecodeError, AttributeError):
                xml_str = raw
                sender_uid = ""

            # Only process GeoChat events
            if 'type="b-t-f"' not in xml_str:
                return

            parsed = parse_cot_event(xml_str)
            if not parsed:
                logger.warning("Firehose: could not parse GeoChat CoT")
                return

            remarks = parsed.get("remarks", "")
            if not remarks:
                return

            # Echo prevention: skip messages we recently uplinked from mesh
            now = time.time()
            text_key = remarks.strip()
            if text_key in self._uplinked_texts and now - self._uplinked_texts[text_key] < 30:
                logger.info("Firehose: skipping echo of '%s'", text_key[:50])
                return

            # Clean old entries
            self._uplinked_texts = {k: v for k, v in self._uplinked_texts.items() if now - v < 60}

            sender = parsed.get("sender_callsign", parsed.get("callsign", "TAK"))

            # Build TAKPacket GeoChat for ATAK on mesh (uncompressed only —
            # compressed requires unishox2 which crashes from thread contention)
            tak = atak_pb2.TAKPacket()
            tak.contact.callsign = sender
            tak.contact.device_callsign = sender_uid or f"TAK-{sender}"
            tak.chat.message = remarks
            tak.chat.to = "All Chat Rooms"

            raw = tak.SerializeToString()
            self._send_data(raw, portnums_pb2.PortNum.Value("ATAK_PLUGIN"))
            logger.info("Firehose→Serial: GeoChat from %s (%d bytes): %s",
                        sender, len(raw), remarks[:80])

        except Exception:
            logger.exception("Error processing firehose CoT")


class SimulatedMeshbus:
    """``--simulate``: no radio, no OTS. Serves meshbus only, so tools that use the
    mesh (tac-advisor, mesh-deadrop) can be exercised on a laptop with any MQTT broker.

    Publishes fake inbound texts on meshbus/rx and logs (rather than transmits)
    everything arriving on meshbus/tx, through the same validation and rate limits.
    """

    NODES = [
        ("!a1a1a1a1", "A1", "Alpha 1"),
        ("!b2b2b2b2", "B2", "Bravo 2"),
        ("!c3c3c3c3", "C3", "Charlie 3"),
    ]
    TEXTS = [
        "ADVISOR: sitrep",
        "ADVISOR: any drone activity?",
        "DROP B2 Meet at checkpoint bravo 0600",
        "PICKUP",
        "STATUS",
        "Comms check",
    ]

    def __init__(self, mqtt_host, mqtt_port, mqtt_user, mqtt_pass, interval: float = 20.0):
        self._interval = interval
        self._gateway = meshbus.MeshbusGateway(self._fake_send)
        self._mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="mesh-shim-sim",
                                 protocol=mqtt.MQTTv311)
        self._mqtt.username_pw_set(mqtt_user, mqtt_pass)
        self._mqtt.on_connect = lambda c, u, f, rc, p: (c.subscribe(meshbus.TOPIC_TX),
                                                        logger.info("SIM: MQTT connected (%s)", rc))
        self._mqtt.on_message = lambda c, u, m: self._gateway.submit(m.payload)
        self._mqtt.reconnect_delay_set(min_delay=1, max_delay=30)
        self._mqtt.connect_async(mqtt_host, mqtt_port, keepalive=60)

    @staticmethod
    def _fake_send(text, to, channel):
        logger.info("SIM radio TX to=%s ch=%d: %s", to or "^all", channel, text)

    def run(self):
        self._mqtt.loop_start()
        self._gateway.start()
        logger.info("Simulation mode — meshbus only, fake mesh texts every ~%.0fs", self._interval)
        try:
            while True:
                time.sleep(random.uniform(self._interval * 0.5, self._interval * 1.5))
                node_id, short, long_name = random.choice(self.NODES)
                packet = {
                    "fromId": node_id,
                    "from": int(node_id[1:], 16),
                    "channel": 0,
                    "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": random.choice(self.TEXTS)},
                }
                nodes = {node_id: {"user": {"shortName": short, "longName": long_name}}}
                msg = meshbus.build_rx(packet, nodes=nodes)
                if self._mqtt.is_connected():
                    self._mqtt.publish(meshbus.TOPIC_RX, json.dumps(msg))
                    logger.info("SIM meshbus RX from=%s: %s", node_id, msg["text"])
        except KeyboardInterrupt:
            pass


def main():
    logging.basicConfig(
        force=True,
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    env = os.environ.get
    parser = argparse.ArgumentParser(description="Meshtastic Serial ↔ MQTT shim for OTS (+ meshbus)")
    parser.add_argument("--port", default=env("MESH_PORT", "auto"),
                        help="Meshtastic serial port, or 'auto' to probe /dev/ttyUSB*, /dev/ttyACM* (default)")
    parser.add_argument("--mqtt-host", default=env("MQTT_HOST", "127.0.0.1"), help="MQTT broker host")
    parser.add_argument("--mqtt-port", type=int, default=int(env("MQTT_PORT", "1883")), help="MQTT broker port")
    parser.add_argument("--mqtt-user", default=env("MQTT_USER", "guest"), help="MQTT username")
    parser.add_argument("--mqtt-pass", default=env("MQTT_PASS", "guest"), help="MQTT password")
    parser.add_argument("--simulate", action="store_true",
                        help="No radio/OTS: serve meshbus with fake mesh texts (for testing other tools)")
    args = parser.parse_args()

    if args.simulate:
        SimulatedMeshbus(args.mqtt_host, args.mqtt_port, args.mqtt_user, args.mqtt_pass).run()
        return

    shim = MqttShim(
        port=args.port,
        mqtt_host=args.mqtt_host,
        mqtt_port=args.mqtt_port,
        mqtt_user=args.mqtt_user,
        mqtt_pass=args.mqtt_pass,
    )
    try:
        shim.start()
    except Exception:
        logger.exception("Bridge failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
