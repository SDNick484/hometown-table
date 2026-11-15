"""Places (spec §5): the record of a restaurant, the ownership verdict and standing an editor sets on it, and the dated
history of every change to either. Route-free; every function takes a sqlite3.Connection and writes inside
db.transaction. Nothing here infers a verdict: ownership is always an editor's decision, with a reason.

A place is never hard-deleted while it has visits. A place sold to a group keeps its visits and history and moves to
Past places (ownership "no", or standing "closed")."""
import re
import sqlite3
from datetime import date, datetime
from zoneinfo import ZoneInfo

import common
import db
import settings
from common import ApiError

OWNERSHIP = ("counts", "unsure", "no")
STANDING = ("open", "temp_closed", "closed")
ORIGINS = ("hand", "scan", "list", "where_to_eat")
DUPLICATE_KM = 0.15
STALE = "Someone else just changed this place. Reload to see it."

# column -> max length, for the free-text fields an editor types (§5.4)
TEXT_FIELDS = {"name": 120, "address": 200, "cuisine": 80, "phone": 40, "website": 300, "hours": 300, "notes": 2000,
               "town": 60}
# every column an editor can set through create() and update(); ownership and standing have their own functions
IDENTITY_FIELDS = ("google_place_id", "osm_ref")
SEARCH_LIMIT_MAX = 50


# ---------------------------------------------------------------- reading
def _row(con, place_id) -> sqlite3.Row:
    row = con.execute("SELECT * FROM place WHERE id = ?", (place_id,)).fetchone()
    if row is None:
        raise ApiError(404, "Place not found.")
    return row


def _history(con, place_id) -> list[dict]:
    rows = con.execute("SELECT kind, old_value, new_value, reason, on_date, by FROM place_event WHERE place_id = ? "
                       "ORDER BY created_at DESC, id DESC", (place_id,)).fetchall()
    return [{"kind": r["kind"], "old": r["old_value"], "new": r["new_value"], "reason": r["reason"], "on": r["on_date"],
             "by": r["by"]} for r in rows]


def _last_visit(con, place_id) -> dict | None:
    v = con.execute("SELECT id, visited_on FROM visit WHERE place_id = ? ORDER BY visited_on DESC, id DESC LIMIT 1",
                    (place_id,)).fetchone()
    if v is None:
        return None
    people = con.execute("SELECT p.name, vp.rating FROM visit_person vp JOIN person p ON p.id = vp.person_id "
                         "WHERE vp.visit_id = ? ORDER BY p.sort_order, p.id", (v["id"],)).fetchall()
    return {"on": v["visited_on"], "people": [{"name": p["name"], "rating": p["rating"]} for p in people]}


def get(con, place_id) -> dict:
    """The place with `tags` ([{id, label, occasion}]), `lists` ([{id, name}]), `history` (newest first, each
    {kind, old, new, reason, on, by}), `last_visit` ({on, people: [{name, rating}]} or None) and `visit_count`."""
    place = dict(_row(con, place_id))
    place["tags"] = [dict(t) for t in con.execute(
        "SELECT t.id, t.label, t.occasion FROM place_tag pt JOIN tag t ON t.id = pt.tag_id WHERE pt.place_id = ? "
        "ORDER BY t.sort_order, t.id", (place_id,))]
    place["lists"] = [dict(r) for r in con.execute(
        "SELECT DISTINCT l.id, l.name FROM list_entry e JOIN source_list l ON l.id = e.list_id WHERE e.place_id = ? "
        "ORDER BY l.name, l.id", (place_id,))]
    place["history"] = _history(con, place_id)
    place["last_visit"] = _last_visit(con, place_id)
    place["visit_count"] = con.execute("SELECT COUNT(*) FROM visit WHERE place_id = ?", (place_id,)).fetchone()[0]
    return place


# ---------------------------------------------------------------- validation
def _choice(value, allowed, label):
    if value not in allowed:
        raise ApiError(400, f"{label} must be one of: {', '.join(allowed)}.")
    return value


def _reason(value) -> str:
    return common.text({"reason": value}, "reason", 200, required=False) or _no_reason()


def _no_reason():
    raise ApiError(400, "Give a reason.")


