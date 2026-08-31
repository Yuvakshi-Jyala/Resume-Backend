import json
import os
import re
from dotenv import load_dotenv

load_dotenv()  # read backend/.env before anything reads os.getenv
from stats import get_stats, recalc_stats
from fastapi import FastAPI, File, UploadFile, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from pydantic import BaseModel, EmailStr

from passlib.context import CryptContext
from jose import jwt
from datetime import datetime, timedelta, timezone

from db import applicants, processed_emails, users
import cogitx


# build the origin list
_default_origins = (
    "http://localhost:5173,"
    "http://127.0.0.1:5173,"
    "http://localhost:3000,"
    "https://cogitx-compass-frontend-dsavamhtead3fqg8.canadacentral-01.azurewebsites.net"
)

ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("FRONTEND_ORIGINS", _default_origins).split(",")
    if o.strip()
]

# CREATE THE APP FIRST
app = FastAPI(title="Resume Screening UI backend")

# THEN add middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# AUTH CONFIGURATION
# =========================

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
)

security = HTTPBearer()
# The KPI agent emits a markdown table + a ```chart block. The table is the
# richer source (all 5 columns), so parse it first and fall back to the chart.

# Column header -> the Dashboard's expected field name.
_COL_MAP = {
    "received": "applications_received",
    "cap": "cap",
    "shortlisted": "shortlisted",
    "calls scheduled": "calls_scheduled",
    "calls completed": "calls_completed",
}
def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_access_token(data: dict) -> str:
    payload = data.copy()

    expire = datetime.now(timezone.utc) + timedelta(
        minutes=ACCESS_TOKEN_EXPIRE_MINUTES
    )

    payload.update({"exp": expire})

    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def _norm(h: str) -> str:
    return h.strip().lower()


def parse_role_breakdown(text: str):
    """Parse the 'Quick View' markdown table into role objects the UI expects.
    Falls back to the ```chart block if no table is found."""
    roles = _parse_markdown_table(text)
    if roles:
        return roles
    return _parse_chart_block(text) or []


def _parse_markdown_table(text: str):
    lines = [ln for ln in text.splitlines() if ln.strip().startswith("|")]
    if len(lines) < 2:
        return None

    def cells(row):
        return [c.strip() for c in row.strip().strip("|").split("|")]

    header = [_norm(c) for c in cells(lines[0])]
    # lines[1] is the |---|---| separator; data starts at lines[2].
    out = []
    for row in lines[2:]:
        vals = cells(row)
        if len(vals) != len(header):
            continue
        rec = {"role": vals[0]}
        for h, v in zip(header[1:], vals[1:]):
            field = _COL_MAP.get(h)
            if not field:
                continue
            try:
                rec[field] = int(v)
            except ValueError:
                rec[field] = 0
        # ensure all numeric fields exist so the frontend never gets undefined
        for field in ("applications_received", "shortlisted", "calls_scheduled",
                      "calls_completed", "cap"):
            rec.setdefault(field, 0)
        out.append(rec)
    return out or None


