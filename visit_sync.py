"""Visits synced from Dinner Log and Where to Eat (Task 15), once a day by `python3 manage.py sync-visits`.

Each app serves a read-only feed, `GET <base><path>?after=<cursor>&limit=200` with `Authorization: Bearer <token>`,
answering `{"version": 1, "visits": [...], "next": <cursor>}` (ids ascending; an empty `visits` is the end). Every
entry is settled once, into the `sync_seen` ledger keyed by (source, ref):

  logged     a visit was logged here at the matching place, on the entry's day, with the default group and no ratings
  duplicate  that place already had a visit that day (from either app or by hand): it is linked, nothing is logged
  unmatched  no single place matched (or the day is not a past date): re-tried at every run, dismissable in Settings
  dismissed  an editor dismissed it: never looked at again

A ref in the ledger is never logged again, even after its visit is deleted here. Later edits or deletions in the other
apps are not propagated: the feed is read forward from the stored cursor only.

Route-free; takes a connection. No db.transaction is held across HTTP: a page is fetched first, then each entry is
written (visits.log runs its own transaction), then the cursor moves past the page. A crash between a visit and its
ledger row leaves the visit, which the next run finds as a same-day duplicate, so nothing is ever logged twice. One
run at a time: `meta.sync_running` is a lock, stale after an hour.

meta keys: sync_people (JSON list of person ids), sync_cursor_<source>, sync_last_<source> (JSON: at, status, counts),
sync_error (JSON {source: message} for each source whose last run failed; health.problems reads it), sync_running."""
import json
import re
from datetime import date

import common
import db
import settings
import visits

SOURCES = ("dinner_log", "where_to_eat")
LABELS = {"dinner_log": "Dinner Log", "where_to_eat": "Where to Eat"}
PATHS = {"dinner_log": "/api/restaurant-visits", "where_to_eat": "/feed/visits"}
SETTING_NAMES = {"dinner_log": ("DINNER_LOG_URL", "DINNER_LOG_TOKEN"), "where_to_eat": ("WTE_URL", "WTE_TOKEN")}
PAGE = 200
MAX_PAGES = 10_000          # a feed that never ends is an error, not a hang
MATCH_KM = 0.15
LOCK_STALE_S = 3600
ERROR_MAX = 500
TEXT_MAX = 500              # longest name, location or dish kept from a feed
COUNTS = ("new", "logged", "duplicate", "unmatched")


class SyncError(Exception):
    """A run (or one source of it) failed. The message names the app, never a token."""


# ---------------------------------------------------------------- settings and meta
def configured(source) -> bool:
    url, token = (getattr(settings, n) for n in SETTING_NAMES[source])
    return bool(url and token)


def _meta(con, key):
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set_meta(con, key, value):
    if value is None:
        con.execute("DELETE FROM meta WHERE key = ?", (key,))
    else:
        con.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                    (key, value))


def _json(raw, default):
    try:
        return json.loads(raw) if raw else default
    except ValueError:
        return default


def people(con) -> list[int]:
    """The default group for synced visits: the stored ids that are still active people, in the stored order."""
    stored = _json(_meta(con, "sync_people"), [])
    active = {r[0] for r in con.execute("SELECT id FROM person WHERE active = 1")}
    return [i for i in stored if isinstance(i, int) and i in active]


def set_people(con, ids) -> list[int]:
    """Store the default group: a list of active person ids, each once (an empty list turns syncing off)."""
    if not isinstance(ids, list) or any(isinstance(i, bool) or not isinstance(i, int) for i in ids):
        raise common.ApiError(400, "people must be a list of person ids.")
    if len(set(ids)) != len(ids):
        raise common.ApiError(400, "Each person can be in the group once.")
    with db.transaction(con):
        for i in ids:
            if not con.execute("SELECT 1 FROM person WHERE id = ? AND active = 1", (i,)).fetchone():
                raise common.ApiError(400, f"No one with id {i}.")
        _set_meta(con, "sync_people", json.dumps(ids))
    return ids


def run_errors(con) -> dict[str, str]:
    """{source: message} for each source whose last run failed."""
    return _json(_meta(con, "sync_error"), {})


def failing(con) -> list[str]:
    """The configured sources whose last run failed (what /healthz reports)."""
    errors = run_errors(con)
    return [s for s in SOURCES if s in errors and configured(s)]


# ---------------------------------------------------------------- the feed
def _feed_url(source, after):
    base = getattr(settings, SETTING_NAMES[source][0]).rstrip("/")
    if not base.endswith(PATHS[source]):
        base += PATHS[source]
    return f"{base}?after={int(after)}&limit={PAGE}"


