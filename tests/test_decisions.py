from datetime import datetime, timezone

import pytest

from app.domain import decisions as d
from app.domain import statuses as s


def test_decisions_are_statuses():
    """Anti-drift: the two vocabularies cannot diverge."""
    for slug in d.DECISIONS:
        assert slug in s.STATUSES


@pytest.mark.parametrize("value,expected", [
    ("shortlist", s.SHORTLISTED),
    ("Shortlisted", s.SHORTLISTED),
    ("accept", s.SHORTLISTED),
    ("reject", s.REJECTED),
    ("REJECTED", s.REJECTED),
    ("declined", s.REJECTED),
])
def test_known_spellings(value, expected):
    assert d.normalize_decision(value) == expected


@pytest.mark.parametrize("not_a_decision", ["call scheduled", "applied", "interviewed"])
def test_a_status_is_not_a_decision(not_a_decision):
    with pytest.raises(ValueError, match="not a decision"):
        d.normalize_decision(not_a_decision)


@pytest.mark.parametrize("bad", [None, "", 5, "maybe"])
def test_unreadable_raises(bad):
    with pytest.raises(ValueError):
        d.normalize_decision(bad)


def test_record_preserves_the_callers_raw_string():
    rec = d.decision_record("shortlist")
    assert rec["value"] == s.SHORTLISTED
    assert rec["raw"] == "shortlist"          # the audit point
    assert rec["at"].tzinfo is not None


def test_record_accepts_an_explicit_timestamp():
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert d.decision_record("accept", at=at)["at"] == at


def test_read_legacy_flat_string():
    rec = d.read_decision("shortlist")
    assert rec == {"value": s.SHORTLISTED, "at": None, "raw": "shortlist",
                   "legacy": True}


def test_read_record_shape():
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rec = d.read_decision({"value": "rejected", "at": at, "raw": "reject"})
    assert rec["value"] == s.REJECTED
    assert rec["legacy"] is False
    assert rec["at"] == at


@pytest.mark.parametrize("absent", [None, ""])
def test_read_no_decision(absent):
    assert d.read_decision(absent) is None


@pytest.mark.parametrize("garbage", ["banana", {"value": "banana"}, 7])
def test_read_never_raises(garbage):
    assert d.read_decision(garbage)["value"] == s.UNKNOWN


def test_payload_is_flat():
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert d.decision_payload({"value": "shortlisted", "at": at, "raw": "x"}) == {
        "decision": "shortlisted",
        "decision_label": "Shortlisted",
        "decision_at": at,
    }


def test_payload_with_no_decision():
    assert d.decision_payload(None) == {
        "decision": None, "decision_label": None, "decision_at": None}