def _parse_chart_block(text: str):
    """Fallback: read the fenced ```chart {...} ``` JSON block."""
    m = re.search(r"```chart\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not m:
        return None
    try:
        chart = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    out = []
    for d in chart.get("data", []):
        out.append({
            "role": d.get("role", ""),
            "applications_received": d.get("applications_received", 0),
            "shortlisted": d.get("shortlisted", 0),
            "calls_scheduled": 0,
            "calls_completed": 0,
            "cap": 0,
        })
    return out or None


def parse_interviews(text: str):
    """Pull '- {date} at {time} — {name} ({role})' lines from the summary."""
    pattern = re.compile(
        r"[-*]\s*(\d{4}-\d{2}-\d{2})\s+at\s+([\d: ]+[APap][Mm])\s*[—-]+\s*(.+?)\s*\((.+?)\)"
    )
    return [
        {"date": d, "time": t.strip(), "name": n.strip(), "role": r.strip()}
        for d, t, n, r in pattern.findall(text)
    ]

def parse_interviews(text: str):
    """Pull '- {date} at {time} — {name} ({role})' lines from the summary."""
    pattern = re.compile(
        r"[-*]\s*(\d{4}-\d{2}-\d{2})\s+at\s+([\d: ]+[APap][Mm])\s*[—-]+\s*(.+?)\s*\((.+?)\)"
    )
    return [
        {"date": d, "time": t.strip(), "name": n.strip(), "role": r.strip()}
        for d, t, n, r in pattern.findall(text)
    ]



_CANDIDATE_LINE_RE = re.compile(
    r"^-\s*(?P<role>[^|]+)\|\s*(?P<name>[^|]+)\|\s*(?P<status>[^|]+?)"
    r"(?:\s*\|\s*(?P<idate>\d{4}-\d{2}-\d{2})\s+at\s+(?P<itime>[^|]+?))?"
    r"(?:\s*\|\s*score:\s*(?P<score>[\d.]+))?\s*$"
)


def parse_all_candidates(text: str):
    m = re.search(r"###\s*All Candidates\s*\n(.*?)(?=\n###|\Z)", text, re.DOTALL)
    if not m:
        return {}
    by_role: dict[str, list] = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line:
            continue
        cm = _CANDIDATE_LINE_RE.match(line)
        if not cm:
            continue
        role = cm.group("role").strip()
        score_raw = cm.group("score")
        by_role.setdefault(role, []).append({
            "name": cm.group("name").strip(),
            "status": cm.group("status").strip(),
            "interview_date": cm.group("idate"),
            "interview_time": (cm.group("itime") or "").strip() or None,
            "score": float(score_raw) if score_raw else None,
        })
    return by_role

# Render deployment test
@app.get("/health")
async def health():
    return {"status": "ok"}

# ----------------------------
# Authentication request models
# ----------------------------

class SignupRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
class LoginRequest(BaseModel):
    email: EmailStr
    password: str
# ----------------------------
# Authentication APIs
# ----------------------------

@app.post("/api/auth/signup")
async def signup(user: SignupRequest):
    # Check if email already exists
    existing_user = await users.find_one({"email": user.email})

    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="Email already registered."
        )

    # Create new user document
    new_user = {
        "name": user.name,
        "email": user.email,
        "password_hash": hash_password(user.password),
        "created_at": datetime.now(timezone.utc),
    }

    result = await users.insert_one(new_user)

    # Create JWT token
    access_token = create_access_token({
        "user_id": str(result.inserted_id),
        "email": user.email,
    })

    return {
        "message": "Signup successful.",
        "access_token": access_token,
        "user": {
            "id": str(result.inserted_id),
            "name": user.name,
            "email": user.email,
        },
    }

@app.post("/api/auth/login")
async def login(credentials: LoginRequest):
    # Find user by email
    user = await users.find_one({"email": credentials.email})

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password."
        )

    # Verify password
    if not verify_password(credentials.password, user["password_hash"]):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password."
        )

    # Generate new JWT
    access_token = create_access_token({
        "user_id": str(user["_id"]),
        "email": user["email"],
    })

    return {
        "message": "Login successful.",
        "access_token": access_token,
        "user": {
            "id": str(user["_id"]),
            "name": user["name"],
            "email": user["email"],
        },
    }

@app.get("/api/kpi")
async def kpi(refresh: bool = False):
    # Normal loads read the cached summary. refresh=1 (the Refresh button) forces
    # a rebuild from the live applicants collection, so manual DB edits show up.
    if refresh:
        stats = await recalc_stats()
    else:
        stats = await get_stats()
        if not stats:
            stats = await get_stats()
    return {
        "roles": stats.get("roles", []),
        "interviews": stats.get("interviews", []),
        "candidates_by_role": stats.get("candidates_by_role", {}),
    }


