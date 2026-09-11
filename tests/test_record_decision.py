import pytest
from bson import ObjectId
from fastapi import HTTPException

import app.services.applicants as svc
from app.domain import statuses as s
from tests.conftest import applicant


@pytest.fixture
def no_email(monkeypatch):
    """Email workflow that always succeeds, capturing its arguments."""
    calls = []

    async def _send(name, email, role, decision):
        calls.append({"name": name, "email": email, "role": role,
                      "decision": decision})
        return True

    monkeypatch.setattr(svc.cogitx, "send_decision_email", _send)
    return calls


async def test_alias_is_stored_canonically_with_the_raw_string(fake_applicants, no_email):
    oid = ObjectId()
    fake_applicants.load([applicant(oid)])

    result = await svc.record_decision(str(oid), "shortlist")

    doc = fake_applicants.docs[0]
    assert doc["status"] == s.SHORTLISTED
    assert doc["decision"]["value"] == s.SHORTLISTED
    assert doc["decision"]["raw"] == "shortlist"     # the audit point
    assert doc["decision"]["at"] is not None

    assert result["status"] == "shortlisted"
    assert result["status_label"] == "Shortlisted"
    assert result["decision"] == "shortlisted"
    assert result["email_sent"] is True


async def test_the_email_client_receives_the_slug(fake_applicants, no_email):
    oid = ObjectId()
    fake_applicants.load([applicant(oid)])

    await svc.record_decision(str(oid), "REJECT")

    assert no_email[0]["decision"] == s.REJECTED   # client converts to the label


async def test_unknown_decision_is_a_400_and_writes_nothing(fake_applicants, no_email):
    oid = ObjectId()
    fake_applicants.load([applicant(oid, status=s.APPLIED)])

    with pytest.raises(HTTPException) as e:
        await svc.record_decision(str(oid), "maybe")

    assert e.value.status_code == 400
    assert fake_applicants.docs[0]["status"] == s.APPLIED
    assert "decision" not in fake_applicants.docs[0]
    assert no_email == []


async def test_a_pipeline_status_is_not_an_acceptable_decision(fake_applicants, no_email):
    oid = ObjectId()
    fake_applicants.load([applicant(oid)])

    with pytest.raises(HTTPException) as e:
        await svc.record_decision(str(oid), "call scheduled")
    assert e.value.status_code == 400


async def test_email_failure_still_persists_the_decision(fake_applicants, monkeypatch):
    oid = ObjectId()
    fake_applicants.load([applicant(oid)])

    async def _boom(*a, **k):
        raise RuntimeError("workflow down")

    monkeypatch.setattr(svc.cogitx, "send_decision_email", _boom)

    result = await svc.record_decision(str(oid), "shortlist")

    assert result["email_sent"] is False
    assert fake_applicants.docs[0]["status"] == s.SHORTLISTED
    assert fake_applicants.docs[0]["email_sent"] is False


async def test_missing_candidate_email_skips_the_send(fake_applicants, no_email):
    oid = ObjectId()
    doc = applicant(oid)
    doc["analysis"]["email"] = None
    fake_applicants.load([doc])

    result = await svc.record_decision(str(oid), "shortlist")

    assert result["email_sent"] is False
    assert no_email == []
    assert fake_applicants.docs[0]["status"] == s.SHORTLISTED


async def test_unknown_applicant_is_a_404(fake_applicants, no_email):
    fake_applicants.load([])
    with pytest.raises(HTTPException) as e:
        await svc.record_decision(str(ObjectId()), "shortlist")
    assert e.value.status_code == 404


async def test_malformed_id_is_a_400(fake_applicants, no_email):
    with pytest.raises(HTTPException) as e:
        await svc.record_decision("not-an-objectid", "shortlist")
    assert e.value.status_code == 400