def _on_date(value) -> str:
    """A calendar day, YYYY-MM-DD, not in the future; None is today (America/Los_Angeles)."""
    if value is None:
        return common.today()
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ApiError(400, "The date must be YYYY-MM-DD.")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ApiError(400, "The date must be YYYY-MM-DD.") from None
    if value > common.today():
        raise ApiError(400, "The date can't be in the future.")
    return value


def _number(data, key, lo, hi):
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
        raise ApiError(400, f"{key} must be a number from {lo} to {hi}.")
    return float(value)


def _int_or_none(data, key):
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError(400, f"{key} must be a whole number.")
    return value


def _clean_fields(data, keys) -> dict:
    """The validated values of the fields in `keys` that `data` carries, as column -> value (None clears a field)."""
    out = {}
    for key in keys:
        if key not in data:
            continue
        if key in TEXT_FIELDS:
            out[key] = common.text(data, key, TEXT_FIELDS[key], required=(key == "name"))
            if key == "town" and out[key]:
                out[key] = next((t.name for t in settings.TOWNS if common.fold(t.name) == common.fold(out[key])), out[key])
        elif key in ("lat", "lng"):
            out[key] = _number(data, key, -90 if key == "lat" else -180, 90 if key == "lat" else 180)
        elif key in IDENTITY_FIELDS:
            out[key] = common.text(data, key, 300)
        elif key == "wte_id":
            out[key] = _int_or_none(data, key)
    if ("lat" in out) != ("lng" in out) or ("lat" in out and (out["lat"] is None) != (out["lng"] is None)):
        raise ApiError(400, "Give both lat and lng, or neither.")
    return out