async def _persist_card(card: dict):
    """Upsert one scored candidate into the applicants collection.

    Dedupe on the candidate's email when available (stable across re-scores),
    falling back to case-insensitive name+role. This prevents duplicate rows
    when the scorer returns the same person's name in different casing.
    """
    name = card.get("name")
    if not name:
        return
    role = card.get("role", "")
    email = (card.get("email") or "").strip().lower()

    applicant = {
        "name": name,
        "role": role,
        "status": "Shortlisted" if (card.get("fit_score") or 0) >= 65 else "Applied",
        "score": card.get("fit_score"),
        # Full analyzer card so the dashboard can show the same detail view
        # (summary, matched/gaps, scores, verdict, interview questions).
        "analysis": card,
        "email_key": email or None,
    }

    # Match on a stable key: email if we have one, else name (case-insensitive) + role.
    if email:
        match = {"email_key": email}
    else:
        match = {
            "name": {"$regex": f"^{re.escape(name)}$", "$options": "i"},
            "role": role,
        }

    # $set only the fields above so we never wipe an existing interview_date /
    # interview_time on a re-screen. $setOnInsert seeds them to None for brand
    # new candidates so the shape stays consistent.
    await applicants.update_one(
        match,
        {
            "$set": applicant,
            "$setOnInsert": {
                "interview_date": None,
                "interview_time": None,

                # New applicant notification
                "is_new": True,

                # Store the Outlook email ID only once
                "message_id": card.get("message_id"),
            },
        },
        upsert=True,
    )


@app.post("/api/screen")
async def screen(files: list[UploadFile] = File(...)):
    blobs = [
        (f.filename, await f.read(), f.content_type)
        for f in files[:5]
    ]

    result = cogitx.run_screening(blobs)

    for card in result.get("cards", []):
        await _persist_card(card)

    await recalc_stats()
    return result



@app.post("/api/ingest")
async def ingest():
    """Auto-ingest: read new application emails, score them, and save. Meant to be
    called on a schedule (e.g. a Render Cron Job every ~15 min). Safe no-op until
    COGITX_INGEST_EXPORT_ID is set — returns ingested: 0 and touches nothing.
    """
    # Emails we've already ingested — skip them so the same applications aren't
    # re-scored (and duplicated) on every fetch.
    seen = {d["_id"] async for d in processed_emails.find({}, {"_id": 1})}

    try:
        # run_ingest does blocking network I/O (downloads + scoring), so run it
        # in a threadpool to avoid blocking the event loop.
        result = await run_in_threadpool(cogitx.run_ingest, seen)
    except Exception as e:
        print(f"[ingest] run_ingest failed: {e}")
        return {"ok": False, "ingested": 0, "error": str(e)}

    cards = result.get("cards", [])
    for card in cards:
        await _persist_card(card)

    # Mark the emails we scored this run as processed so we don't touch them again.
    for eid in result.get("processed_ids", []):
        await processed_emails.update_one(
            {"_id": eid}, {"$set": {"_id": eid}}, upsert=True
        )

    if cards:
        await recalc_stats()
    return {"ok": True, "ingested": len(cards), "files": result.get("ingested_files", 0)}

from bson import ObjectId

@app.get("/api/applicants")
async def get_applicants():
    result = []

    async for applicant in applicants.find():
        analysis = applicant.get("analysis", {})
        score = analysis.get("fit_score")

        if score is None:
            score = applicant.get("score")

        score = float(score or 0)
        years = analysis.get("experience_years")
        years = analysis.get("experience_years")

        if years is None:
            exp = "—"                      # Unknown / old seeded data
        elif years < 1:
            exp = "Fresher"                # 0–1 year experience
        elif years == int(years):
            exp = f"{int(years)} yr" if years == 1 else f"{int(years)} yrs"
        else:
            exp = f"{years:.1f} yrs"


        if score >= 90:
            recommendation = "Fast-track"
            skills = "Strong"
        elif score >= 75:
            recommendation = "Interview"
            skills = "Strong"
        elif score >= 60:
            recommendation = "Review"
            skills = "Moderate"
        else:
            recommendation = "Reject"
            skills = "Weak"

        result.append({
            "id": str(applicant["_id"]),
            "name": applicant.get("name"),
            "role": applicant.get("role"),
            "match": round(score, 1),
            "experience": exp,
            "skills": skills,
            "recommendation": recommendation,
            "status": applicant.get("status"),
        })

    result.sort(key=lambda x: x["match"], reverse=True)
    return result


