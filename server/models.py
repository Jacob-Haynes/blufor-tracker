import time
import uuid

from pydantic import BaseModel, Field


class PositionReport(BaseModel):
    type: str = "position"
    callsign: str
    lat: float
    lon: float
    altitude: float = 0.0
    timestamp: float  # unix epoch
    battery: float | None = None  # 0-100 percent
    speed: float | None = None  # km/h
    heading: float | None = None  # degrees
    stale: bool = False


class Message(BaseModel):
    type: str = "message"
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = Field(default_factory=time.time)
    sender: str
    channel: str  # "BROADCAST", callsign for DM, or "HQ"
    body: str
