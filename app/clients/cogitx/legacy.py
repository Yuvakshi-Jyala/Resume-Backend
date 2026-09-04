"""Fallback parsers for the pre-JSON workflow output shape.

Still live: run_screening()'s old-format branch calls _cards_from_report()
when the workflow returns prose instead of structured JSON.

NOTE on band_from_score below: it intentionally uses different thresholds
(85/70/50) and different labels ("Fast-track"/"Shortlisted") than
app.domain.bands.band_for_score (85/75/65/55, "Fast Track"/"Strong
Shortlist"/...). Its output is fed through normalize_band() downstream by
callers, so left exactly as it was rather than unified — changing it here
would change behavior for the legacy prose path.
"""
import re


def _cards_from_report(report: str) -> list:
    """Fallback card builder: parse per-candidate cards out of the markdown
    report when the workflow didn't return structured data.

    Looks for detail blocks headed by:  **Name** — Role — Score/100
    and pulls Matched/Gaps/Verdict from the following lines. Also picks up
    non-shortlisted candidates from a '### Not shortlisted' bullet list.
    """
    cards = []
    lines = report.split("\n")

    # 1) Shortlisted candidates: **Name** — Role — NN/100 (or NN)
    header_re = re.compile(
        r"^\*\*(?P<name>[^*]+?)\*\*\s*[—–-]\s*(?P<role>.+?)\s*[—–-]\s*(?P<score>\d+(?:\.\d+)?)\s*(?:/\s*100)?\s*$"
    )
    matched_re = re.compile(r"^\*\*Matched:?\*\*\s*(.+)$", re.IGNORECASE)
    gaps_re = re.compile(r"^\*\*Gaps:?\*\*\s*(.+)$", re.IGNORECASE)
    verdict_re = re.compile(r"^\*\*Verdict:?\*\*\s*(.+)$", re.IGNORECASE)

    def split_skills(s: str) -> list:
        # strip trailing markdown spaces, split on commas
        return [x.strip() for x in s.replace("  ", " ").split(",") if x.strip()]

    def band_from_score(score: float) -> str:
        if score >= 85:
            return "Fast-track"
        if score >= 70:
            return "Shortlisted"
        if score >= 50:
            return "Hold"
        return "Reject"

    seen = set()
    for i, line in enumerate(lines):
        m = header_re.match(line.strip())
        if not m:
            continue
        name = m.group("name").strip()
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        role = m.group("role").strip()
        score = float(m.group("score"))

        matched, gaps, summary = [], [], ""
        # scan the next ~15 lines for this candidate's fields
        for j in range(i + 1, min(i + 16, len(lines))):
            t = lines[j].strip()
            if header_re.match(t):
                break
            mm = matched_re.match(t)
            if mm:
                matched = split_skills(mm.group(1))
            gm = gaps_re.match(t)
            if gm:
                g = gm.group(1).strip()
                gaps = [] if g.lower() in ("none", "none.") else split_skills(g)
            vm = verdict_re.match(t)
            if vm:
                summary = vm.group(1).strip()

        cards.append({
            "name": name,
            "role": role,
            "email": None,
            "phone": None,
            "fit_score": int(score) if score.is_integer() else score,
            "band": band_from_score(score),
            "matched_skills": matched,
            "missing_skills": gaps,
            "summary": summary,
            "experience_years": None,
        })

    return cards


def _merge_cards(results: list, candidates: list) -> list:
    """Join scoring (results) with profile (candidates) by candidate_name into
    a flat per-candidate card the frontend can render directly. Scoring drives
    the list; profile fields are looked up and attached where the name matches."""
    by_name = {}
    for c in candidates:
        if isinstance(c, dict) and c.get("candidate_name"):
            by_name[c["candidate_name"]] = c

    cards = []
    for r in results:
        if not isinstance(r, dict):
            continue
        name = r.get("candidate_name", "")
        prof = by_name.get(name, {})
        cards.append({
            "name": name,
            "role": r.get("matched_role") or prof.get("role_applied") or "",
            "email": prof.get("email"),
            "phone": prof.get("phone"),
            "fit_score": r.get("fit_score"),
            "band": r.get("band"),
            "matched_skills": r.get("matched_skills") or [],
            "missing_skills": r.get("missing_skills") or [],
            "summary": r.get("summary_reason") or "",
            "experience_years": prof.get("total_experience_years"),
        })
    return cards
