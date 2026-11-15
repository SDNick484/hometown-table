"""The SQLite schema, connections, transactions and migrate().

Connections run in autocommit mode (isolation_level=None): a single statement commits on its own, and anything that
must be atomic runs inside `with transaction(con):`. Every schema change adds a step to MIGRATIONS and bumps
SCHEMA_VERSION (kept in PRAGMA user_version); migrate() is idempotent and runs at every start, so the host never needs
a manual database step. Task 1 created the whole v1 schema; v2 (Task 15, approved by the owner) adds the visit-sync
ledger. A fresh database runs every step in order: v1, then v2.

Timestamps are integer epoch seconds; dates are YYYY-MM-DD in America/Los_Angeles (common.today()). Connections
carry the SQL function fold(text), common.fold: `WHERE fold(name) LIKE ?` is accent- and case-insensitive."""
import sqlite3
import time
from contextlib import contextmanager

import common

SCHEMA_VERSION = 2

# Version 1. A deployed database is created from this text: never edit it, add a step to MIGRATIONS instead.
# Statements are separated by ";" and contain none themselves: see _statements().
#
# Beyond the plan's column list, two columns serve spec rules the listed ones can't express:
#   place.want_since         when want_to_try was last turned on (epoch seconds): "want" mode is oldest starred first
#   place.closure_dismissed  the Google status an editor dismissed: the flag stays off until the status changes
V1 = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE place (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL CHECK (name <> ''),
  town TEXT,
  address TEXT,
  lat REAL,
  lng REAL,
  cuisine TEXT,
  phone TEXT,
  website TEXT,
  hours TEXT,
  want_to_try INTEGER NOT NULL DEFAULT 0 CHECK (want_to_try IN (0, 1)),
  want_since INTEGER,
  notes TEXT,
  ownership TEXT NOT NULL CHECK (ownership IN ('counts', 'unsure', 'no')),
  ownership_reason TEXT NOT NULL,
  standing TEXT NOT NULL DEFAULT 'open' CHECK (standing IN ('open', 'temp_closed', 'closed')),
  standing_since TEXT,
  closure_flag TEXT CHECK (closure_flag IN ('CLOSED_PERMANENTLY', 'CLOSED_TEMPORARILY')),
  closure_dismissed TEXT CHECK (closure_dismissed IN ('CLOSED_PERMANENTLY', 'CLOSED_TEMPORARILY')),
  closure_checked_at INTEGER,
  google_place_id TEXT UNIQUE,
  osm_ref TEXT,
  wte_id INTEGER,
  origin TEXT NOT NULL DEFAULT 'hand' CHECK (origin IN ('hand', 'scan', 'list', 'where_to_eat')),
  first_seen_at INTEGER,
  added_by TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  version INTEGER NOT NULL DEFAULT 1);
CREATE INDEX place_eligible ON place (ownership, standing);
CREATE INDEX place_wte ON place (wte_id);

CREATE TABLE place_event (
  id INTEGER PRIMARY KEY,
  place_id INTEGER NOT NULL REFERENCES place (id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('ownership', 'standing')),
  old_value TEXT,
  new_value TEXT NOT NULL,
  reason TEXT,
  on_date TEXT NOT NULL,
  by TEXT,
  created_at INTEGER NOT NULL);
CREATE INDEX place_event_place ON place_event (place_id, created_at);

CREATE TABLE person (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL CHECK (name <> ''),
  grp TEXT NOT NULL CHECK (grp IN ('adult', 'kid')),
  email TEXT UNIQUE,
  sort_order INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)));

CREATE TABLE tag (
  id INTEGER PRIMARY KEY,
  label TEXT NOT NULL CHECK (label <> ''),
  occasion INTEGER NOT NULL DEFAULT 0 CHECK (occasion IN (0, 1)),
  sort_order INTEGER NOT NULL DEFAULT 0);

CREATE TABLE place_tag (
  place_id INTEGER NOT NULL REFERENCES place (id) ON DELETE CASCADE,
  tag_id INTEGER NOT NULL REFERENCES tag (id) ON DELETE CASCADE,
  PRIMARY KEY (place_id, tag_id));
CREATE INDEX place_tag_tag ON place_tag (tag_id);

CREATE TABLE visit (
  id INTEGER PRIMARY KEY,
  place_id INTEGER NOT NULL REFERENCES place (id),
  visited_on TEXT NOT NULL CHECK (visited_on GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
  occasion TEXT,
  note TEXT,
  added_by TEXT,
  created_at INTEGER NOT NULL,
  version INTEGER NOT NULL DEFAULT 1);
CREATE INDEX visit_place ON visit (place_id, visited_on);
CREATE INDEX visit_on ON visit (visited_on);

CREATE TABLE visit_person (
  visit_id INTEGER NOT NULL REFERENCES visit (id) ON DELETE CASCADE,
  person_id INTEGER NOT NULL REFERENCES person (id),
  rating INTEGER CHECK (rating IS NULL OR (typeof(rating) = 'integer' AND rating BETWEEN 1 AND 5)),
  note TEXT,
  PRIMARY KEY (visit_id, person_id));
CREATE INDEX visit_person_person ON visit_person (person_id);

CREATE TABLE source_list (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL CHECK (name <> ''),
  url TEXT,
  created_at INTEGER NOT NULL);

CREATE TABLE candidate (
  id INTEGER PRIMARY KEY,
  google_place_id TEXT UNIQUE,
  osm_ref TEXT,
  osm_name TEXT,
  osm_address TEXT,
  osm_lat REAL,
  osm_lng REAL,
  osm_extra TEXT,
  chain_hint INTEGER NOT NULL DEFAULT 0 CHECK (chain_hint IN (0, 1)),
  wte_id INTEGER,
  wte_json TEXT,
  g_name TEXT,
  g_address TEXT,
  g_lat REAL,
  g_lng REAL,
  g_status TEXT,
  g_type TEXT,
  g_fetched_at INTEGER,
  origin TEXT NOT NULL CHECK (origin IN ('scan', 'list', 'where_to_eat')),
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'new', 'backlog', 'dismissed', 'accepted', 'chain_hidden')),
  first_seen_at INTEGER NOT NULL,
  decided_at INTEGER,
  place_id INTEGER REFERENCES place (id) ON DELETE SET NULL);
