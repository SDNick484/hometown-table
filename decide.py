"""The decider: "where tonight?" (spec §7). One ranking function serves the home screen, GET /api/decide and the
MCP's suggest_places, so they always agree.

Eligible means `ownership` in (counts, unsure) and `standing = open`; nothing else is ever offered, whatever the mode or
filters (a place sold to a group, closed or temporarily closed never appears). The audience (everyone, adults, kids,
me, or one person's name) decides whose visits count as "visited", whose last visit is "last visit", and whose
latest ratings make the score (visits.score, §6.3).

Order of work: eligible places -> the mode's picks -> filters -> the mode's order (and distance for `any` and
`never_been` when `near` is given) -> the seeded shuffle -> offset and limit. `total` counts what matched before
paging."""
import random
import re
from collections.abc import Mapping
from datetime import datetime
from zoneinfo import ZoneInfo

import common
import hours
import settings
import visits
from common import ApiError

MODES = ("not_lately", "want", "never_been", "new_in_town", "occasion", "any")
AUDIENCES = ("everyone", "adults", "kids", "me")
LIMIT_MAX, LIMIT_DEFAULT, OFFSET_MAX = 50, 10, 100_000
TEXT_MAX = 100          # audience, cuisine, town, a tag or list name, seed
LIKED_SCORE = 4         # not_lately picks places the audience scores 4 or more
NO_LINK = "Link your email to a person in Settings."
TRUE, FALSE = ("1", "true", "yes", "on"), ("0", "false", "no", "off", "")
WHOLE = re.compile(r"-?[0-9]{1,18}")    # ASCII digits only: str.isdigit() also passes "²", which int() refuses


class ParamError(ApiError):
    """A parameter the decider can't use. Always 400 (app.py renders every ApiError)."""

    def __init__(self, message: str):
        super().__init__(400, message)


# ---------------------------------------------------------------- parameters
def _values(args: Mapping, key: str) -> list:
    """Every value given for key: a query string's repeats, an MCP list, or one value. Blank strings are dropped."""
    if hasattr(args, "getlist"):
        raw = args.getlist(key)
    else:
        value = args.get(key)
        raw = [] if value is None else list(value) if isinstance(value, (list, tuple)) else [value]
    return [v.strip() if isinstance(v, str) else v for v in raw if not (isinstance(v, str) and not v.strip())]


def _one(args: Mapping, key: str):
    """The single value for key, or None. A list of several (from MCP kwargs) is 400."""
    found = _values(args, key)
    if hasattr(args, "getlist"):
        found = found[:1]           # a repeated query-string key: the first one wins, as Flask's args.get does
    if len(found) > 1:
        raise ParamError(f"{key} takes one value.")
    return found[0] if found else None