def _fetch(source, after) -> tuple[list, int]:
    label, (url_name, token_name) = LABELS[source], SETTING_NAMES[source]
    try:
        answer = common.http_json(_feed_url(source, after),
                                  headers={"Authorization": f"Bearer {getattr(settings, token_name)}"})
    except common.UpstreamError as e:
        if e.status == 401:
            raise SyncError(f"{label} refused the token (401): check {token_name} here and the feed token in "
                            f"{label}.") from None
        if e.status == 404:
            raise SyncError(f"{label} has no visit feed (404): set its feed token in {label}, or check "
                            f"{url_name}.") from None
        raise SyncError(f"{label}: {e}") from None
    if not (isinstance(answer, dict) and answer.get("version") == 1 and isinstance(answer.get("visits"), list)
            and isinstance(answer.get("next"), int) and not isinstance(answer.get("next"), bool)):
        raise SyncError(f"{label} sent an answer this version doesn't understand.")
    return answer["visits"], answer["next"]


def _text(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:TEXT_MAX]


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _entry(source, raw) -> dict | None:
    """A feed entry as a ledger row (without status), or None when it has no usable ref."""
    if not isinstance(raw, dict):
        return None
    ref = _text(raw.get("ref"))
    if ref is None or len(ref) > 100:
        return None
    if source == "dinner_log":
        extra = {"dish": _text(raw.get("dish"))}
    else:
        wte_id = raw.get("place_id")
        extra = {"place_id": wte_id if isinstance(wte_id, int) and not isinstance(wte_id, bool) else None,
                 "address": _text(raw.get("address")), "lat": _number(raw.get("lat")), "lng": _number(raw.get("lng"))}
    return {"source": source, "ref": ref, "day": _text(raw.get("day")), "name": _text(raw.get("name")),
            "location": _text(raw.get("location")), "extra": {k: v for k, v in extra.items() if v is not None}}


# ---------------------------------------------------------------- matching
def _one(rows):
    return rows[0]["id"] if len(rows) == 1 else None


def _by_town(named, town):
    if town is None:
        return _one(named)
    return _one([p for p in named if common.fold(p["town"]) == common.fold(town)])


def match(con, source, name, location, extra) -> int | None:
    """The one place an entry means, or None when there is none or several. Any standing: a visit to a place that
    has since closed still happened.

    Where to Eat: the place whose wte_id is the entry's place_id; else, with a pin, the one place whose name matches
    (common.names_match) within 150 m (a place here without a pin is matched by name and town instead); else by name
    and town (the town of the address, else of the area). Dinner Log: name and town, the town being
    common.town_for(location) when it finds one, else name alone."""
    if not name:
        return None
    places = con.execute("SELECT id, name, town, lat, lng, wte_id FROM place").fetchall()
    if source == "where_to_eat":
        wte_id = extra.get("place_id")
        if wte_id is not None:
            found = _one([p for p in places if p["wte_id"] == wte_id])
            if found is not None:
                return found
        named = [p for p in places if common.names_match(p["name"], name)]
        town = common.town_for(extra.get("address"), None, None) or common.town_for(location, None, None)
        lat, lng = extra.get("lat"), extra.get("lng")
        if lat is not None and lng is not None:
            close = [p for p in named if p["lat"] is not None and p["lng"] is not None
                     and common.km_between(lat, lng, p["lat"], p["lng"]) <= MATCH_KM]
            if close:
                return _one(close)
            town = town or common.town_for(None, lat, lng)
            return _by_town([p for p in named if p["lat"] is None or p["lng"] is None], town) if town else None
        return _by_town(named, town)
    named = [p for p in places if common.names_match(p["name"], name)]
    return _by_town(named, common.town_for(location, None, None))


def _past_day(day) -> bool:
    if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return False
    try:
        date.fromisoformat(day)
    except ValueError:
        return False
    return day <= common.today()


def _settle(con, row, group) -> tuple[str, int | None, int | None]:
    """(status, place_id, visit_id) for an entry not yet settled: logs the visit when it should be logged."""
    place_id = match(con, row["source"], row["name"], row["location"], row["extra"]) if _past_day(row["day"]) else None
    if place_id is None:
        return "unmatched", None, None
    same_day = con.execute("SELECT id FROM visit WHERE place_id = ? AND visited_on = ? ORDER BY id LIMIT 1",
                           (place_id, row["day"])).fetchone()
    if same_day:
        return "duplicate", place_id, same_day[0]
    dish = row["extra"].get("dish")
    note = f"Dinner Log: {dish}"[:visits.NOTE_MAX] if dish else None
    vid = visits.log(con, place_id, {"visited_on": row["day"], "people": list(group), "note": note},
                     by=f"sync:{row['source']}", me_person_id=None)
    return "logged", place_id, vid


