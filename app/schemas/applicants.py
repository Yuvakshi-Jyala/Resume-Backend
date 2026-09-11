from pydantic import BaseModel, field_validator

from app.domain.decisions import normalize_decision


class Decision(BaseModel):
    id: str
    # Any known spelling ("shortlist", "accept", "Shortlisted", ...) is accepted;
    # anything else is a 422.
    decision: str

    @field_validator("decision")
    @classmethod
    def _check(cls, v: str) -> str:
        """Validate only — the caller's ORIGINAL string is passed through.

        record_decision() normalizes it and keeps the raw value in the stored
        decision record; normalizing here instead would record the slug as the
        raw value and the audit trail would lose what was actually sent.
        """
        normalize_decision(v)  # raises -> 422
        return v
