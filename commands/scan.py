"""Run the discovery scan now (spec §8.2). ops/hometown-scan runs it weekly."""
import sys

import db
import google
import scan
import settings

NAME = "scan"
HELP = "search Google for new places in the towns, sort them into the inbox and check closures (weekly cron job)"


def add_arguments(p):
    pass


def run(args) -> int:
    if not google.enabled():
        print("GOOGLE_PLACES_KEY is not set: the scan is off.")
        return 0
    con = db.connect(settings.DB_PATH)
    try:
        db.migrate(con)
        try:
            row = scan.run(con)
        except Exception as e:
            print(f"Scan failed: {e}", file=sys.stderr)
            return 1
        waiting = con.execute("SELECT COUNT(*) FROM candidate WHERE status = 'pending'").fetchone()[0]
    finally:
        con.close()
    n = row["new_candidates"]
    line = (f"Scan done: {row['tiles']} tiles, {row['search_calls']} searches, {row['detail_calls']} Google lookups, "
            f"{n} new candidate{'s' if n != 1 else ''}")
    if waiting:
        line += f", {waiting} waiting for next month's Google lookups"
    print(line + ".")
    return 0
