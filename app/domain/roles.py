"""Canonical role vocabulary — the single source of truth for the 6 roles.

Previously this list existed in three places with three different shapes:
ROLE_CAPS (db.py), _ROLE_ORDER (stats.py), _ROLE_ALIASES (cogitx.py). They're
all derived from ROLE_ORDER here now.
"""

# Stable display order for roles on the dashboard.
ROLE_ORDER = [
    "AI/ML Engineer",
    "Solution Architect",
    "Product Manager",
    "Software Development Engineer",
    "Forward Deployed Engineer",
    "Internship",
]

# Role application caps.
ROLE_CAPS = {role: 30 for role in ROLE_ORDER}

_ROLE_ALIASES = {
    "ai/ml engineer": "AI/ML Engineer",
    "aiml engineer": "AI/ML Engineer",
    "machine learning engineer": "AI/ML Engineer",
    "ml engineer": "AI/ML Engineer",
    "agent architect": "AI/ML Engineer",
    "software development engineer": "Software Development Engineer",
    "sde": "Software Development Engineer",
    "software engineer": "Software Development Engineer",
    "forward deployed engineer": "Forward Deployed Engineer",
    "fde": "Forward Deployed Engineer",
    "solution architect": "Solution Architect",
    "product manager": "Product Manager",
    "ai product manager": "Product Manager",
    "internship": "Internship",
    "intern": "Internship",
}

# Short role labels used only in the /api/kpi response. Stored data keeps the
# full role name; this is display-only so the dashboard columns stay narrow.
ROLE_DISPLAY_NAMES = {
    "Software Development Engineer": "SDE",
    "Forward Deployed Engineer": "FDE",
}


def normalize_role(role):
    """Map role-name variants to the 6 canonical roles. Drops any trailing
    parenthetical (e.g. "AI/ML Engineer (Agent Architect)" -> "AI/ML Engineer").
    Unknown values are returned trimmed but unchanged."""
    if not role or not isinstance(role, str):
        return role
    base = role.split("(")[0]  # drop "(Agent Architect)" etc.
    key = " ".join(base.strip().lower().split())
    return _ROLE_ALIASES.get(key, role.strip())


def display_role(role: str) -> str:
    return ROLE_DISPLAY_NAMES.get(role, role)


def abbrev_roles(items: list) -> list:
    return [
        {**it, "role": display_role(it.get("role", ""))} if isinstance(it, dict) else it
        for it in items
    ]


def role_sort_key(role: str) -> tuple:
    return (ROLE_ORDER.index(role) if role in ROLE_ORDER else len(ROLE_ORDER), role)
