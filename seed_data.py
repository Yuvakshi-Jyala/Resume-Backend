"""
One-time seed script: load the dummy applicant records into MongoDB.

Run once after setting MONGODB_URI in backend/.env:

    python seed_data.py

It wipes the applicants collection, inserts the 44 dummy candidates parsed
from kpi_dashboard_data_KB_1.txt, then runs recalc so dashboard_stats is
populated and the KPI endpoint works immediately.

The KB file path defaults to ./kpi_dashboard_data_KB_1.txt — put the file next
to this script, or set KB_FILE to point at it.
"""
import asyncio
import os
import re

from dotenv import load_dotenv

load_dotenv()

from db import applicants
from stats import recalc_stats

KB_FILE = os.getenv("KB_FILE", "kpi_dashboard_data_KB_1.txt")


def parse_kb(path: str) -> list[dict]:
    """Parse APPLICANT lines from the flat-text KB into applicant dicts."""
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("APPLICANT"):
                continue
            parts = [p.strip() for p in line.split("|")]
            # parts: APPLICANT | id | name | role: X | status: Y | [interview ...] | [score: N]
            rec = {
                "applicant_id": parts[1],
                "name": parts[2],
                "role": parts[3].replace("role:", "").strip(),
                "status": parts[4].replace("status:", "").strip(),
                "interview_date": None,
                "interview_time": None,
                "score": None,
            }
            for extra in parts[5:]:
                if extra.startswith("interview"):
                    m = re.search(r"(\d{4}-\d{2}-\d{2})\s+at\s+(.+)", extra)
                    if m:
                        rec["interview_date"] = m.group(1)
                        rec["interview_time"] = m.group(2).strip()
                elif extra.startswith("score:"):
                    try:
                        rec["score"] = float(extra.replace("score:", "").strip())
                    except ValueError:
                        pass
            records.append(rec)
    return records


async def main():
    records = parse_kb(KB_FILE)
    print(f"Parsed {len(records)} applicants from {KB_FILE}")

    await applicants.delete_many({})           # fresh start
    if records:
        await applicants.insert_many(records)
    print(f"Inserted {len(records)} applicants into MongoDB")

    stats = await recalc_stats()
    print("Recalculated dashboard_stats:")
    for r in stats["roles"]:
        print(f"  {r['role']:<28} recv={r['applications_received']:<3} "
              f"cap={r['cap']:<3} short={r['shortlisted']:<3} "
              f"sched={r['calls_scheduled']:<3} compl={r['calls_completed']}")
    print(f"  upcoming interviews: {len(stats['interviews'])}")


if __name__ == "__main__":
    asyncio.run(main())