"""bridge.check's TAK round-trip (contract v5) against a fake OTS that enforces the real rules."""

import pytest

from bridge import check
from tests.fake_ots import FakeOTS


@pytest.fixture
def ots():
    o = FakeOTS(bind_delay=0.3)
    yield o
    o.close()


def run(ots, **kw):
    kw.setdefault("register_delay", 0.5)
    kw.setdefault("db", None)
    return check.check_tak("127.0.0.1", ots.port, timeout=2, station="isr-pi", **kw)


def test_round_trip_ok_with_stable_identities(ots):
    ok, msg = run(ots)
    assert ok and msg.startswith("round-trip OK"), msg
    assert ots.euds["isrkit-check-a-isr-pi"] == "CHECK-A isr-pi"
    assert ots.euds["isrkit-check-b-isr-pi"] == "CHECK-B isr-pi"
    ok2, _ = run(ots)  # same identities again: still fine, no new rows
    assert ok2 and len(ots.euds) == 2


def test_listener_not_bound_when_registering_too_early(ots):
    ok, msg = run(ots, register_delay=0.0)
    assert not ok and "listener not bound" in msg


def test_unregistered_sender_dropped_when_callsign_taken(ots):
    ots.euds["someone-else"] = "CHECK-A isr-pi"  # an older EUD owns CHECK-A's callsign
    ok, msg = run(ots)
    assert not ok and "unregistered sender dropped" in msg


def test_db_lookup_names_the_callsign_owner(ots):
    ots.euds["someone-else"] = "CHECK-A isr-pi"

    def db(uids, callsigns):
        return {"by_uid": {u: c for u, c in ots.euds.items() if u in uids},
                "by_callsign": {c: u for u, c in ots.euds.items() if c in callsigns}}

    ok, msg = run(ots, db=db)
    assert not ok and msg.startswith("unregistered sender dropped") and "someone-else" in msg


def test_db_lookup_says_listener_not_bound_when_both_registered(ots):
    def db(uids, callsigns):
        return {"by_uid": {u: c for u, c in ots.euds.items() if u in uids}, "by_callsign": {}}

    ok, msg = run(ots, register_delay=0.0, db=db)
    assert not ok and msg.startswith("listener not bound")


def test_euds_lookup_without_psql_or_config_is_none(tmp_path):
    assert check.euds_lookup(["u"], ["c"], config=str(tmp_path / "missing.yml")) is None
