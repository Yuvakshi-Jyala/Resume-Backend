"""The external CogitX email workflow branches on "Shortlisted"/"Rejected".

These tests are load-bearing: if the internal slug ever leaks onto the wire,
the workflow sees an unknown value and candidates get the wrong email (or
none). That is the incident this whole change exists to prevent recurring.
"""
import pytest

from app.clients.cogitx import email as email_client


@pytest.fixture
def captured(monkeypatch):
    """Capture the body posted to the workflow, with an export id configured."""
    sent = {}

    async def _fake_trigger(body, **kwargs):
        sent["body"] = body
        sent["kwargs"] = kwargs
        return {"success": True}

    monkeypatch.setattr(email_client, "_trigger", _fake_trigger)
    monkeypatch.setattr(email_client, "EMAIL_EXPORT_ID", "export-123")
    return sent


@pytest.mark.parametrize("slug,wire", [
    ("shortlisted", "Shortlisted"),
    ("rejected", "Rejected"),
])
async def test_slug_is_converted_to_the_wire_label(captured, slug, wire):
    assert await email_client.send_decision_email("A", "a@x.com", "SDE", slug)

    body = captured["body"]
    assert body["decision"] == wire
    assert body["payload"]["decision"] == wire
    assert f'"decision": "{wire}"' in body["text"]


async def test_unknown_decision_raises_and_never_triggers(captured):
    with pytest.raises(ValueError, match="refusing to email"):
        await email_client.send_decision_email("A", "a@x.com", "SDE", "maybe")
    assert "body" not in captured


async def test_a_title_case_decision_is_also_refused(captured):
    """Only canonical slugs are accepted — callers must not pre-convert."""
    with pytest.raises(ValueError):
        await email_client.send_decision_email("A", "a@x.com", "SDE", "Shortlisted")


async def test_noop_without_an_export_id(monkeypatch):
    monkeypatch.setattr(email_client, "EMAIL_EXPORT_ID", "")
    assert await email_client.send_decision_email("A", "a@x.com", "SDE", "shortlisted") is False
