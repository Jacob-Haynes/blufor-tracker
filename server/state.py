import asyncio
import threading
import time

from server.models import Message, PositionReport

STALE_THRESHOLD_S = 60


class PositionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._positions: dict[str, PositionReport] = {}
        self._queue: asyncio.Queue[PositionReport] | None = None

    def set_queue(self, queue: asyncio.Queue[PositionReport]) -> None:
        self._queue = queue

    def update(self, report: PositionReport) -> None:
        with self._lock:
            self._positions[report.callsign] = report
        if self._queue is not None:
            try:
                self._queue.put_nowait(report)
            except asyncio.QueueFull:
                pass

    def get_all(self) -> list[PositionReport]:
        now = time.time()
        with self._lock:
            results = []
            for p in self._positions.values():
                report = p.model_copy(
                    update={"stale": (now - p.timestamp) > STALE_THRESHOLD_S}
                )
                results.append(report)
            return results


store = PositionStore()


class MessageStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._messages: list[Message] = []
        self._queue: asyncio.Queue[Message] | None = None

    def set_queue(self, queue: asyncio.Queue[Message]) -> None:
        self._queue = queue

    def add(self, msg: Message) -> None:
        with self._lock:
            self._messages.append(msg)
        if self._queue is not None:
            try:
                self._queue.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    def get_for_participant(self, callsign: str) -> list[Message]:
        """Return messages visible to a given callsign (or HQ)."""
        with self._lock:
            if callsign == "HQ":
                return list(self._messages)
            return [
                m
                for m in self._messages
                if m.channel == "BROADCAST"
                or m.channel == callsign
                or m.sender == callsign
                or m.channel == "HQ" and m.sender == callsign
            ]

    def get_all(self) -> list[Message]:
        with self._lock:
            return list(self._messages)


message_store = MessageStore()
