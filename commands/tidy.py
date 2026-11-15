"""Clear Google data older than 30 days from the candidate cache (run daily by ops/hometown-tidy)."""
import db
import google
import settings

NAME = "tidy"
HELP = "clear cached Google fields on candidates fetched 30 or more days ago"


def add_arguments(p):
    pass


def run(args) -> int:
    con = db.connect(settings.DB_PATH)
    db.migrate(con)
    n = google.tidy(con)
    print(f"Cleared Google data from {n} candidate{'s' if n != 1 else ''}.")
    return 0
