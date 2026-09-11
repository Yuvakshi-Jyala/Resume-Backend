import pytest

from app.domain import statuses as s


def test_every_alias_maps_into_the_vocabulary():
    for alias in s._CANONICAL_STATUSES:
        assert s.normalize_status(alias) in s.STATUSES


def test_every_slug_is_a_fixed_point():
    for slug in s.STATUSES:
        assert s.normalize_status(slug) == slug


def test_every_label_normalizes_back_to_its_slug():
    """Catches label/alias-table drift — the exact bug class being fixed."""
    for slug in s.STATUSES:
        assert s.normalize_status(s.status_label(slug)) == slug


def test_labels_cover_every_status_and_the_sentinel():
    assert set(s.STATUS_LABELS) == set(s.STATUSES) | {s.UNKNOWN}


@pytest.mark.parametrize("bad", [None, "", "   ", 123, [], "hired", "maybe"])
def test_strict_normalizer_raises(bad):
    with pytest.raises(ValueError):
        s.normalize_status(bad)


@pytest.mark.parametrize("value,expected", [
    ("Shortlisted", s.SHORTLISTED),
    ("shortlist", s.SHORTLISTED),
    ("ACCEPT", s.SHORTLISTED),
    ("  Call-Scheduled  ", s.CALL_SCHEDULED),
    ("call_completed", s.CALL_COMPLETED),
    ("reject", s.REJECTED),
    ("Declined", s.REJECTED),
])
def test_known_spellings(value, expected):
    assert s.normalize_status(value) == expected


@pytest.mark.parametrize("bad", [None, "", "   ", 123, []])
def test_coerce_defaults_blank_to_applied(bad):
    assert s.coerce_status(bad) == s.APPLIED


def test_coerce_marks_unreadable_as_unknown_without_raising():
    assert s.coerce_status("hired") == s.UNKNOWN
    assert s.coerce_status("banana") == s.UNKNOWN


def test_rejected_is_off_the_pipeline():
    assert s.status_rank(s.REJECTED) == -1
    assert s.status_rank(s.UNKNOWN) == -1


def test_cumulative_shortlist():
    assert s.has_reached(s.SHORTLISTED, s.SHORTLISTED)
    assert s.has_reached(s.CALL_SCHEDULED, s.SHORTLISTED)
    assert s.has_reached(s.CALL_COMPLETED, s.SHORTLISTED)


def test_rejected_never_counts_as_shortlisted():
    """The incident regression test."""
    assert not s.has_reached(s.REJECTED, s.SHORTLISTED)
    assert not s.has_reached(s.UNKNOWN, s.SHORTLISTED)
    assert not s.has_reached(s.APPLIED, s.SHORTLISTED)


def test_is_terminal():
    assert s.is_terminal(s.REJECTED)
    assert not s.is_terminal(s.CALL_COMPLETED)


def test_status_payload_carries_slug_and_label():
    assert s.status_payload(s.CALL_SCHEDULED) == {
        "status": "call_scheduled",
        "status_label": "Call Scheduled",
    }