def _tag_ids(con, value) -> list[int]:
    if not isinstance(value, list) or any(isinstance(i, bool) or not isinstance(i, int) for i in value):
        raise ApiError(400, "tag_ids must be a list of tag ids.")
    ids = list(dict.fromkeys(value))
    known = {r[0] for r in con.execute(f"SELECT id FROM tag WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else set()
    if known != set(ids):
        raise ApiError(400, "Unknown tag.")
    return ids


def _find_duplicate(con, name, lat, lng, skip_id=None):
    """The id of a place within 150 m whose name matches (token subset either way), or None (§5.4)."""
    if lat is None or lng is None:
        return None
    for r in con.execute("SELECT id, name, lat, lng FROM place WHERE lat IS NOT NULL AND lng IS NOT NULL"):
        if r["id"] != skip_id and common.names_match(name, r["name"]) \
                and common.km_between(lat, lng, r["lat"], r["lng"]) <= DUPLICATE_KM:
            return r["id"]
    return None


def _unique_google_id(e: sqlite3.IntegrityError):
    if "google_place_id" in str(e):
        raise ApiError(409, "Another place already has that Google place id.") from None
    raise e


# ---------------------------------------------------------------- writing
def create(con, data: dict, by: str) -> int:
    """Add a place. Needs `name`, `ownership` and `ownership_reason`. See the module header for the optional fields.
    A place within 150 m with a matching name is 409 with `duplicate_of`, unless data["add_anyway"]."""
    name = common.text(data, "name", TEXT_FIELDS["name"], required=True)
    ownership = _choice(data.get("ownership"), OWNERSHIP, "ownership")
    reason = common.text(data, "ownership_reason", 200, required=True)
    origin = _choice(data.get("origin", "hand"), ORIGINS, "origin")
    fields = _clean_fields(data, [k for k in TEXT_FIELDS if k != "name"] + ["lat", "lng", "wte_id", *IDENTITY_FIELDS])
    tag_ids = _tag_ids(con, data.get("tag_ids", []))
    first_seen = _int_or_none(data, "first_seen_at")
    if not fields.get("town"):
        fields["town"] = common.town_for(fields.get("address"), fields.get("lat"), fields.get("lng"))
    dup = _find_duplicate(con, name, fields.get("lat"), fields.get("lng"))
    if dup is not None and not data.get("add_anyway"):
        existing = con.execute("SELECT name FROM place WHERE id = ?", (dup,)).fetchone()["name"]
        raise ApiError(409, f"{existing} is already on the list, close by. Add anyway?", duplicate_of=dup)
    now = common.now()
    want = 1 if data.get("want_to_try") else 0
    row = {"name": name, "ownership": ownership, "ownership_reason": reason, "origin": origin, "first_seen_at": first_seen,
           "want_to_try": want, "want_since": now if want else None, "added_by": by, "created_at": now, "updated_at": now,
           **fields}
    try:
        with db.transaction(con):
            cur = con.execute(f"INSERT INTO place ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
            con.executemany("INSERT INTO place_tag (place_id, tag_id) VALUES (?, ?)", [(cur.lastrowid, t) for t in tag_ids])
    except sqlite3.IntegrityError as e:
        _unique_google_id(e)
    return cur.lastrowid


def update(con, place_id, data: dict, version, by: str) -> dict:
    """Save the fields `data` carries (§5.4 fields, `want_to_try`, `tag_ids`). `version` must be the one the editor
    loaded: a stale one is 409 and nothing is saved. Ownership and standing change only through set_ownership and
    set_standing, so they leave a history."""
    if "ownership" in data or "standing" in data or "ownership_reason" in data:
        raise ApiError(400, "Change ownership and standing with their own buttons, so they leave a history.")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ApiError(400, "version is required.")
    fields = _clean_fields(data, [*TEXT_FIELDS, "lat", "lng", "wte_id", *IDENTITY_FIELDS])
    tag_ids = _tag_ids(con, data["tag_ids"]) if "tag_ids" in data else None
    try:
        with db.transaction(con):
            current = _row(con, place_id)
            if current["version"] != version:
                raise ApiError(409, STALE)
            sets = dict(fields)
            if "want_to_try" in data:
                want = 1 if data["want_to_try"] else 0
                sets["want_to_try"] = want
                sets["want_since"] = (current["want_since"] or common.now()) if want else None
            sets["updated_at"] = common.now()
            assignments = ", ".join(f"{k} = ?" for k in sets)
            con.execute(f"UPDATE place SET {assignments}, version = version + 1 WHERE id = ?", (*sets.values(), place_id))
            if tag_ids is not None:
                _write_tags(con, place_id, tag_ids)
    except sqlite3.IntegrityError as e:
        _unique_google_id(e)
    return get(con, place_id)


def _write_tags(con, place_id, tag_ids):
    con.execute("DELETE FROM place_tag WHERE place_id = ?", (place_id,))
    con.executemany("INSERT INTO place_tag (place_id, tag_id) VALUES (?, ?)", [(place_id, t) for t in tag_ids])


def _change(con, place_id, kind, value, reason, by, on_date) -> dict:
    allowed, column = (OWNERSHIP, "ownership") if kind == "ownership" else (STANDING, "standing")
    value = _choice(value, allowed, kind)
    reason = _reason(reason)
    on = _on_date(on_date)
    with db.transaction(con):
        current = _row(con, place_id)
        old = current[column]
        sets = {}
        if kind == "ownership":
            sets["ownership_reason"] = reason
        else:
            sets.update(standing_since=on, closure_flag=None)
        if value != old:
            sets[column] = value
            con.execute("INSERT INTO place_event (place_id, kind, old_value, new_value, reason, on_date, by, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (place_id, kind, old, value, reason, on, by, common.now()))
        if value != old or kind == "ownership" and reason != current["ownership_reason"]:
            sets["updated_at"] = common.now()
            assignments = ", ".join(f"{k} = ?" for k in sets)
            con.execute(f"UPDATE place SET {assignments}, version = version + 1 WHERE id = ?", (*sets.values(), place_id))
    return get(con, place_id)


def set_ownership(con, place_id, value, reason, by, on_date=None) -> dict:
    """Set the ownership verdict (`counts`, `unsure`, `no`) with a required reason, writing a place_event when it
    changes. Setting the verdict it already has only updates the reason. Visits and history stay."""
    return _change(con, place_id, "ownership", value, reason, by, on_date)


def set_standing(con, place_id, value, reason, by, on_date=None) -> dict:
    """Set the standing (`open`, `temp_closed`, `closed`) with a required reason, writing a place_event when it changes,
    stamping standing_since, and clearing any closure flag (an editor has now looked at it)."""
    return _change(con, place_id, "standing", value, reason, by, on_date)


def delete(con, place_id) -> None:
    """Remove a place made by mistake: only one with no visits. Its events, tags and list entries go with it."""
    with db.transaction(con):
        _row(con, place_id)
        if con.execute("SELECT 1 FROM visit WHERE place_id = ?", (place_id,)).fetchone():
            raise ApiError(409, "This place has visits. Mark it closed or doesn't count instead.")
        con.execute("DELETE FROM place WHERE id = ?", (place_id,))


def set_tags(con, place_id, tag_ids) -> dict:
    ids = _tag_ids(con, tag_ids)
    with db.transaction(con):
        _row(con, place_id)
        _write_tags(con, place_id, ids)
        con.execute("UPDATE place SET updated_at = ? WHERE id = ?", (common.now(), place_id))
    return get(con, place_id)


def set_want_to_try(con, place_id, on: bool) -> dict:
    """Star or unstar. Turning it on stamps want_since (kept if already on); turning it off clears it."""
    with db.transaction(con):
        current = _row(con, place_id)
        since = (current["want_since"] or common.now()) if on else None
        con.execute("UPDATE place SET want_to_try = ?, want_since = ?, updated_at = ? WHERE id = ?",
                    (1 if on else 0, since, common.now(), place_id))
    return get(con, place_id)


# ---------------------------------------------------------------- lists of places
def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _summary(con, row) -> dict:
    out = {k: row[k] for k in ("id", "name", "town", "address", "cuisine", "ownership", "standing", "want_to_try")}
    last = con.execute("SELECT MAX(visited_on) FROM visit WHERE place_id = ?", (row["id"],)).fetchone()[0]
    out["last_visit_on"] = last
    return out


def search(con, q, include_past=False, limit=20) -> list[dict]:
    """Places whose name, cuisine, town, address or notes contain every word of `q`, accents and case ignored, by name.
    Past places (closed, or ownership no) are left out unless include_past."""
    sql = "SELECT * FROM place WHERE 1 = 1"
    args = []
    for term in common.fold(q).split():
        sql += (" AND fold(coalesce(name, '') || ' ' || coalesce(cuisine, '') || ' ' || coalesce(town, '') || ' ' || "
                "coalesce(address, '') || ' ' || coalesce(notes, '')) LIKE ? ESCAPE '\\'")
        args.append(_like(term))
    if not include_past:
        sql += " AND ownership <> 'no' AND standing <> 'closed'"
    sql += " ORDER BY fold(name), id LIMIT ?"
    args.append(limit)
    return [_summary(con, r) for r in con.execute(sql, args)]


def _day(ts) -> str:
    return datetime.fromtimestamp(ts, ZoneInfo(settings.TZ)).date().isoformat()


def past(con) -> list[dict]:
    """Every place that is closed or doesn't count, newest change first. Each carries `kind` ("closed" or
    "ownership": which change put it here, the newer when both), `since` (that change's date), `reason`, and
    `visit_count`. A place that arrived already closed or not counting has no event: it uses its own date and reason."""
    out = []
    for p in con.execute("SELECT * FROM place WHERE standing = 'closed' OR ownership = 'no'"):
        candidates = []
        if p["standing"] == "closed":
            candidates.append(("closed", "standing", p["standing_since"], None))
        if p["ownership"] == "no":
            candidates.append(("ownership", "ownership", None, p["ownership_reason"]))
        best = None
        for kind, event_kind, since, reason in candidates:
            ev = con.execute("SELECT on_date, reason, created_at, id FROM place_event WHERE place_id = ? AND kind = ? "
                             "AND new_value = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                             (p["id"], event_kind, "closed" if kind == "closed" else "no")).fetchone()
            if ev:
                entry = (ev["on_date"], ev["created_at"], ev["id"], kind, ev["on_date"], ev["reason"])
            else:
                entry = (since or _day(p["created_at"]), p["created_at"], 0, kind, since or _day(p["created_at"]), reason)
            best = max(best, entry, key=lambda e: e[:3]) if best else entry
        out.append((best[:3], {
            **_summary(con, p), "kind": best[3], "since": best[4], "reason": best[5],
            "visit_count": con.execute("SELECT COUNT(*) FROM visit WHERE place_id = ?", (p["id"],)).fetchone()[0]}))
    out.sort(key=lambda t: t[0], reverse=True)
    return [row for _, row in out]
