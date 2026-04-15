"""Bidirectional Meshtastic ↔ TAK Server CoT bridge."""

import argparse
import asyncio
import logging
import random
import threading
import time
from configparser import ConfigParser

import pytak
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


class _BridgeReceiver(pytak.QueueWorker):
    """Reads CoT events from TAK server (rx_queue) and relays to mesh."""

    def __init__(self, queue, config, bridge: "MeshBridge"):
        super().__init__(queue, config)
        self.bridge = bridge

    async def handle_data(self, data: bytes) -> None:
        cot_xml = data.decode("utf-8", errors="replace")
        await asyncio.get_running_loop().run_in_executor(
            None, self.bridge._handle_tak_event, cot_xml
        )


class MeshBridge:
    def __init__(
        self,
        port: str,
        tak_host: str,
        tak_port: int,
        simulate: bool,
        upstream_host: str | None = None,
        upstream_port: int = 8087,
        upstream_tls: bool = False,
        upstream_certfile: str | None = None,
        upstream_cafile: str | None = None,
    ):
        self.port = port
        self.tak_host = tak_host
        self.tak_port = tak_port
        self.simulate = simulate
        self._interface = None
        self._running = False

        # PyTAK async plumbing
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tx_queue: asyncio.Queue | None = None

        # Upstream relay (optional)
        self._upstream = None
        if upstream_host:
            from bridge.upstream_relay import UpstreamRelay

            self._upstream = UpstreamRelay(
                host=upstream_host,
                port=upstream_port,
                tls=upstream_tls,
                certfile=upstream_certfile,
                cafile=upstream_cafile,
                downstream_callback=self._handle_upstream_event,
            )

    def start(self):
        self._running = True

        if self.simulate:
            logger.info("Starting in simulation mode (no hardware)")
            threading.Thread(target=self._simulator_loop, daemon=True).start()
        else:
            self._connect_meshtastic()

        threading.Thread(target=self._tak_thread, daemon=True).start()

        if self._upstream:
            self._upstream.start()

        logger.info("Bridge started — press Ctrl+C to stop")
        try:
            while self._running:
                time.sleep(5)
                self._cleanup_dedup()
        except KeyboardInterrupt:
            logger.info("Shutting down")
            self._running = False
            if self._upstream:
                self._upstream.stop()

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

    # ── PyTAK Connection ─────────────────────────────────────────────

    def _tak_thread(self):
        """Run PyTAK event loop in a dedicated thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._tak_async())
        except Exception:
            logger.exception("PyTAK thread crashed")
        finally:
            self._loop.close()
            self._loop = None

    async def _tak_async(self):
        """Connect to TAK server via PyTAK with auto-reconnect."""
        while self._running:
            try:
                config = ConfigParser()
                config["meshbridge"] = {
                    "COT_URL": f"tcp://{self.tak_host}:{self.tak_port}",
                    "COT_HOST_ID": "MESH-BRIDGE",
                }

                clitool = pytak.CLITool(config["meshbridge"])
                await clitool.setup()

                self._tx_queue = clitool.tx_queue

                receiver = _BridgeReceiver(
                    clitool.rx_queue, config["meshbridge"], self
                )
                clitool.add_task(receiver)

                logger.info(
                    "Connected to TAK via PyTAK at %s:%d",
                    self.tak_host,
                    self.tak_port,
                )

                await clitool.run()

            except OSError as e:
                logger.warning(
                    "Cannot connect to TAK at %s:%d: %s",
                    self.tak_host,
                    self.tak_port,
                    e,
                )
            except Exception:
                logger.exception("PyTAK error")
            finally:
                self._tx_queue = None

            if self._running:
                logger.info("Reconnecting to TAK in 5s...")
                await asyncio.sleep(5)

    # ── Meshtastic → TAK ──────────────────────────────────────────────

    def _on_mesh_receive(self, packet, interface=None):
        try:
            decoded = packet.get("decoded", {})
            portnum = decoded.get("portnum")
            from_id = str(packet.get("fromId", packet.get("from", "unknown")))
            callsign = self._node_id_to_callsign(from_id)

            # Enrich packet with resolved callsign for converter
            packet["_callsign"] = callsign
            logger.debug("Mesh RX: portnum=%s from=%s", portnum, callsign)

            # Cache battery from telemetry
            if portnum == "TELEMETRY_APP":
                telemetry = decoded.get("telemetry", {})
                battery = telemetry.get("deviceMetrics", {}).get("batteryLevel")
                if battery is not None:
                    _battery_cache[callsign] = float(battery)
                return

            # Convert to CoT XML
            cot_xml = meshtastic_to_cot_xml(packet, _battery_cache)
            if cot_xml is None:
                return

            uid = self._extract_uid(cot_xml)
            if uid and self._is_duplicate(uid):
                return

            logger.info("Mesh→TAK: %s from %s [%s]", portnum, callsign, uid or "?")
            self._send_to_tak(cot_xml)

            if self._upstream:
                self._upstream.send(cot_xml)

        except Exception:
            logger.exception("Error processing mesh packet")

    def _send_to_tak(self, cot_xml: str):
        """Thread-safe: enqueue CoT for PyTAK to send to TAK server."""
        if self._loop is None or self._tx_queue is None:
            logger.debug("PyTAK not connected, dropping CoT")
            return
        data = cot_xml.encode("utf-8")

        def _enqueue():
            try:
                self._tx_queue.put_nowait(data)
            except asyncio.QueueFull:
                try:
                    self._tx_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                self._tx_queue.put_nowait(data)

        try:
            self._loop.call_soon_threadsafe(_enqueue)
        except RuntimeError:
            pass  # event loop closed

    # ── TAK → Meshtastic ──────────────────────────────────────────────

    def _handle_tak_event(self, cot_xml: str):
        try:
            uid = self._extract_uid(cot_xml)
            if uid and uid.startswith(UID_PREFIX):
                return

            mesh_pkt = cot_xml_to_meshtastic(cot_xml)
            if mesh_pkt is None:
                return

            logger.info("TAK→Mesh: %s [%s]", mesh_pkt.get("portnum", "?"), uid or "?")
            self._send_to_mesh(mesh_pkt)
        except Exception:
            logger.exception("Error relaying TAK event to mesh")

    # ── Upstream → Local TAK ─────────────────────────────────────────

    def _handle_upstream_event(self, cot_xml: str):
        """Downstream callback: inject CoT from upstream into local TAK."""
        uid = self._extract_uid(cot_xml)
        if uid and uid.startswith(UID_PREFIX):
            return
        if uid and self._is_duplicate(uid):
            return
        logger.info("Upstream→Local: [%s]", uid or "?")
        self._send_to_tak(cot_xml)

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
                from meshtastic.protobuf import mesh_pb2, portnums_pb2

                wp_data = mesh_pkt["waypoint"]
                wp_pb = mesh_pb2.Waypoint()
                wp_pb.latitudeI = wp_data["latitudeI"]
                wp_pb.longitudeI = wp_data["longitudeI"]
                wp_pb.name = wp_data["name"]
                wp_pb.description = wp_data.get("description", "")
                self._interface.sendData(
                    wp_pb.SerializeToString(),
                    portNum=portnums_pb2.PortNum.WAYPOINT_APP,
                    wantAck=True,
                )

            elif portnum == "ATAK_PLUGIN":
                from meshtastic.protobuf import portnums_pb2

                self._interface.sendData(
                    mesh_pkt["tak_packet"],
                    portNum=portnums_pb2.PortNum.ATAK_PLUGIN,
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
    def _extract_type(cot_xml: str) -> str | None:
        start = cot_xml.find('type="')
        if start < 0:
            return None
        start += 6
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
                self._send_to_tak(cot_xml)

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
                    self._send_to_tak(cot_xml)

            time.sleep(random.uniform(3, 8))


def parse_args():
    parser = argparse.ArgumentParser(description="Meshtastic ↔ TAK Server bridge")
    parser.add_argument("--port", default="/dev/ttyUSB0", help="Meshtastic serial port")
    parser.add_argument("--tak-host", default="127.0.0.1", help="TAK server host")
    parser.add_argument("--tak-port", type=int, default=8088, help="TAK server CoT streaming TCP port")
    parser.add_argument("--simulate", action="store_true", help="Simulate mesh traffic (no hardware)")

    upstream = parser.add_argument_group("upstream TAK server (optional)")
    upstream.add_argument("--upstream-host", default=None, help="Remote TAK server host (enables upstream relay)")
    upstream.add_argument("--upstream-port", type=int, default=8087, help="Remote TAK server port")
    upstream.add_argument("--upstream-tls", action="store_true", help="Use TLS for upstream connection")
    upstream.add_argument("--upstream-certfile", default=None, help="Client certificate PEM for upstream TLS")
    upstream.add_argument("--upstream-cafile", default=None, help="CA certificate PEM for upstream TLS")
    return parser.parse_args()


def main():
    logging.basicConfig(
        force=True,
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    args = parse_args()
    bridge = MeshBridge(
        port=args.port,
        tak_host=args.tak_host,
        tak_port=args.tak_port,
        simulate=args.simulate,
        upstream_host=args.upstream_host,
        upstream_port=args.upstream_port,
        upstream_tls=args.upstream_tls,
        upstream_certfile=args.upstream_certfile,
        upstream_cafile=args.upstream_cafile,
    )
    bridge.start()


if __name__ == "__main__":
    main()
