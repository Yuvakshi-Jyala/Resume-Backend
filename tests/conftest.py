"""Shared fixtures.

No live Mongo: AsyncIOMotorClient construction in app/db/mongo.py is lazy, so
importing the services is safe, and FakeCollection stands in for the real
collection. A hand-rolled fake rather than mongomock because the surface used
is tiny and the backfill's concurrency tests need exact control over
modified_count.
"""
import re

import pytest

from app.domain.statuses import APPLIED


class _Result:
    def __init__(self, modified_count=0, matched_count=0):
        self.modified_count = modified_count
        self.matched_count = matched_count


def _get(doc, path):
    """Read a possibly-dotted field path out of a document."""
    cur = doc
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _matches(doc, flt) -> bool:
    for key, cond in flt.items():
        value = _get(doc, key)
        if isinstance(cond, dict):
            for op, operand in cond.items():
                if op == "$exists" and (value is not None) != operand:
                    return False
                if op == "$ne" and value == operand:
                    return False
                if op == "$in" and value not in operand:
                    return False
                if op == "$regex":
                    flags = re.I if "i" in cond.get("$options", "") else 0
                    if not isinstance(value, str) or not re.search(operand, value, flags):
                        return False
        elif value != cond:
            return False
    return True


class FakeCollection:
    """The slice of the Motor collection API these services actually use."""

    def __init__(self, docs=None):
        self.docs = [dict(d) for d in (docs or [])]
        # Set to a doc id to simulate that row changing underneath a scan.
        self.fail_updates_for = set()

    def find(self, flt=None):
        flt = flt or {}
        matched = [d for d in self.docs if _matches(d, flt)]

        async def _gen():
            for d in matched:
                yield d

        return _gen()

    async def find_one(self, flt):
        for d in self.docs:
            if _matches(d, flt):
                return d
        return None

    async def update_one(self, flt, update, upsert=False):
        for d in self.docs:
            if _matches(d, flt):
                if d["_id"] in self.fail_updates_for:
                    return _Result(modified_count=0, matched_count=1)
                d.update(update.get("$set", {}))
                return _Result(modified_count=1, matched_count=1)
        if upsert:
            new = {**update.get("$set", {}), **update.get("$setOnInsert", {})}
            self.docs.append(new)
            return _Result(modified_count=0, matched_count=0)
        return _Result()

    async def update_many(self, flt, update):
        n = 0
        for d in self.docs:
            if _matches(d, flt):
                d.update(update.get("$set", {}))
                n += 1
        return _Result(modified_count=n, matched_count=n)

    async def replace_one(self, flt, doc, upsert=False):
        for i, existing in enumerate(self.docs):
            if _matches(existing, flt):
                self.docs[i] = dict(doc)
                return _Result(modified_count=1, matched_count=1)
        if upsert:
            self.docs.append(dict(doc))
        return _Result()

    async def delete_many(self, flt):
        keep = [d for d in self.docs if not _matches(d, flt)]
        removed = len(self.docs) - len(keep)
        self.docs[:] = keep
        return _Result(modified_count=removed, matched_count=removed)


@pytest.fixture
def fake_applicants(monkeypatch):
    """Patch the applicants collection into both modules that bind it.

    app.services.applicants and app.services.stats each import the collection
    at module load, so patching app.db.mongo alone would not take effect.
    """
    collection = FakeCollection()

    def _install(docs):
        collection.docs = [dict(d) for d in docs]
        return collection

    collection.load = _install

    import app.services.applicants as applicants_service
    import app.services.stats as stats_service

    monkeypatch.setattr(applicants_service, "applicants", collection)
    monkeypatch.setattr(stats_service, "applicants", collection)

    async def _noop_recalc():
        return {}

    monkeypatch.setattr(applicants_service, "recalc_stats", _noop_recalc)
    return collection


@pytest.fixture
def fake_stats(monkeypatch):
    """Patch the dashboard_stats collection (bound at import, like applicants).

    Starts EMPTY, which is the cold-cache state /api/kpi has to survive.
    """
    collection = FakeCollection()
    import app.services.stats as stats_service

    monkeypatch.setattr(stats_service, "dashboard_stats", collection)
    return collection


def applicant(_id, **overrides):
    """A minimal applicant document with sane defaults."""
    doc = {
        "_id": _id,
        "name": f"Candidate {_id}",
        "role": "AI/ML Engineer",
        "status": APPLIED,
        "score": 80,
        "analysis": {"name": f"Candidate {_id}", "role": "AI/ML Engineer",
                     "email": f"{_id}@example.com", "fit_score": 80},
    }
    doc.update(overrides)
    return doc