from bson import ObjectId
from fastapi import HTTPException

@app.get("/api/applicants/{applicant_id}")
async def get_applicant(applicant_id: str):
    applicant = await applicants.find_one({"_id": ObjectId(applicant_id)})

    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")

    analysis = applicant.get("analysis", {})

    years = analysis.get("experience_years")
    if years is None:
        experience = "Fresher"
    elif years < 1:
        experience = "Fresher"
    elif years == int(years):
        experience = f"{int(years)} yr" if years == 1 else f"{int(years)} yrs"
    else:
        experience = f"{years:.1f} yrs"

    return {
        "id": str(applicant["_id"]),

        "name": analysis.get("name"),
        "role": analysis.get("role"),
        "email": analysis.get("email"),
        "phone": analysis.get("phone"),

        "fit_score": analysis.get("fit_score"),
        "band": analysis.get("band"),

        "experience": experience,
        "experience_years": years,

        "summary": analysis.get("summary"),
        "verdict": analysis.get("verdict"),

        "matched_skills": analysis.get("matched_skills", []),
        "missing_skills": analysis.get("missing_skills", []),

        "technical_questions": analysis.get("interview_questions", {}).get("technical", []),
        "behavioral_questions": analysis.get("interview_questions", {}).get("behavioral", [])
    }

@app.post("/api/roles/{role}/mark-seen")
async def mark_role_seen(role: str):
    """Mark all new applicants in a role as seen."""
    await applicants.update_many(
        {"role": role, "is_new": True},
        {"$set": {"is_new": False}},
    )

    await recalc_stats()

    return {"ok": True}


class Decision(BaseModel):
    name: str
    role: str
    decision: str  # "Shortlisted" (accept) or "Rejected"


@app.post("/api/decision")
async def decision(body: Decision):
    """Record a recruiter's accept/reject decision and send the matching email.

    The email goes out via the CogitX email workflow (cogitx.send_decision_email).
    That is a safe no-op until COGITX_EMAIL_EXPORT_ID is set in the environment —
    while unset, the decision is still recorded, email_sent stays False, and
    nothing is sent. The send is also wrapped in try/except so a failure never
    breaks the endpoint; the decision is saved regardless.
    """
    print(f"[decision] received: {body.name} | {body.role} | {body.decision}")
    print(f"[decision] email export configured: {bool(cogitx.EMAIL_EXPORT_ID)}")

    new_status = "Shortlisted" if body.decision == "Shortlisted" else "Rejected"
    await applicants.update_one(
        {"name": body.name, "role": body.role},
        {
            "$set": {
                "status": new_status,
                "decision": body.decision,
                "email_sent": False,
            }
        },
        upsert=True,
    )

    # Send the shortlist/rejection email. The candidate's email address comes from
    # the stored analyzer card; seeded candidates without analysis have no email,
    # so we simply skip sending for them.
    email_sent = False
    doc = await applicants.find_one({"name": body.name, "role": body.role})
    email = (doc.get("analysis") or {}).get("email") if doc else None
    if not doc:
        print(f"[decision] no applicant doc found for {body.name} / {body.role}")
    elif not email:
        print(f"[decision] no email on {body.name} (seeded/unscored?) — skipping send")
    if email:
        print(f"[decision] sending {body.decision} email to {email} …")
        try:
            email_sent = cogitx.send_decision_email(
                body.name, email, body.role, body.decision
            )
            print(f"[decision] send_decision_email returned: {email_sent}")
        except Exception as e:  # never let an email failure break the decision
            print(f"[decision] email send FAILED for {body.name}: {e!r}")
            email_sent = False
        if email_sent:
            await applicants.update_one(
                {"name": body.name, "role": body.role},
                {"$set": {"email_sent": True}},
            )
            print(f"[decision] email_sent=True saved for {body.name}")

    await recalc_stats()
    return {
        "ok": True,
        "name": body.name,
        "role": body.role,
        "status": new_status,
        "email_sent": email_sent,
    }
