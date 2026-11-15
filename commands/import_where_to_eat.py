"""`manage.py import-where-to-eat <export.json>`: open places in the towns become inbox candidates (spec §9)."""
import json
import sys

import db
import settings
import wte_import

NAME = "import-where-to-eat"
HELP = "Import Where to Eat's export.json (More > Download > JSON) into the inbox; safe to run twice"


def add_arguments(p):
    p.add_argument("path", help="the export file")


def run(args) -> int:
    try:
        with open(args.path, encoding="utf-8") as f:
            export = json.load(f)
    except (OSError, ValueError) as e:
        print(f"Can't read {args.path}: {e}", file=sys.stderr)
        return 1
    con = db.connect(settings.DB_PATH)
    try:
        db.migrate(con)
        counts = wte_import.run(con, export)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    finally:
        con.close()
    for key, n in counts.items():
        print(f"{key}: {n}")
    return 0
