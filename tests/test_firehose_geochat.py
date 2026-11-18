"""Firehose → LoRa GeoChat relay: kit tools' chat is not relayed, long human chat is cut to fit."""

import json
import logging

from meshtastic.protobuf import atak_pb2

from bridge.mqtt_shim import DATA_PAYLOAD_LEN, fit_chat, is_kit_uid
from tests.test_mqtt_shim_meshbus import _shim

PHONE = "ANDROID-5f1c2b9e0a7d4c11"
ADVISOR = "isrkit-tac-advisor-isr-pi"


def geochat(sender_uid, callsign, text):
    return (f'<event version="2.0" uid="GeoChat.{sender_uid}.All Chat Rooms.6b1d7c52" type="b-t-f" '
            f'time="2026-11-16T11:02:13.412Z" start="2026-11-16T11:02:13.412Z" stale="2026-11-17T11:02:13.412Z" '
            f'how="h-g-i-g-o"><point lat="51.15" lon="-1.75" hae="0" ce="9999999.0" le="9999999.0"/><detail>'
            f'<__chat parent="RootContactGroup" groupOwner="false" messageId="6b1d7c52" chatroom="All Chat Rooms" '
            f'id="All Chat Rooms" senderCallsign="{callsign}"><chatgrp uid0="{sender_uid}" uid1="All Chat Rooms" '
            f'id="All Chat Rooms"/></__chat><link uid="{sender_uid}" type="a-f-G-U-C" relation="p-p"/>'
            f'<remarks source="BAO.F.ATAK.{sender_uid}" to="All Chat Rooms" time="2026-11-16T11:02:13.412Z">'
            f'{text}</remarks></detail></event>')


def firehose(shim, sender_uid, callsign, text):
    shim._on_firehose_cot(json.dumps({"uid": sender_uid, "cot": geochat(sender_uid, callsign, text)}).encode())


def test_kit_tool_geochat_is_not_relayed_to_lora():
    shim = _shim()
    firehose(shim, ADVISOR, "ADVISOR isr-pi", "SITREP 1102Z. " + "5 simulated drone detections. " * 20)
    assert shim._interface.data == []
    assert is_kit_uid(ADVISOR) and is_kit_uid(f"GeoChat.{ADVISOR}.All Chat Rooms.x")
    assert not is_kit_uid(PHONE) and not is_kit_uid("")


def test_kit_uid_only_in_chatgrp_is_still_skipped():
    shim = _shim()
    shim._on_firehose_cot(json.dumps({"uid": "", "cot": geochat(ADVISOR, "ADVISOR isr-pi", "hi")}).encode())
    assert shim._interface.data == []


def test_short_human_geochat_relayed_unchanged():
    shim = _shim()
    firehose(shim, PHONE, "HANGOVER", "ADVISOR: SITREP")
    (payload, port), = shim._interface.data
    tak = atak_pb2.TAKPacket.FromString(payload)
    assert tak.chat.message == "ADVISOR: SITREP" and tak.contact.callsign == "HANGOVER"


def test_long_human_geochat_truncated_to_fit_and_logged_once(caplog):
    shim = _shim()
    long_text = "Contact report: " + "two vehicles moving north along the track, " * 12
    with caplog.at_level(logging.WARNING, logger="mqtt_shim"):
        firehose(shim, PHONE, "HANGOVER", long_text)
        firehose(shim, PHONE, "HANGOVER", long_text + " again")
    assert len(shim._interface.data) == 2
    for payload, _ in shim._interface.data:
        assert len(payload) <= DATA_PAYLOAD_LEN
        msg = atak_pb2.TAKPacket.FromString(payload).chat.message
        assert msg.endswith("…") and long_text.startswith(msg[:-1].rstrip())
        assert len(payload) > DATA_PAYLOAD_LEN - 8  # filled, not cut short
    assert sum("truncated" in r.getMessage() for r in caplog.records) == 1


def test_fit_chat_multibyte_text():
    tak = atak_pb2.TAKPacket()
    tak.contact.callsign = "HANGOVER"
    raw, cut = fit_chat(tak, "é" * 400)
    assert cut and len(raw) <= DATA_PAYLOAD_LEN
    assert atak_pb2.TAKPacket.FromString(raw).chat.message.endswith("…")
