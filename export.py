"""Exports (spec §12): everything as JSON, and one CSV row per visit person. Reads only."""
import csv
import io

import common
import db

# Every table except meta (the database's own bookkeeping), in dependency order.
TABLES = ("person", "tag", "place", "place_tag", "place_event", "visit", "visit_person", "source_list", "list_entry",
          "candidate", "scan_run", "google_usage", "sync_seen")
CSV_HEADER = ("place", "town", "ownership", "visited_on", "occasion", "person", "rating", "note")


def to_json(con) -> dict:
    return {"exported_at": common.now(), "schema_version": db.SCHEMA_VERSION,
            "tables": {t: [dict(r) for r in con.execute(f"SELECT * FROM {t} ORDER BY rowid")] for t in TABLES}}


def _cell(value) -> str:
    """Text a spreadsheet can't run as a formula: a leading = + - @ (or tab/CR) gets a quote in front."""
    value = "" if value is None else str(value)
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def to_csv(con) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_HEADER)
    rows = con.execute(
        "SELECT p.name AS place, p.town, p.ownership, v.visited_on, v.occasion, pe.name AS person, vp.rating, vp.note "
        "FROM visit_person vp JOIN visit v ON v.id = vp.visit_id JOIN place p ON p.id = v.place_id "
        "JOIN person pe ON pe.id = vp.person_id ORDER BY v.visited_on, v.id, pe.sort_order, pe.id")
    for r in rows:
        writer.writerow([_cell(r["place"]), _cell(r["town"]), r["ownership"], r["visited_on"], _cell(r["occasion"]),
                         _cell(r["person"]), "" if r["rating"] is None else r["rating"], _cell(r["note"])])
    return out.getvalue()