# ---------------------------------------------------------------- a run
def _sync_source(con, source, group) -> dict:
    counts = dict.fromkeys(COUNTS, 0)
    for r in con.execute("SELECT * FROM sync_seen WHERE source = ? AND status = 'unmatched' ORDER BY seen_at, ref",
                         (source,)).fetchall():
        row = dict(r) | {"extra": _json(r["extra"], {})}
        status, place_id, visit_id = _settle(con, row, group)
        if status != "unmatched":
            con.execute("UPDATE sync_seen SET status = ?, place_id = ?, visit_id = ? "
                        "WHERE source = ? AND ref = ? AND status = 'unmatched'",
                        (status, place_id, visit_id, source, row["ref"]))
            counts[status] += 1
    after = int(_meta(con, f"sync_cursor_{source}") or 0)
    for _ in range(MAX_PAGES):
        entries, nxt = _fetch(source, after)
        if not entries:
            return counts
        if nxt <= after:
            raise SyncError(f"{LABELS[source]}'s feed cursor did not advance past {after}.")
        for raw in entries:
            row = _entry(source, raw)
            if row is None or con.execute("SELECT 1 FROM sync_seen WHERE source = ? AND ref = ?",
                                          (source, row["ref"])).fetchone():
                continue
            status, place_id, visit_id = _settle(con, row, group)
            con.execute("INSERT INTO sync_seen (source, ref, day, name, location, extra, status, place_id, visit_id, "
                        "seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (source, row["ref"], row["day"], row["name"], row["location"],
                         json.dumps(row["extra"]) if row["extra"] else None, status, place_id, visit_id, common.now()))
            counts["new"] += 1
            counts[status] += 1
        _set_meta(con, f"sync_cursor_{source}", str(nxt))
        after = nxt
    raise SyncError(f"{LABELS[source]}'s feed did not end after {MAX_PAGES} pages.")


def _lock(con):
    with db.transaction(con):
        held = _meta(con, "sync_running")
        if held and held.isdigit() and common.now() - int(held) < LOCK_STALE_S:
            raise SyncError("A visit sync is already running.")
        _set_meta(con, "sync_running", str(common.now()))


def _record(con, source, last, error):
    with db.transaction(con):
        _set_meta(con, f"sync_last_{source}", json.dumps(last))
        errors = run_errors(con)
        if error:
            errors[source] = error[:ERROR_MAX]
        else:
            errors.pop(source, None)
        _set_meta(con, "sync_error", json.dumps(errors) if errors else None)


def run(con) -> dict:
    """Sync every configured source; returns {source: {"status": "off"|"no_people"|"ok"|"failed", counts...}}. A
    source that fails doesn't stop the other; its message is stored (sync_error) and the run then raises SyncError."""
    out = {s: {"status": "off"} for s in SOURCES}
    on = [s for s in SOURCES if configured(s)]
    if not on:
        return out
    _lock(con)
    failures = []
    try:
        group = people(con)
        for source in on:
            started = common.now()
            if not group:
                out[source] = {"status": "no_people"}
                _record(con, source, {"at": started, "status": "no_people"}, None)
                continue
            try:
                counts = _sync_source(con, source, group)
            except Exception as e:
                message = str(e) if isinstance(e, SyncError) else f"{LABELS[source]}: {e}"
                failures.append(message)
                out[source] = {"status": "failed", "error": message}
                _record(con, source, {"at": started, "status": "failed"}, message)
                continue
            out[source] = {"status": "ok", **counts}
            _record(con, source, {"at": started, "status": "ok", **counts}, None)
    finally:
        _set_meta(con, "sync_running", None)
    if failures:
        raise SyncError(" ".join(failures))
    return out


# ---------------------------------------------------------------- Settings
def status(con) -> dict:
    """What Settings shows: the group, each source's state, and the unmatched entries (newest day first)."""
    errors = run_errors(con)
    sources = [{"source": s, "label": LABELS[s], "configured": configured(s), "settings": list(SETTING_NAMES[s]),
                "last": _json(_meta(con, f"sync_last_{s}"), None), "error": errors.get(s)} for s in SOURCES]
    unmatched = [{"source": r["source"], "label": LABELS[r["source"]], "ref": r["ref"], "day": r["day"],
                  "name": r["name"], "location": r["location"]}
                 for r in con.execute("SELECT * FROM sync_seen WHERE status = 'unmatched' "
                                      "ORDER BY day DESC, source, CAST(ref AS INTEGER) DESC, ref DESC")]
    return {"people": people(con), "sources": sources, "unmatched": unmatched}


def dismiss(con, source, ref) -> None:
    """Mark an unmatched entry dismissed: 404 for an unknown one, 409 when it is no longer unmatched."""
    with db.transaction(con):
        row = con.execute("SELECT status FROM sync_seen WHERE source = ? AND ref = ?", (source, ref)).fetchone()
        if row is None:
            raise common.ApiError(404, "No such synced visit.")
        if row[0] != "unmatched":
            raise common.ApiError(409, "That visit is no longer waiting for a place.")
        con.execute("UPDATE sync_seen SET status = 'dismissed' WHERE source = ? AND ref = ?", (source, ref))
