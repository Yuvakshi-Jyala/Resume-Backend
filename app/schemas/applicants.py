from pydantic import BaseModel


class Decision(BaseModel):
    id: str
    decision: str  # "Shortlisted" (accept) or "Rejected"
