"""Bidirectional Meshtastic ↔ FreeTAKServer CoT bridge."""

import argparse
import logging
import random
import socket
import threading
import time

from pubsub import pub

from bridge.cot_converter import (
    UID_PREFIX,
    cot_xml_to_meshtastic,
    meshtastic_to_cot_xml,
)

logger = logging.getLogger("bridge")

# Track recent UIDs to prevent echo loops
_recent_uids: dict[str, float] = {}
_DEDUP_WINDOW = 30  # seconds

# Battery cache: callsign → battery %
_battery_cache: dict[str, float] = {}

# Rate limiter for outbound mesh sends
_last_mesh_send = 0.0
_MESH_SEND_INTERVAL = 1.0  # seconds


class MeshBridge:
    def __init__(self, port: str, fts_host: str, fts_port: int, simulate: bool):
        self.port = port
        self.fts_host = fts_host
        self.fts_port = fts_port
        self.simulate = simulate
        self._interface = None
        self._fts_sock: socket.socket | None = None
        self._running = False

    def start(self):
        self._running = True

        if self.simulate:
            logger.info("Starting in simulation mode (no hardware)")
            threading.Thread(target=self._simulator_loop, daemon=True).start()
        else:
            self._connect_meshtastic()

        threading.Thread(target=self._fts_reader_loop, daemon=True).start()

        logger.info("Bridge started — press Ctrl+C to stop")
        try:
            while self._running:
                time.sleep(1)
                self._cleanup_dedup()
        except KeyboardInterrupt:
            logger.info("Shutting down")
            self._running = False

    def _connect_meshtastic(self):
        from meshtastic.serial_interface import SerialInterface

        logger.info("Connecting to Meshtastic on %s", self.port)
        self._interface = SerialInterface(devPath=self.port)
        pub.subscribe(self._on_mesh_receive, "meshtastic.receive")
        logger.info("Meshtastic connected")

    def _node_id_to_callsign(self, node_id: str) -> str:
        if self._interface and self._interface.nodes:
            node = self._interface.nodes.get(node_id)
            if node:
                user = node.get("user", {})
                return user.get("shortName") or user.get("longName") or node_id
        return node_id

    # ── Meshtastic → FTS ──────────────────────────────────────────────

    def _on_mesh_receive(self, packet, interface=None):
        try:
            decoded = packet.get("decoded", {})
            portnum = decoded.get("portnum")
            from_id = str(packet.get("fromId", packet.get("from", "unknown")))
            callsign = self._node_id_to_callsign(from_id)

            # Enrich packet with resolved callsign for converter
            packet["_callsign"] = callsign

            # Cache battery from telemetry
            if portnum == "TELEMETRY_APP":
                telemetry = decoded.get("telemetry", {})
                battery = telemetry.get("deviceMetrics", {}).get("batteryLevel")
                if battery is not None:
                    _battery_cache[callsign] = float(battery)
                    logger.debug("Battery %s: %.0f%%", callsign, battery)
                return

            # ATAK_PLUGIN (portnum 72): forward raw protobuf to FTS as-is
            if portnum == "ATAK_PLUGIN" or decoded.get("portnum_raw") == 72:
                raw = decoded.get("payload", b"")
                if raw:
                    logger.info("ATAK plugin passthrough from %s (%d bytes)", callsign, len(raw))
                    self._send_to_fts_raw(raw)
                return

            # Convert to CoT XML
            cot_xml = meshtastic_to_cot_xml(packet, _battery_cache)
            if cot_xml is None:
                return

            # Dedup check
            uid = self._extract_uid(cot_xml)
            if uid and self._is_duplicate(uid):
                return

            logger.info("Mesh→FTS: %s from %s [%s]", portnum, callsign, uid or "?")
            self._send_to_fts(cot_xml)

        except Exception:
            logger.exception("Error processing mesh packet")

    def _send_to_fts(self, cot_xml: str):
        try:
            if self._fts_sock is None:
                self._connect_fts()
            if self._fts_sock is None:
                logger.warning("No FTS connection, dropping CoT")
                return
            self._fts_sock.sendall(cot_xml.encode("utf-8"))
        except (OSError, BrokenPipeError):
            logger.warning("FTS connection lost, reconnecting")
            self._fts_sock = None
            self._connect_fts()

    def _send_to_fts_raw(self, data: bytes):
        try:
            if self._fts_sock is None:
                self._connect_fts()
            if self._fts_sock is None:
                return
            self._fts_sock.sendall(data)
        except (OSError, BrokenPipeError):
            logger.warning("FTS connection lost, reconnecting")
            self._fts_sock = None

    def _connect_fts(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((self.fts_host, self.fts_port))
            sock.settimeout(None)
            self._fts_sock = sock
            logger.info("Connected to FTS at %s:%d", self.fts_host, self.fts_port)
        except OSError as e:
            logger.warning("Cannot connect to FTS at %s:%d: %s", self.fts_host, self.fts_port, e)
            self._fts_sock = None

    # ── FTS → Meshtastic ──────────────────────────────────────────────

    def _fts_reader_loop(self):
        """Read streaming CoT events from FTS and relay to mesh."""
        buf = ""
        while self._running:
            if self._fts_sock is None:
                self._connect_fts()
                if self._fts_sock is None:
                    time.sleep(5)
                    continue

            try:
                data = self._fts_sock.recv(4096)
                if not data:
                    logger.warning("FTS connection closed")
                    self._fts_sock = None
                    time.sleep(2)
                    continue

                buf += data.decode("utf-8", errors="replace")

                # Split on </event> to extract complete CoT events
                while "</event>" in buf:
                    end = buf.index("</event>") + len("</event>")
                    event_xml = buf[:end].strip()
                    buf = buf[end:]

                    # Find the start of the event
                    start = event_xml.rfind("<event")
                    if start < 0:
                        continue
                    event_xml = event_xml[start:]

                    self._handle_fts_event(event_xml)

            except socket.timeout:
                continue
            except OSError:
                logger.warning("FTS read error, reconnecting")
                self._fts_sock = None
                time.sleep(2)

    def _handle_fts_event(self, cot_xml: str):
        # Filter out events that originated from this bridge
        uid = self._extract_uid(cot_xml)
        if uid and uid.startswith(UID_PREFIX):
            return

        # Convert to Meshtastic packet
        mesh_pkt = cot_xml_to_meshtastic(cot_xml)
        if mesh_pkt is None:
            return

        logger.info("FTS→Mesh: %s [%s]", mesh_pkt.get("portnum", "?"), uid or "?")
        self._send_to_mesh(mesh_pkt)

    def _send_to_mesh(self, mesh_pkt: dict):
        global _last_mesh_send

        if self._interface is None and not self.simulate:
            return

        # Rate limit
        now = time.time()
        wait = _MESH_SEND_INTERVAL - (now - _last_mesh_send)
        if wait > 0:
            time.sleep(wait)
        _last_mesh_send = time.time()

        portnum = mesh_pkt.get("portnum")

        if self.simulate:
            logger.info("SIM: Would send mesh %s: %s", portnum, mesh_pkt)
            return

        try:
            if portnum == "TEXT_MESSAGE_APP":
                self._interface.sendText(mesh_pkt["text"], wantAck=True)

            elif portnum == "WAYPOINT_APP":
                from meshtastic.protobuf import mesh_pb2

                wp_data = mesh_pkt["waypoint"]
                wp_pb = mesh_pb2.Waypoint()
                wp_pb.latitudeI = wp_data["latitudeI"]
                wp_pb.longitudeI = wp_data["longitudeI"]
                wp_pb.name = wp_data["name"]
                wp_pb.description = wp_data.get("description", "")
                self._interface.sendData(
                    wp_pb.SerializeToString(),
                    portNum=mesh_pb2.PortNum.WAYPOINT_APP,
                    wantAck=True,
                )
        except Exception:
            logger.exception("Failed to send to mesh")

    # ── Deduplication ─────────────────────────────────────────────────

    @staticmethod
    def _extract_uid(cot_xml: str) -> str | None:
        # Quick extract without full XML parse
        start = cot_xml.find('uid="')
        if start < 0:
            return None
        start += 5
        end = cot_xml.find('"', start)
        if end < 0:
            return None
        return cot_xml[start:end]

    @staticmethod
    def _is_duplicate(uid: str) -> bool:
        now = time.time()
        if uid in _recent_uids and (now - _recent_uids[uid]) < _DEDUP_WINDOW:
            return True
        _recent_uids[uid] = now
        return False

    @staticmethod
    def _cleanup_dedup():
        now = time.time()
        stale = [uid for uid, ts in _recent_uids.items() if now - ts > _DEDUP_WINDOW]
        for uid in stale:
            del _recent_uids[uid]

    # ── Simulator ─────────────────────────────────────────────────────

    def _simulator_loop(self):
        """Generate fake Meshtastic packets for testing without hardware."""
        callsigns = ["Alpha1", "Bravo2", "Charlie3", "Delta4", "Echo5"]
        base_lat, base_lon = 51.5, -1.5  # Salisbury Plain area

        positions = {cs: (base_lat + random.uniform(-0.02, 0.02),
                          base_lon + random.uniform(-0.05, 0.05))
                     for cs in callsigns}

        logger.info("Simulator started with %d nodes", len(callsigns))

        while self._running:
            cs = random.choice(callsigns)

            # Drift position slightly
            lat, lon = positions[cs]
            lat += random.uniform(-0.001, 0.001)
            lon += random.uniform(-0.001, 0.001)
            positions[cs] = (lat, lon)

            # Position packet
            packet = {
                "fromId": cs,
                "_callsign": cs,
                "decoded": {
                    "portnum": "POSITION_APP",
                    "position": {
                        "latitude": lat,
                        "longitude": lon,
                        "altitude": random.randint(80, 200),
                        "groundSpeed": random.randint(0, 15),
                        "groundTrack": int(random.uniform(0, 360) * 1e5),
                    },
                },
            }

            # Cache a random battery
            _battery_cache[cs] = random.uniform(20, 100)

            cot_xml = meshtastic_to_cot_xml(packet, _battery_cache)
            if cot_xml:
                logger.info("SIM position: %s @ %.5f, %.5f", cs, lat, lon)
                self._send_to_fts(cot_xml)

            # Occasionally send a chat message
            if random.random() < 0.15:
                msg_packet = {
                    "fromId": cs,
                    "toId": "^all",
                    "_callsign": cs,
                    "decoded": {
                        "portnum": "TEXT_MESSAGE_APP",
                        "text": random.choice([
                            "Contact north, 200m",
                            "Moving to checkpoint bravo",
                            "All clear",
                            "Request resupply",
                            "In position",
                        ]),
                    },
                }
                cot_xml = meshtastic_to_cot_xml(msg_packet, _battery_cache)
                if cot_xml:
                    logger.info("SIM chat: %s: %s", cs, msg_packet["decoded"]["text"])
                    self._send_to_fts(cot_xml)

            time.sleep(random.uniform(3, 8))


def parse_args():
    parser = argparse.ArgumentParser(description="Meshtastic ↔ FreeTAKServer bridge")
    parser.add_argument("--port", default="/dev/ttyUSB0", help="Meshtastic serial port")
    parser.add_argument("--fts-host", default="127.0.0.1", help="FreeTAKServer host")
    parser.add_argument("--fts-port", type=int, default=8087, help="FreeTAKServer port")
    parser.add_argument("--simulate", action="store_true", help="Simulate mesh traffic (no hardware)")
    return parser.parse_args()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    args = parse_args()
    bridge = MeshBridge(
        port=args.port,
        fts_host=args.fts_host,
        fts_port=args.fts_port,
        simulate=args.simulate,
    )
    bridge.start()


if __name__ == "__main__":
    main()