CREATE INDEX candidate_status ON candidate (status, first_seen_at);
CREATE INDEX candidate_place ON candidate (place_id);
CREATE INDEX candidate_wte ON candidate (wte_id);
CREATE INDEX candidate_g_fetched ON candidate (g_fetched_at);

CREATE TABLE list_entry (
  id INTEGER PRIMARY KEY,
  list_id INTEGER NOT NULL REFERENCES source_list (id) ON DELETE CASCADE,
  line TEXT NOT NULL,
  candidate_id INTEGER REFERENCES candidate (id) ON DELETE SET NULL,
  place_id INTEGER REFERENCES place (id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK (status IN ('matched', 'unmatched')),
  created_at INTEGER NOT NULL);
CREATE INDEX list_entry_list ON list_entry (list_id);
CREATE INDEX list_entry_candidate ON list_entry (candidate_id);
CREATE INDEX list_entry_place ON list_entry (place_id);

CREATE TABLE scan_run (
  id INTEGER PRIMARY KEY,
  started_at INTEGER NOT NULL,
  finished_at INTEGER,
  tiles INTEGER NOT NULL DEFAULT 0,
  search_calls INTEGER NOT NULL DEFAULT 0,
  detail_calls INTEGER NOT NULL DEFAULT 0,
  new_candidates INTEGER NOT NULL DEFAULT 0,
  error TEXT);

CREATE TABLE google_usage (
  month TEXT PRIMARY KEY,
  pro_calls INTEGER NOT NULL DEFAULT 0,
  id_calls INTEGER NOT NULL DEFAULT 0)
"""


# Version 2 (Task 15): the ledger of feed entries visit_sync.py has seen, one row per (source, ref), which is what makes
# a synced visit "exactly once": a ref in it is never logged again, even after its visit is deleted here (visit_id
# goes NULL, the row stays). `extra` is JSON (Dinner Log: dish; Where to Eat: place_id, address, lat, lng). Every
# statement is IF NOT EXISTS, so the step is idempotent on its own as well as through user_version.
V2 = """
CREATE TABLE IF NOT EXISTS sync_seen (
  source TEXT NOT NULL CHECK (source IN ('dinner_log', 'where_to_eat')),
  ref TEXT NOT NULL,
  day TEXT,
  name TEXT,
  location TEXT,
  extra TEXT,
  status TEXT NOT NULL CHECK (status IN ('logged', 'duplicate', 'unmatched', 'dismissed')),
  place_id INTEGER REFERENCES place (id) ON DELETE SET NULL,
  visit_id INTEGER REFERENCES visit (id) ON DELETE SET NULL,
  seen_at INTEGER,
  PRIMARY KEY (source, ref));
CREATE INDEX IF NOT EXISTS sync_seen_status ON sync_seen (status, source);
CREATE INDEX IF NOT EXISTS sync_seen_visit ON sync_seen (visit_id);
CREATE INDEX IF NOT EXISTS sync_seen_place ON sync_seen (place_id)
"""


def _statements(script):
    # Not executescript(): it commits any open transaction first, which would split a migration in two.
    return [s.strip() for s in script.split(";") if s.strip()]


def connect(path) -> sqlite3.Connection:
    """A connection in autocommit mode with sqlite3.Row rows, foreign keys on, WAL, a 10 s busy wait, and fold()."""
    con = sqlite3.connect(path, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.create_function("fold", 1, common.fold, deterministic=True)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 10000")
    _use_wal(con)
    return con


def _use_wal(con, wait=10.0):
    """Switch to WAL unless the file already is. Switching takes an exclusive lock, and SQLite answers "database is
    locked" at once instead of calling the busy handler, so two workers opening a new file at the same moment would
    fail: retry within the same 10 s the busy timeout allows."""
    deadline = time.monotonic() + wait
    while True:
        try:
            if con.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                con.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


@contextmanager
def transaction(con):
    """BEGIN IMMEDIATE ... COMMIT, or ROLLBACK if the block raises. Not reentrant: don't nest them."""
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")


def _to_v1(con):
    for stmt in _statements(V1):
        con.execute(stmt)
    con.execute("INSERT INTO meta (key, value) VALUES ('created_at', ?)", (str(common.now()),))


def _to_v2(con):
    for stmt in _statements(V2):
        con.execute(stmt)


MIGRATIONS = {1: _to_v1, 2: _to_v2}  # version -> the step that brings version-1 up to it


def _version(con):
    return con.execute("PRAGMA user_version").fetchone()[0]


def migrate(con):
    """Bring the database up to SCHEMA_VERSION, one atomic step at a time. Safe to run at every start, and from two
    gunicorn workers at once: each step re-reads the version inside its own write transaction."""
    if _version(con) > SCHEMA_VERSION:
        raise RuntimeError(f"the database is version {_version(con)}, newer than this code ({SCHEMA_VERSION})")
    if _version(con) == SCHEMA_VERSION:
        return
    for target in range(1, SCHEMA_VERSION + 1):
        with transaction(con):
            if _version(con) >= target:
                continue
            MIGRATIONS[target](con)
            con.execute(f"PRAGMA user_version = {int(target)}")
