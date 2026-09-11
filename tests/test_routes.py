import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

import app.api.routes.applicants as routes
import app.services.applicants as svc
from app.domain import statuses as s
from app.main import app
from tests.conftest import applicant


@pytest.fixture
def client(fake_applicants, monkeypatch):
    async def _send(name, email, role, decision):
        return True

    monkeypatch.setattr(svc.cogitx, "send_decision_email", _send)
    # Deliberately NOT used as a context manager: that would run the lifespan,
    # which builds job indexes against a real Mongo. These tests exercise the
    # routes over the fake collection only.
    return TestClient(app)


def test_a_decision_alias_is_accepted(client, fake_applicants):
    oid = ObjectId()
    fake_applicants.load([applicant(oid)])

    r = client.post("/api/decision", json={"id": str(oid), "decision": "shortlist"})

    assert r.status_code == 200
    assert r.json()["status"] == "shortlisted"
    assert r.json()["status_label"] == "Shortlisted"


def test_an_unknown_decision_is_422(client, fake_applicants):
    oid = ObjectId()
    fake_applicants.load([applicant(oid)])

    r = client.post("/api/decision", json={"id": str(oid), "decision": "maybe"})

    assert r.status_code == 422
    assert fake_applicants.docs[0]["status"] == s.APPLIED


def test_backfill_defaults_to_a_dry_run(client, fake_applicants):
    fake_applicants.load([applicant(ObjectId(), status="Rejected",
                                    decision="shortlist")])

    r = client.post("/api/admin/backfill/applicant-vocabulary")

    assert r.status_code == 200
    assert r.json()["applied"] is False
    assert r.json()["updated"] == 0
    assert fake_applicants.docs[0]["status"] == "Rejected"   # untouched


def test_apply_is_refused_without_a_token(client, fake_applicants, monkeypatch):
    monkeypatch.setattr(routes, "BACKFILL_TOKEN", "s3cret")
    fake_applicants.load([applicant(ObjectId(), status="Rejected",
                                    decision="shortlist")])

    r = client.post("/api/admin/backfill/applicant-vocabulary?apply=true")

    assert r.status_code == 403
    assert fake_applicants.docs[0]["status"] == "Rejected"


def test_apply_is_refused_with_a_wrong_token(client, fake_applicants, monkeypatch):
    monkeypatch.setattr(routes, "BACKFILL_TOKEN", "s3cret")
    fake_applicants.load([applicant(ObjectId(), status="Rejected",
                                    decision="shortlist")])

    r = client.post("/api/admin/backfill/applicant-vocabulary?apply=true",
                    headers={"X-Admin-Token": "wrong"})

    assert r.status_code == 403


def test_apply_is_disabled_while_the_token_is_unconfigured(client, fake_applicants,
                                                           monkeypatch):
    monkeypatch.setattr(routes, "BACKFILL_TOKEN", "")
    fake_applicants.load([applicant(ObjectId(), status="Rejected",
                                    decision="shortlist")])

    r = client.post("/api/admin/backfill/applicant-vocabulary?apply=true",
                    headers={"X-Admin-Token": "anything"})

    assert r.status_code == 403
    assert fake_applicants.docs[0]["status"] == "Rejected"


def test_apply_with_the_right_token_migrates(client, fake_applicants, monkeypatch):
    monkeypatch.setattr(routes, "BACKFILL_TOKEN", "s3cret")
    fake_applicants.load([applicant(ObjectId(), status="Rejected",
                                    decision="shortlist")])

    r = client.post("/api/admin/backfill/applicant-vocabulary?apply=true",
                    headers={"X-Admin-Token": "s3cret"})

    assert r.status_code == 200
    assert r.json()["updated"] == 1
    assert fake_applicants.docs[0]["status"] == s.SHORTLISTED


def test_the_deprecated_path_still_works(client, fake_applicants):
    fake_applicants.load([applicant(ObjectId(), status="Rejected",
                                    decision="shortlist")])

    r = client.post("/api/admin/backfill/decision-status")

    assert r.status_code == 200
    assert r.json()["counts"]["contradiction"] == 1


def test_kpi_builds_the_summary_on_a_cold_cache(client, fake_applicants, fake_stats):
    """Regression: the miss path re-read the same empty doc and 500'd."""
    fake_applicants.load([
        applicant(ObjectId(), status=s.SHORTLISTED),
        applicant(ObjectId(), status=s.REJECTED),
    ])
    assert fake_stats.docs == []          # nothing has written the cache yet

    r = client.get("/api/kpi")

    assert r.status_code == 200
    row = next(x for x in r.json()["roles"] if x["role"] == "AI/ML Engineer")
    assert row["applications_received"] == 2
    assert row["shortlisted"] == 1
    # The summary was built and cached, so the next read is warm.
    assert len(fake_stats.docs) == 1


def test_kpi_serves_the_warm_cache_without_recomputing(client, fake_applicants,
                                                       fake_stats, monkeypatch):
    import app.api.routes.kpi as kpi_routes

    fake_applicants.load([applicant(ObjectId(), status=s.SHORTLISTED)])
    client.get("/api/kpi")                # warm it

    async def _boom():
        raise AssertionError("recalc_stats called on a warm cache")

    monkeypatch.setattr(kpi_routes, "recalc_stats", _boom)

    r = client.get("/api/kpi")

    assert r.status_code == 200
    assert r.json()["roles"][0]["shortlisted"] == 1


def test_listing_returns_slugs_and_labels(client, fake_applicants):
    fake_applicants.load([applicant(ObjectId(), status="Call Scheduled")])

    r = client.get("/api/applicants")

    assert r.json()[0]["status"] == "call_scheduled"
    assert r.json()[0]["status_label"] == "Call Scheduled"
