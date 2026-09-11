from bson import ObjectId

from app.domain import statuses as s
from app.services.stats import compute_stats
from tests.conftest import applicant


def role_row(stats, role="AI/ML Engineer"):
    return next(r for r in stats["roles"] if r["role"] == role)


async def test_shortlist_is_cumulative_across_the_pipeline(fake_applicants):
    fake_applicants.load([
        applicant(ObjectId(), status=s.SHORTLISTED),
        applicant(ObjectId(), status=s.CALL_SCHEDULED),
        applicant(ObjectId(), status=s.CALL_COMPLETED),
        applicant(ObjectId(), status=s.APPLIED),
    ])

    row = role_row(await compute_stats())

    assert row["applications_received"] == 4
    assert row["shortlisted"] == 3
    assert row["calls_scheduled"] == 1
    assert row["calls_completed"] == 1


async def test_a_rejected_row_never_counts_as_shortlisted(fake_applicants):
    """The incident regression test."""
    fake_applicants.load([
        applicant(ObjectId(), status=s.REJECTED),
        applicant(ObjectId(), status=s.SHORTLISTED),
    ])

    row = role_row(await compute_stats())

    assert row["applications_received"] == 2
    assert row["shortlisted"] == 1


async def test_a_legacy_spelling_counts_the_same_as_a_slug(fake_applicants):
    fake_applicants.load([
        applicant(ObjectId(), status="Shortlisted"),
        applicant(ObjectId(), status=s.SHORTLISTED),
        applicant(ObjectId(), status="Call Scheduled"),
    ])

    row = role_row(await compute_stats())

    assert row["shortlisted"] == 3
    assert row["calls_scheduled"] == 1
    assert row["unknown_status"] == 0


async def test_one_unreadable_row_does_not_break_the_recompute(fake_applicants):
    fake_applicants.load([
        applicant(ObjectId(), status="banana"),
        applicant(ObjectId(), status=s.SHORTLISTED),
    ])

    row = role_row(await compute_stats())

    assert row["applications_received"] == 2
    assert row["shortlisted"] == 1
    assert row["unknown_status"] == 1


async def test_blank_status_is_treated_as_applied(fake_applicants):
    fake_applicants.load([applicant(ObjectId(), status=None)])

    row = role_row(await compute_stats())

    assert row["shortlisted"] == 0
    assert row["unknown_status"] == 0


async def test_interviews_are_scheduled_calls_only(fake_applicants):
    fake_applicants.load([
        applicant(ObjectId(), status=s.CALL_SCHEDULED, interview_date="2026-10-01",
                  interview_time="10:00"),
        # Completed: no longer upcoming.
        applicant(ObjectId(), status=s.CALL_COMPLETED, interview_date="2026-09-01"),
        # Scheduled but undated: nothing to show.
        applicant(ObjectId(), status=s.CALL_SCHEDULED),
    ])

    stats = await compute_stats()

    assert [i["date"] for i in stats["interviews"]] == ["2026-10-01"]


async def test_candidate_rows_carry_slugs_and_labels(fake_applicants):
    fake_applicants.load([applicant(
        ObjectId(), status=s.CALL_SCHEDULED,
        decision={"value": s.SHORTLISTED, "at": None, "raw": "shortlist"},
    )])

    stats = await compute_stats()
    candidate = stats["candidates_by_role"]["AI/ML Engineer"][0]

    assert candidate["status"] == "call_scheduled"
    assert candidate["status_label"] == "Call Scheduled"
    assert candidate["decision"] == "shortlisted"
    assert candidate["decision_label"] == "Shortlisted"


async def test_a_legacy_flat_decision_still_reads(fake_applicants):
    """Stats must survive un-migrated rows, not just post-backfill ones."""
    fake_applicants.load([applicant(ObjectId(), status="Shortlisted",
                                    decision="shortlist")])

    stats = await compute_stats()
    candidate = stats["candidates_by_role"]["AI/ML Engineer"][0]

    assert candidate["decision"] == "shortlisted"
    assert candidate["decision_label"] == "Shortlisted"
