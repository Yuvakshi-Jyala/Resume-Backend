"""Canonical decision-band vocabulary and score->band thresholds.

Single source of truth for the rubric bands. Previously the thresholds
existed in three places with different values: stats._band_for,
main.get_applicants, and cogitx._cards_from_report's inner band_from_score.
The latter (app.clients.cogitx.legacy) intentionally keeps its own different
thresholds/labels — see the comment there.
"""
from app.domain.rubric import effective_fit_score

# The 5 rubric decision bands, in display order.
BANDS = ["Fast Track", "Strong Shortlist", "Shortlist", "Hold", "Reject"]

_CANONICAL_BANDS = {
    "fast track": "Fast Track",
    "strong shortlist": "Strong Shortlist",
    "shortlist": "Shortlist",
    "hold": "Hold",
    "reject": "Reject",
}


def normalize_band(band):
    """Map any casing/hyphen/spacing variant to the 5 canonical rubric bands.
    Unknown values are returned unchanged."""
    if not band or not isinstance(band, str):
        return band
    key = " ".join(band.strip().lower().replace("-", " ").replace("_", " ").split())
    return _CANONICAL_BANDS.get(key, band)


def band_for_score(score: float) -> str:
    """Rubric decision bands (score out of 100)."""
    score = float(score or 0)
    if score >= 85:
        return "Fast Track"
    if score >= 75:
        return "Strong Shortlist"
    if score >= 65:
        return "Shortlist"
    if score >= 55:
        return "Hold"
    return "Reject"


def band_for_applicant(doc: dict) -> str:
    """The candidate's band, derived from the score the rubric produces.

    Deriving rather than trusting the workflow's `band` string keeps the band
    consistent with the score shown beside it. The stored band is only used when
    there's no score at all to derive from.
    """
    analysis = doc.get("analysis") or {}
    score = effective_fit_score(analysis, doc.get("score"))
    if score is not None:
        return band_for_score(score)
    return normalize_band(analysis.get("band")) or band_for_score(0)
