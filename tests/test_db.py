import sqlite3
import threading

import pytest

import db
import settings
from conftest import mk_person, mk_place, mk_visit

TABLES = {"place", "place_event", "person", "tag", "place_tag", "visit", "visit_person", "source_list", "list_entry",
          "candidate", "scan_run", "google_usage", "meta", "sync_seen"}


def schema_text(con):
    return [tuple(r) for r in con.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name")]


def test_fresh_db_is_v2_with_every_table(con):
    assert con.execute("PRAGMA user_version").fetchone()[0] == 2
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names


def test_migrate_twice_is_a_noop(con):
    before = schema_text(con)
    db.migrate(con)
    db.migrate(con)
    assert schema_text(con) == before
    assert con.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 2


def test_rating_outside_1_to_5_is_refused(con):
    pid, a = mk_place(con), mk_person(con, "Alex")
    vid = mk_visit(con, pid, "2026-11-01", {})
    for bad in (0, 6, 4.5):
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO visit_person (visit_id, person_id, rating) VALUES (?, ?, ?)", (vid, a, bad))
    con.execute("INSERT INTO visit_person (visit_id, person_id, rating) VALUES (?, ?, NULL)", (vid, a))  # no rating is fine


def test_connection_settings(con):
    assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert con.execute("SELECT fold('Phở Ejemplo')").fetchone()[0] == "pho ejemplo"
    assert isinstance(con.execute("SELECT 1 AS one").fetchone(), sqlite3.Row)


def test_checks_hold_the_enumerations(con):
    for bad in ({"ownership": "maybe"}, {"standing": "gone"}, {"origin": "magic"}, {"closure_flag": "OPERATIONAL"}):
        with pytest.raises(sqlite3.IntegrityError):
            mk_place(con, **bad)
    with pytest.raises(sqlite3.IntegrityError):
        mk_person(con, "Riley", grp="teen")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO candidate (origin, status, first_seen_at) VALUES ('scan', 'maybe', 0)")


def test_place_with_visits_cannot_be_deleted_but_its_events_and_tags_cascade(con):
    pid = mk_place(con)
    con.execute("INSERT INTO place_event (place_id, kind, old_value, new_value, reason, on_date, by, created_at) "
                "VALUES (?, 'ownership', 'counts', 'no', 'sold', '2026-11-14', 'lan', 0)", (pid,))
    mk_visit(con, pid, "2026-11-01", {mk_person(con, "Alex"): 5})
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM place WHERE id = ?", (pid,))
    other = mk_place(con, name="Pho Example")
    con.execute("INSERT INTO place_event (place_id, kind, new_value, on_date, created_at) "
                "VALUES (?, 'standing', 'closed', '2026-11-14', 0)", (other,))
    con.execute("DELETE FROM place WHERE id = ?", (other,))
    assert con.execute("SELECT COUNT(*) FROM place_event WHERE place_id = ?", (other,)).fetchone()[0] == 0


def test_transaction_rolls_back_on_error(con):
    with pytest.raises(RuntimeError):
        with db.transaction(con):
            mk_place(con)
            raise RuntimeError("stop")
    assert con.execute("SELECT COUNT(*) FROM place").fetchone()[0] == 0


def test_two_workers_migrating_at_once_both_succeed(tmp_path):
    """gunicorn starts two workers, and each runs init_db: the second must find the first one's work done."""
    path = str(tmp_path / "race.db")
    errors = []

    def run():
        try:
            c = db.connect(path)
            db.migrate(c)
            c.close()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    c = db.connect(path)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 2
    assert TABLES <= {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _v1_database(path):
    """A database as a v1 deploy left it: the v1 schema only, user_version 1."""
    c = db.connect(path)
    with db.transaction(c):
        db.MIGRATIONS[1](c)
        c.execute("PRAGMA user_version = 1")
    return c


def test_v1_database_with_data_upgrades_to_v2(tmp_path):
    c = _v1_database(str(tmp_path / "v1.db"))
    assert "sync_seen" not in {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    pid = mk_place(c, "Casa Ejemplo")
    vid = mk_visit(c, pid, "2026-11-01", {mk_person(c, "Alex"): 4})
    before = {t: [tuple(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY rowid")] for t in ("place", "visit", "visit_person", "person", "meta")}
    db.migrate(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 2
    after = {t: [tuple(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY rowid")] for t in before}
    assert after == before
    c.execute("INSERT INTO sync_seen (source, ref, day, name, status, place_id, visit_id, seen_at) "
              "VALUES ('dinner_log', '1', '2026-11-01', 'Casa Ejemplo', 'logged', ?, ?, 0)", (pid, vid))
    with pytest.raises(sqlite3.IntegrityError):                         # (source, ref) is the ledger's key
        c.execute("INSERT INTO sync_seen (source, ref, status) VALUES ('dinner_log', '1', 'unmatched')")
    for bad in ({"source": "elsewhere", "status": "logged"}, {"source": "where_to_eat", "status": "pending"}):
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO sync_seen (source, ref, status) VALUES (?, '2', ?)", (bad["source"], bad["status"]))
    c.execute("DELETE FROM visit WHERE id = ?", (vid,))                  # the ledger row stays, unlinked
    assert tuple(c.execute("SELECT status, visit_id FROM sync_seen").fetchone()) == ("logged", None)
    schema = schema_text(c)
    db.migrate(c)
    assert schema_text(c) == schema


def test_two_workers_upgrading_a_v1_database_at_once_both_succeed(tmp_path):
    path = str(tmp_path / "race-v1.db")
    _v1_database(path).close()
    errors = []

    def run():
        try:
            c = db.connect(path)
            db.migrate(c)
            c.close()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    c = db.connect(path)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 2 and "sync_seen" in {
        r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_v2_step_is_idempotent_on_its_own(con):
    """MIGRATIONS[2] run again over a database that already has it changes nothing (IF NOT EXISTS throughout)."""
    before = schema_text(con)
    with db.transaction(con):
        db.MIGRATIONS[2](con)
    assert schema_text(con) == before


def test_a_newer_database_is_refused(tmp_path):
    c = db.connect(str(tmp_path / "newer.db"))
    c.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="newer"):
        db.migrate(c)


def test_database_records_when_it_was_created(con):
    assert int(con.execute("SELECT value FROM meta WHERE key = 'created_at'").fetchone()[0]) > 0
    assert settings.DB_PATH.endswith("hometown.db")
