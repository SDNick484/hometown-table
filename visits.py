"""Visits, per-person ratings and notes, and the scores the decider uses (spec §6.2, §6.3).

A visit is a place, a date, who went (one or more active people, each with an optional 1 to 5 rating and note), an
optional occasion and note. Every write is one transaction; logging a visit also clears want_to_try on the place."""
import re
from datetime import date

import common
import db
from common import ApiError

OCCASION_MAX, NOTE_MAX, PERSON_NOTE_MAX = 80, 2000, 500
STALE = "Someone else just changed this visit. Reload to see it."
LIKED = 4   # "liked it" is a latest rating of 4 or 5


# ---------------------------------------------------------------- reading
def _visits(con, where="", args=(), limit=None):
    rows = con.execute(
        "SELECT v.id, v.place_id, p.name AS place_name, v.visited_on, v.occasion, v.note, v.added_by, v.created_at, "
        f"v.version FROM visit v JOIN place p ON p.id = v.place_id {where} "
        "ORDER BY v.visited_on DESC, v.id DESC" + (f" LIMIT {int(limit)}" if limit else ""), args).fetchall()
    out = [dict(r) for r in rows]
    if not out:
        return out
    by_id = {v["id"]: v for v in out}
    for v in out:
        v["people"] = []
    marks = ",".join("?" * len(by_id))
    for r in con.execute(
            "SELECT vp.visit_id, vp.person_id, pe.name, pe.grp, vp.rating, vp.note FROM visit_person vp "
            f"JOIN person pe ON pe.id = vp.person_id WHERE vp.visit_id IN ({marks}) ORDER BY pe.sort_order, pe.id",
            tuple(by_id)):
        by_id[r["visit_id"]]["people"].append(
            {"person_id": r["person_id"], "name": r["name"], "grp": r["grp"], "rating": r["rating"], "note": r["note"]})
    return out


def get(con, visit_id) -> dict:
    found = _visits(con, "WHERE v.id = ?", (visit_id,))
    if not found:
        raise ApiError(404, "No such visit.")
    return found[0]


def _person_filter(con, person):
    """The id for a history filter: a number is an id, text is a name (inactive people still have history)."""
    if isinstance(person, int) and not isinstance(person, bool):
        return person
    text = str(person).strip()
    if text.isdigit():
        return int(text)
    folded = common.fold(text)
    for r in con.execute("SELECT id, name FROM person"):
        if common.fold(r["name"]) == folded:
            return r["id"]
    raise ApiError(400, f"No one called {text}.")


def history(con, place_id=None, person=None, occasion=None, since=None, until=None, limit=50) -> list[dict]:
    """Visits newest first, each with its `people`. `person` is an id or a name; `occasion` matches as a folded
    substring; since and until are inclusive YYYY-MM-DD dates."""
    where, args = [], []
    if place_id is not None:
        where.append("v.place_id = ?")
        args.append(place_id)
    if person not in (None, ""):
        where.append("v.id IN (SELECT visit_id FROM visit_person WHERE person_id = ?)")
        args.append(_person_filter(con, person))
    if since:
        where.append("v.visited_on >= ?")
        args.append(_date(since, "since"))
    if until:
        where.append("v.visited_on <= ?")
        args.append(_date(until, "until"))
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    needle = common.fold(occasion)
    if not needle:
        return _visits(con, clause, tuple(args), limit)
    found = [v for v in _visits(con, clause, tuple(args)) if needle in common.fold(v["occasion"])]
    return found[:limit]


# ---------------------------------------------------------------- scores (§6.3)
def latest_ratings(con, place_id, person_ids) -> dict[int, int]:
    """Each listed person's rating on their most recent rated visit to the place; people with none are left out.
    Two visits on one day: the one logged later wins."""
    out = {}
    for pid in person_ids:
        row = con.execute(
            "SELECT vp.rating FROM visit_person vp JOIN visit v ON v.id = vp.visit_id "
            "WHERE v.place_id = ? AND vp.person_id = ? AND vp.rating IS NOT NULL "
            "ORDER BY v.visited_on DESC, v.id DESC LIMIT 1", (place_id, pid)).fetchone()
        if row:
            out[pid] = row[0]
    return out


def score(con, place_id, person_ids) -> float | None:
    """The mean of the audience's latest ratings, or None when no one in it has rated the place."""
    ratings = latest_ratings(con, place_id, person_ids)
    return sum(ratings.values()) / len(ratings) if ratings else None


def liked_by_all(con, place_id, person_ids) -> bool:
    """Everyone in the audience who has a rating gave their latest 4 or 5, and at least one has."""
    ratings = latest_ratings(con, place_id, person_ids)
    return bool(ratings) and all(r >= LIKED for r in ratings.values())


# ---------------------------------------------------------------- checking input
def _date(value, label="visited_on") -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        raise ApiError(400, f"{label} must be a date like 2026-11-14.")
    value = value.strip()
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ApiError(400, f"{label} must be a date like 2026-11-14.") from None
    return value


