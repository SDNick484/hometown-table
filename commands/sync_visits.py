"""Pull the visits recorded in Dinner Log and Where to Eat and log them here (Task 15). ops/hometown-sync runs it
daily."""
import sys

import db
import settings
import visit_sync

NAME = "sync-visits"
HELP = "log the restaurant visits recorded in Dinner Log and Where to Eat (daily cron job)"


def add_arguments(p):
    pass


def run(args) -> int:
    if not any(visit_sync.configured(s) for s in visit_sync.SOURCES):
        print("Visit sync is off: set DINNER_LOG_URL and DINNER_LOG_TOKEN, or WTE_URL and WTE_TOKEN.")
        return 0
    con = db.connect(settings.DB_PATH)
    try:
        db.migrate(con)
        try:
            result = visit_sync.run(con)
        except Exception as e:
            print(f"Visit sync failed: {e}", file=sys.stderr)
            return 1
    finally:
        con.close()
    for source, r in result.items():
        label = visit_sync.LABELS[source]
        if r["status"] == "no_people":
            print(f"{label}: nothing synced. No one is chosen for synced visits: pick them in Settings.")
        elif r["status"] == "ok":
            print(f"{label}: {r['new']} new, {r['logged']} logged, {r['duplicate']} already logged, "
                  f"{r['unmatched']} unmatched.")
    return 0
