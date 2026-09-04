"""Canonical decision-band vocabulary and score->band thresholds.

Single source of truth for the rubric bands. Previously the thresholds
existed in three places with different values: stats._band_for,
main.get_applicants, and cogitx._cards_from_report's inner band_from_score.
The latter (app.clients.cogitx.legacy) intentionally keeps its own different
thresholds/labels — see the comment there.
"""

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
    """Rubric decision bands (score out of ~105)."""
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
    """The candidate's band: use the workflow-assigned band if present, else
    derive it from the score using the rubric thresholds."""
    analysis = doc.get("analysis") or {}
    band = normalize_band(analysis.get("band"))
    if band:
        return band
    score = analysis.get("fit_score")
    if score is None:
        score = doc.get("score")
    return band_for_score(score)
