"""Small display-formatting helpers shared by the applicants routes/services.

experience_label had two near-identical copies (list view vs. detail view)
that differ only in how they label an unknown `years`. Preserved here via the
`unknown` parameter rather than unifying the two outputs.
"""


def experience_label(years, unknown: str = "—") -> str:
    if years is None:
        return unknown
    if years < 1:
        return "Fresher"
    if years == int(years):
        return f"{int(years)} yr" if years == 1 else f"{int(years)} yrs"
    return f"{years:.1f} yrs"


def skills_bucket(score: float) -> str:
    if score >= 75:
        return "Strong"
    if score >= 60:
        return "Moderate"
    return "Weak"
