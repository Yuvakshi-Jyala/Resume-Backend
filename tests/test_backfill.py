import copy

from bson import ObjectId

import app.services.applicants as svc
from app.domain import statuses as s
from tests.conftest import applicant


def ids(rows):
    return {r["id"] for r in rows}


async def test_dry_run_writes_nothing(fake_applicants):
    oid = ObjectId()
    fake_applicants.load([applicant(oid, status="Rejected", decision="shortlist")])
    before = copy.deepcopy(fake_applicants.docs)

    result = await svc.normalize_applicants()

    assert result["applied"] is False
    assert result["updated"] == 0
    assert fake_applicants.docs == before
    assert result["counts"]["contradiction"] == 1


async def test_the_incident_row_is_repaired(fake_applicants):
    """status Rejected + decision 'shortlist' -> shortlisted, as intended."""
    oid = ObjectId()
    fake_applicants.load([
        applicant(oid, status="Rejected", decision="shortlist", email_sent=True),
    ])

    result = await svc.normalize_applicants(apply=True)

    doc = fake_applicants.docs[0]
    assert doc["status"] == s.SHORTLISTED
    assert doc["decision"]["value"] == s.SHORTLISTED
    assert doc["decision"]["raw"] == "shortlist"
    assert doc["decision"]["at"] is None            # never invented
    assert doc["status_backfilled_from"] == {"status": "Rejected",
                                             "decision": "shortlist"}
    assert result["updated"] == 1
    assert result["needs_email_followup"][0]["should_have_been"] == s.SHORTLISTED


async def test_status_spelling_is_normalized(fake_applicants):
    oid = ObjectId()
    fake_applicants.load([applicant(oid, status="Call Scheduled")])

    result = await svc.normalize_applicants(apply=True)

    assert fake_applicants.docs[0]["status"] == s.CALL_SCHEDULED
    assert result["counts"]["status_normalized"] == 1


async def test_missing_status_is_seeded(fake_applicants):
    a, b = ObjectId(), ObjectId()
    fake_applicants.load([
        applicant(a, status=None),
        applicant(b, status="", decision="reject"),
    ])

    await svc.normalize_applicants(apply=True)

    by_id = {d["_id"]: d for d in fake_applicants.docs}
    assert by_id[a]["status"] == s.APPLIED             # no decision -> applied
    assert by_id[b]["status"] == s.REJECTED            # derived from the decision


async def test_legacy_flat_decision_is_migrated(fake_applicants):
    oid = ObjectId()
    fake_applicants.load([applicant(oid, status="Shortlisted", decision="Shortlisted")])

    result = await svc.normalize_applicants(apply=True)

    assert fake_applicants.docs[0]["decision"] == {
        "value": s.SHORTLISTED,
        "at": None,
        "raw": "Shortlisted",
        "migrated_at": fake_applicants.docs[0]["decision"]["migrated_at"],
    }
    assert result["counts"]["decision_migrated"] == 1


async def test_later_stage_consistent_with_the_decision_is_ok(fake_applicants):
    """call_scheduled + shortlisted is progression, not a contradiction."""
    oid = ObjectId()
    fake_applicants.load([applicant(
        oid, status=s.CALL_SCHEDULED,
        decision={"value": s.SHORTLISTED, "at": None, "raw": "shortlist"},
    )])

    result = await svc.normalize_applicants(apply=True)

    assert result["ok"] == 1
    assert result["updated"] == 0
    assert fake_applicants.docs[0]["status"] == s.CALL_SCHEDULED


async def test_later_stage_contradicting_the_decision_is_a_conflict(fake_applicants):
    """call_scheduled + rejected has two readings — never guessed."""
    oid = ObjectId()
    fake_applicants.load([applicant(
        oid, status=s.CALL_SCHEDULED,
        decision={"value": s.REJECTED, "at": None, "raw": "reject"},
    )])

    result = await svc.normalize_applicants(apply=True)

    assert ids(result["conflicts"]) == {str(oid)}
    assert result["updated"] == 0
    assert fake_applicants.docs[0]["status"] == s.CALL_SCHEDULED   # untouched


async def test_unreadable_values_are_reported_not_overwritten(fake_applicants):
    a, b = ObjectId(), ObjectId()
    fake_applicants.load([
        applicant(a, status="banana"),
        applicant(b, status="Applied", decision="perhaps"),
    ])

    result = await svc.normalize_applicants(apply=True)

    assert ids(result["rows"]["unreadable_status"]) == {str(a)}
    assert ids(result["rows"]["unreadable_decision"]) == {str(b)}
    assert fake_applicants.docs[0]["status"] == "banana"
    assert fake_applicants.docs[1]["decision"] == "perhaps"
    assert result["updated"] == 0


async def test_idempotent(fake_applicants):
    fake_applicants.load([
        applicant(ObjectId(), status="Rejected", decision="shortlist"),
        applicant(ObjectId(), status="Call Scheduled"),
        applicant(ObjectId(), status=None),
    ])

    first = await svc.normalize_applicants(apply=True)
    assert first["updated"] == 3

    second = await svc.normalize_applicants(apply=True)
    assert second["updated"] == 0
    assert second["changed"] == 0
    assert second["ok"] == 3


async def test_a_row_that_moved_underneath_the_scan_is_skipped(fake_applicants):
    a, b = ObjectId(), ObjectId()
    fake_applicants.load([
        applicant(a, status="Rejected", decision="shortlist"),
        applicant(b, status="Shortlisted"),
    ])
    fake_applicants.fail_updates_for = {a}

    result = await svc.normalize_applicants(apply=True)

    assert result["updated"] == 1
    assert result["skipped"] == 1


async def test_followup_lists_only_wrongly_emailed_contradictions(fake_applicants):
    wrong, spelling = ObjectId(), ObjectId()
    fake_applicants.load([
        # Emailed a rejection but was actually shortlisted -> needs follow-up.
        applicant(wrong, status="Rejected", decision="shortlist", email_sent=True),
        # Right email, wrong spelling -> no follow-up needed.
        applicant(spelling, status="Shortlisted", decision="Shortlisted",
                  email_sent=True),
    ])

    result = await svc.normalize_applicants(apply=True)

    assert {r["id"] for r in result["needs_email_followup"]} == {str(wrong)}


async def test_a_canonical_row_is_left_alone(fake_applicants):
    oid = ObjectId()
    fake_applicants.load([applicant(
        oid, status=s.SHORTLISTED,
        decision={"value": s.SHORTLISTED, "at": None, "raw": "shortlist"},
    )])

    result = await svc.normalize_applicants(apply=True)

    assert result["ok"] == 1
    assert "status_backfilled_at" not in fake_applicants.docs[0]
