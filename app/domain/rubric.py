"""Weighted rubric breakdown: the authoritative source of a candidate's score.

The screening workflow returns a per-candidate `rubric_breakdown` — categories
each carrying an `anchor` (0-5), a `weight`, and a `score`, with the weights
forming a 100-point budget:

    {"Technical Capability": {"anchor": 5, "weight": 20, "score": 20}, ...}

The workflow also emits a free-floating `fit_score`, but it drifts from its own
breakdown (an observed payload summed to 88 while claiming 92). The breakdown is
the auditable artifact, so it wins: fit_score is derived from it here and the
band is derived from that, which keeps the number a recruiter sees consistent
with the rows underneath it.

Pure domain logic — imports nothing from app.*, so app.domain.bands can use it.
"""

ANCHOR_MAX = 5      # anchors are scored 0-5
RUBRIC_TOTAL = 100  # weights are a 100-point budget


def _number(value):
    """Coerce to float, or None if it isn't a usable number.

    bool is excluded explicitly: it's a subclass of int, and a stray True would
    otherwise become a weight of 1.0 and silently skew the total.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_rubric(raw) -> dict:
    """Clean the workflow's breakdown into {category: {anchor, weight, score}}.

    Rows that aren't dicts, or that carry no positive weight, are dropped — a
    zero-weight category contributes nothing to the score and would only make
    the displayed breakdown noisier. When `score` is missing or unparseable it
    is derived from the anchor as `anchor / ANCHOR_MAX * weight`, the
    relationship the workflow's own numbers hold to.

    Never raises: anything unusable comes back as {}.
    """
    if not isinstance(raw, dict):
        return {}

    out = {}
    for category, row in raw.items():
        if not isinstance(category, str) or not isinstance(row, dict):
            continue

        weight = _number(row.get("weight"))
        if weight is None or weight <= 0:
            continue

        anchor = _number(row.get("anchor"))
        score = _number(row.get("score"))
        if score is None and anchor is not None:
            score = anchor / ANCHOR_MAX * weight
        if score is None:
            continue

        # A category can't earn more than its weight or less than nothing.
        score = max(0.0, min(score, weight))

        out[category] = {"anchor": anchor, "weight": weight, "score": score}

    return out


def rubric_total_weight(breakdown) -> float:
    """Sum of the category weights (100 for a well-formed breakdown)."""
    return sum(r["weight"] for r in normalize_rubric(breakdown).values())


def score_from_rubric(breakdown) -> float | None:
    """The candidate's score out of 100, or None if there's no usable breakdown.

    Scaling by the total weight is a no-op when the weights already sum to 100
    (the normal case), and is what stops a truncated or reweighted breakdown
    from silently reading as a low score rather than a partial one.
    """
    rows = normalize_rubric(breakdown)
    if not rows:
        return None

    total_weight = sum(r["weight"] for r in rows.values())
    if total_weight <= 0:
        return None

    score = sum(r["score"] for r in rows.values()) / total_weight * RUBRIC_TOTAL
    return round(max(0.0, min(score, float(RUBRIC_TOTAL))), 1)


def effective_fit_score(analysis, fallback=None) -> float | None:
    """The score to use for a candidate, rubric first.

    Falls back to the workflow's asserted fit_score and then to `fallback` (the
    applicant document's top-level score), so rows written before the rubric
    existed keep the score they were stored with — no migration needed.
    """
    if not isinstance(analysis, dict):
        analysis = {}

    score = score_from_rubric(analysis.get("rubric_breakdown"))
    if score is not None:
        return score

    for candidate in (analysis.get("fit_score"), fallback):
        value = _number(candidate)
        if value is not None:
            return value
    return None


def rubric_extremes(breakdown) -> tuple[dict | None, dict | None]:
    """The (strongest, weakest) categories as {category, score, weight}.

    Ranked by the fraction of the weight earned, so a 3/5 in a 20-point category
    doesn't outrank a 5/5 in a 5-point one. Both ends tie-break toward the
    heavier weight — among categories scoring equally well the 20-point one is
    the more meaningful strength, and among equally weak ones it's the bigger
    hole (9/15 loses six points where 3/5 loses two).
    """
    rows = normalize_rubric(breakdown)
    if not rows:
        return None, None

    def entry(item):
        category, r = item
        return {"category": category, "score": r["score"], "weight": r["weight"]}

    items = list(rows.items())
    best = max(items, key=lambda i: (i[1]["score"] / i[1]["weight"], i[1]["weight"]))
    worst = min(items, key=lambda i: (i[1]["score"] / i[1]["weight"], -i[1]["weight"]))
    return entry(best), entry(worst)