def _visit_date(value) -> str:
    on = common.today() if value in (None, "") else _date(value)
    if on > common.today():
        raise ApiError(400, "That date is in the future.")
    return on


def _resolve(con, ref, me_person_id) -> int:
    if isinstance(ref, str) and ref.strip().casefold() == "me":
        if me_person_id is None:
            raise ApiError(400, "Link your email to a person in Settings.")
        return me_person_id
    if isinstance(ref, int) and not isinstance(ref, bool):
        if con.execute("SELECT 1 FROM person WHERE id = ? AND active = 1", (ref,)).fetchone():
            return ref
        raise ApiError(400, f"No one with id {ref}.")
    if not isinstance(ref, str) or not ref.strip():
        raise ApiError(400, "Who went?")
    folded = common.fold(ref)
    for r in con.execute("SELECT id, name FROM person WHERE active = 1"):
        if common.fold(r["name"]) == folded:
            return r["id"]
    raise ApiError(400, f"No one called {ref.strip()}.")


def _people(con, raw, me_person_id) -> list[tuple[int, int | None, str | None]]:
    if not isinstance(raw, list) or not raw:
        raise ApiError(400, "Who went?")
    out, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            item = {"person": item}      # a bare name or id is a person with no rating
        pid = _resolve(con, item.get("person"), me_person_id)
        if pid in seen:
            raise ApiError(400, "Each person can be on a visit once.")
        seen.add(pid)
        rating = item.get("rating")
        if rating is not None and (isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5):
            raise ApiError(400, "A rating is a whole number of stars from 1 to 5.")
        out.append((pid, rating, common.text(item, "note", PERSON_NOTE_MAX)))
    return out


def _write_people(con, visit_id, people):
    con.execute("DELETE FROM visit_person WHERE visit_id = ?", (visit_id,))
    con.executemany("INSERT INTO visit_person (visit_id, person_id, rating, note) VALUES (?, ?, ?, ?)",
                    [(visit_id, *p) for p in people])


# ---------------------------------------------------------------- writing
def log(con, place_id, data, by, me_person_id) -> int:
    """Save a visit and clear want_to_try on the place, in one transaction. Returns the visit id. Everything is
    checked before anything is written, so a 400 leaves no visit behind. A back-dated visit (before the LA day of
    want_since) leaves a later star alone: the place was starred after that visit."""
    if not isinstance(data, dict):
        raise ApiError(400, "Expected a JSON object.")
    on = _visit_date(data.get("visited_on"))
    occasion = common.text(data, "occasion", OCCASION_MAX)
    note = common.text(data, "note", NOTE_MAX)
    with db.transaction(con):
        if not con.execute("SELECT 1 FROM place WHERE id = ?", (place_id,)).fetchone():
            raise ApiError(404, "No such place.")
        people = _people(con, data.get("people"), me_person_id)
        now = common.now()
        vid = con.execute("INSERT INTO visit (place_id, visited_on, occasion, note, added_by, created_at) "
                          "VALUES (?, ?, ?, ?, ?, ?)", (place_id, on, occasion, note, by, now)).lastrowid
        _write_people(con, vid, people)
        star = con.execute("SELECT want_to_try, want_since FROM place WHERE id = ?", (place_id,)).fetchone()
        if star["want_to_try"] and (star["want_since"] is None or on >= common.day_of(star["want_since"])):
            con.execute("UPDATE place SET want_to_try = 0, want_since = NULL, updated_at = ? WHERE id = ?",
                        (now, place_id))
    return vid


def update(con, visit_id, data, version, by, me_person_id=None) -> dict:
    """Change the keys present in `data` (visited_on, people, occasion, note; people replaces the whole list).
    409 when `version` is not the visit's current one. Returns the visit."""
    if not isinstance(data, dict):
        raise ApiError(400, "Expected a JSON object.")
    with db.transaction(con):
        row = con.execute("SELECT version FROM visit WHERE id = ?", (visit_id,)).fetchone()
        if not row:
            raise ApiError(404, "No such visit.")
        if row[0] != version:
            raise ApiError(409, STALE)
        sets, args = [], []
        if "visited_on" in data:
            sets.append("visited_on = ?")
            args.append(_visit_date(data["visited_on"]))
        if "occasion" in data:
            sets.append("occasion = ?")
            args.append(common.text(data, "occasion", OCCASION_MAX))
        if "note" in data:
            sets.append("note = ?")
            args.append(common.text(data, "note", NOTE_MAX))
        people = _people(con, data["people"], me_person_id) if "people" in data else None
        con.execute(f"UPDATE visit SET {''.join(s + ', ' for s in sets)}version = version + 1 WHERE id = ?",
                    (*args, visit_id))
        if people is not None:
            _write_people(con, visit_id, people)
    return get(con, visit_id)


def delete(con, visit_id) -> None:
    with db.transaction(con):
        if not con.execute("DELETE FROM visit WHERE id = ?", (visit_id,)).rowcount:
            raise ApiError(404, "No such visit.")