def _text(args, key) -> str | None:
    value = _one(args, key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ParamError(f"{key} must be text.")
    value = str(value).strip()
    if len(value) > TEXT_MAX:
        raise ParamError(f"{key} must be at most {TEXT_MAX} characters.")
    return value or None


def _whole(args, key, lo, hi, default) -> int:
    value = _one(args, key)
    if value is None:
        return default
    if isinstance(value, str) and WHOLE.fullmatch(value):
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ParamError(f"{key} must be a whole number from {lo} to {hi}.")
    return value


def _flag(args, key) -> bool:
    value = _one(args, key)
    if value is None or isinstance(value, bool):
        return bool(value)
    if str(value).strip().lower() in TRUE:
        return True
    if str(value).strip().lower() in FALSE:
        return False
    raise ParamError(f"{key} must be true or false.")


def _open_now(args):
    value = _one(args, "open_now")
    if value is None or isinstance(value, bool):
        return bool(value)
    word = str(value).strip().lower()
    if word == "strict":
        return "strict"
    if word in TRUE:
        return True
    if word in FALSE:
        return False
    raise ParamError("open_now must be true, false or strict.")


def _near(args) -> tuple[float, float] | None:
    values = _values(args, "near")
    if hasattr(args, "getlist"):
        values = values[:1]
    if not values:
        return None
    value = values[0] if len(values) == 1 else values       # "lat,lng", or (lat, lng) from MCP kwargs
    parts = value.split(",") if isinstance(value, str) else value
    try:
        if isinstance(parts, (str, bytes)) or len(parts) != 2 or any(isinstance(p, bool) for p in parts):
            raise ValueError
        lat, lng = (float(p) for p in parts)
    except (TypeError, ValueError):
        raise ParamError("near must be lat,lng (for example 37.978,-122.031).") from None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ParamError("near must be lat,lng (for example 37.978,-122.031).")
    return lat, lng


def _seed(args):
    value = _one(args, "seed")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ParamError("seed must be a number or short text.")
    if isinstance(value, str):
        if len(value) > TEXT_MAX:
            raise ParamError(f"seed must be at most {TEXT_MAX} characters.")
        if WHOLE.fullmatch(value):
            return int(value)                # "42" and 42 shuffle the same way
    return value


def _town(args) -> str | None:
    value = _text(args, "town")
    if value is None:
        return None
    for town in settings.TOWNS:
        if common.fold(town.name) == common.fold(value):
            return town.name
    raise ParamError(f"No town called {value}.")


def _mode(args) -> str:
    value = (_text(args, "mode") or "not_lately").lower()
    if value not in MODES:
        raise ParamError(f"There's no mode {value}. Pick one of {', '.join(MODES)}.")
    return value


def _refs(args, key) -> list:
    out = []
    for v in _values(args, key):
        if isinstance(v, bool) or not isinstance(v, (str, int)):
            raise ParamError(f"{key} must be an id or a name.")
        out.append(str(v))
    return out


def parse_params(args: Mapping) -> dict:
    """The decider's parameters from a query string (a MultiDict), MCP keyword arguments (a dict) or an earlier
    parse_params result (parsing twice changes nothing). Unknown keys are ignored. Raises ParamError (400).

    mode (MODES, default not_lately); audience (everyone, adults, kids, me or a name; default everyone); cuisine
    (folded substring); town (one of TOWNS, any case); tag and list (repeatable ids or names; every one must match);
    liked_by_all (bool); near ("lat,lng"); open_now (true, false or strict); seed (shuffles); limit (1 to 50, default
    10); offset (default 0). Names (audience, tag, list) are looked up by decide(), which has the database."""
    return {
        "mode": _mode(args),
        "audience": _text(args, "audience") or "everyone",
        "cuisine": _text(args, "cuisine"),
        "town": _town(args),
        "tag": _refs(args, "tag"),
        "list": _refs(args, "list"),
        "liked_by_all": _flag(args, "liked_by_all"),
        "near": _near(args),
        "open_now": _open_now(args),
        "seed": _seed(args),
        "limit": _whole(args, "limit", 1, LIMIT_MAX, LIMIT_DEFAULT),
        "offset": _whole(args, "offset", 0, OFFSET_MAX, 0),
    }


# ---------------------------------------------------------------- looking things up
def audience_ids(con, audience: str, me_person_id=None) -> list[int]:
    """The active people an audience means. `me` without a linked person, or a name no active person has, is 400."""
    word = common.fold(audience)
    if word == "everyone":
        where, args = "", ()
    elif word in ("adults", "kids"):
        where, args = "AND grp = ?", (word[:-1],)     # adults -> adult, kids -> kid
    elif word == "me":
        if me_person_id is None:
            raise ParamError(NO_LINK)
        return [me_person_id]
    else:
        for r in con.execute("SELECT id, name FROM person WHERE active = 1"):
            if common.fold(r["name"]) == word:
                return [r["id"]]
        raise ParamError(f"No one called {audience}.")
    return [r[0] for r in con.execute(f"SELECT id FROM person WHERE active = 1 {where} ORDER BY sort_order, id", args)]


def _lookup(con, table, column, ref, what) -> int:
    if WHOLE.fullmatch(ref) and con.execute(f"SELECT 1 FROM {table} WHERE id = ?", (int(ref),)).fetchone():
        return int(ref)
    for r in con.execute(f"SELECT id, {column} FROM {table}"):
        if common.fold(r[column]) == common.fold(ref):
            return r["id"]
    raise ParamError(f"No {what} called {ref}.")


def _place_sets(con, table, key, ids) -> list[set[int]]:
    return [{r[0] for r in con.execute(f"SELECT place_id FROM {table} WHERE {key} = ? AND place_id IS NOT NULL", (i,))}
            for i in ids]


def _baseline(con) -> int | None:
    row = con.execute("SELECT value FROM meta WHERE key = 'baseline_at'").fetchone()
    try:
        return int(float(row[0])) if row and row[0] not in (None, "") else None
    except ValueError:
        return None


def _local(now) -> datetime:
    """`now` (epoch seconds, a datetime, or None for common.now()) as a naive datetime in America/Los_Angeles."""
    zone = ZoneInfo(settings.TZ)
    if now is None:
        now = common.now()
    if isinstance(now, datetime):
        return now.astimezone(zone).replace(tzinfo=None) if now.tzinfo else now
    return datetime.fromtimestamp(now, zone).replace(tzinfo=None)


def _days_between(earlier: str, later) -> int:
    return (later - datetime.fromisoformat(earlier).date()).days


# ---------------------------------------------------------------- the decider
def decide(con, params, me_person_id=None, now=None) -> dict:
    """{"mode", "total", "places": [card]} for parse_params-style `params` (raw ones are parsed here). A card is
    {id, name, town, cuisine, unsure, want_to_try, tags, last_visit, score, distance_km (only with near), open,
    closes_at}. `now` (epoch seconds or a datetime; naive means LA local time) defaults to the clock."""
    p = parse_params(params)
    local = _local(now)
    audience = audience_ids(con, p["audience"], me_person_id)
    tag_ids = [_lookup(con, "tag", "label", t, "tag") for t in p["tag"]]
    list_ids = [_lookup(con, "source_list", "name", x, "list") for x in p["list"]]
    mode = p["mode"]

    where, args = ["ownership IN ('counts', 'unsure')", "standing = 'open'"], []
    if p["town"]:
        where.append("town = ?")
        args.append(p["town"])
    if mode == "want":
        where.append("want_to_try = 1")
    if mode == "new_in_town":
        baseline = _baseline(con)
        if baseline is None:
            return {"mode": mode, "total": 0, "places": []}
        where.append("origin = 'scan' AND first_seen_at > ? AND NOT EXISTS (SELECT 1 FROM visit v WHERE v.place_id = place.id)")
        args.append(baseline)
    if mode == "occasion":
        where.append("EXISTS (SELECT 1 FROM place_tag pt JOIN tag t ON t.id = pt.tag_id "
                     "WHERE pt.place_id = place.id AND t.occasion = 1)")
    rows = [dict(r) for r in con.execute(
        "SELECT id, name, town, cuisine, lat, lng, hours, ownership, want_to_try, want_since, first_seen_at, created_at "
        f"FROM place WHERE {' AND '.join(where)}", args)]

    if p["cuisine"]:
        needle = common.fold(p["cuisine"])
        rows = [r for r in rows if needle in common.fold(r["cuisine"])]
    for wanted in _place_sets(con, "place_tag", "tag_id", tag_ids) + _place_sets(con, "list_entry", "list_id", list_ids):
        rows = [r for r in rows if r["id"] in wanted]

    last = _last_visits(con, audience)
    if mode in ("want", "never_been"):
        rows = [r for r in rows if r["id"] not in last]

    scores: dict[int, float | None] = {}

    def score(pid):
        if pid not in scores:
            scores[pid] = visits.score(con, pid, audience) if pid in last else None
        return scores[pid]

    if mode == "not_lately":
        rows = [r for r in rows if r["id"] in last and _days_between(last[r["id"]][1], local.date()) > settings.REST_DAYS
                and (score(r["id"]) or 0) >= LIKED_SCORE]
    if p["liked_by_all"]:
        rows = [r for r in rows if r["id"] in last and visits.liked_by_all(con, r["id"], audience)]
    for r in rows:
        r["open"] = hours.open_at(r["hours"], local)
    if p["open_now"]:
        rows = [r for r in rows if r["open"] is True or (r["open"] is None and p["open_now"] != "strict")]

    _order(rows, mode, last, score)
    if p["near"]:
        for r in rows:
            has_pin = r["lat"] is not None and r["lng"] is not None
            r["distance_km"] = round(common.km_between(*p["near"], r["lat"], r["lng"]), 2) if has_pin else None
        if mode in ("any", "never_been"):
            rows.sort(key=lambda r: (r["distance_km"] is None, r["distance_km"] or 0))
    if p["seed"] is not None:
        random.Random(p["seed"]).shuffle(rows)

    page = rows[p["offset"]:p["offset"] + p["limit"]]
    return {"mode": mode, "total": len(rows), "places": _cards(con, page, last, score, local, bool(p["near"]))}


def _last_visits(con, audience) -> dict[int, tuple[int, str]]:
    """place id -> (visit id, date) of the newest visit anyone in the audience was on."""
    if not audience:
        return {}
    marks = ",".join("?" * len(audience))
    out = {}
    for r in con.execute(
            "SELECT v.place_id, v.id, v.visited_on FROM visit v WHERE EXISTS (SELECT 1 FROM visit_person vp "
            f"WHERE vp.visit_id = v.id AND vp.person_id IN ({marks})) ORDER BY v.visited_on DESC, v.id DESC",
            tuple(audience)):
        out.setdefault(r["place_id"], (r["id"], r["visited_on"]))
    return out


def _order(rows, mode, last, score):
    """Sort in place by the mode's order (spec §7.1); name, then id, breaks every tie."""
    rows.sort(key=lambda r: (common.fold(r["name"]), r["id"]))
    if mode == "not_lately":        # longest since the last visit first, ties by score
        rows.sort(key=lambda r: -(score(r["id"]) or 0))
        rows.sort(key=lambda r: last[r["id"]][1])
    elif mode == "want":            # oldest starred first
        rows.sort(key=lambda r: (r["want_since"] is None, r["want_since"] or 0))
    elif mode == "never_been":      # newest added first
        rows.sort(key=lambda r: (-r["created_at"], -r["id"]))
    elif mode == "new_in_town":     # newest first seen first
        rows.sort(key=lambda r: -(r["first_seen_at"] or 0))
    elif mode == "occasion":        # audience score, highest first; unrated last
        rows.sort(key=lambda r: (score(r["id"]) is None, -(score(r["id"]) or 0)))


def _cards(con, rows, last, score, local, near) -> list[dict]:
    ids = [r["id"] for r in rows]
    marks = ",".join("?" * len(ids))
    tags: dict[int, list] = {i: [] for i in ids}
    people: dict[int, list] = {}
    if ids:
        for t in con.execute(f"SELECT pt.place_id, t.id, t.label FROM place_tag pt JOIN tag t ON t.id = pt.tag_id "
                             f"WHERE pt.place_id IN ({marks}) ORDER BY t.sort_order, t.id", ids):
            tags[t["place_id"]].append({"id": t["id"], "label": t["label"]})
        visit_ids = [last[i][0] for i in ids if i in last]
        if visit_ids:
            for v in con.execute(
                    "SELECT vp.visit_id, pe.name, vp.rating FROM visit_person vp JOIN person pe ON pe.id = vp.person_id "
                    f"WHERE vp.visit_id IN ({','.join('?' * len(visit_ids))}) ORDER BY pe.sort_order, pe.id", visit_ids):
                people.setdefault(v["visit_id"], []).append({"name": v["name"], "rating": v["rating"]})
    out = []
    for r in rows:
        seen = last.get(r["id"])
        s = score(r["id"])
        card = {
            "id": r["id"], "name": r["name"], "town": r["town"], "cuisine": r["cuisine"],
            "unsure": r["ownership"] == "unsure", "want_to_try": bool(r["want_to_try"]),
            "tags": tags[r["id"]],
            "last_visit": {"on": seen[1], "people": people.get(seen[0], [])} if seen else None,
            "score": round(s, 2) if s is not None else None,
            "open": r["open"], "closes_at": hours.closes_at(r["hours"], local) if r["open"] else None,
        }
        if near:
            card["distance_km"] = r["distance_km"]
        out.append(card)
    return out


def choices(con) -> dict:
    """What the home screen's filter row offers: modes, towns, the cuisines eligible places have (one spelling per
    folded value), tags, lists and active people."""
    cuisines: dict[str, str] = {}
    for r in con.execute("SELECT cuisine FROM place WHERE ownership IN ('counts', 'unsure') AND standing = 'open' "
                         "AND cuisine IS NOT NULL AND trim(cuisine) <> '' ORDER BY cuisine"):
        cuisines.setdefault(common.fold(r[0]), r[0].strip())
    return {
        "modes": list(MODES),
        "towns": [{"name": t.name} for t in settings.TOWNS],
        "cuisines": [cuisines[k] for k in sorted(cuisines)],
        "tags": [{"id": r["id"], "label": r["label"], "occasion": bool(r["occasion"])}
                 for r in con.execute("SELECT id, label, occasion FROM tag ORDER BY sort_order, id")],
        "lists": [dict(r) for r in con.execute("SELECT id, name FROM source_list ORDER BY name, id")],
        "people": [dict(r) for r in con.execute(
            "SELECT id, name, grp FROM person WHERE active = 1 ORDER BY sort_order, id")],
    }
