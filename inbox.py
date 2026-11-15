"""The inbox (spec §8.3), closure flags (§8.2 step 5) and the add-by-hand lookup (§8.4). Route-free; every function
takes a sqlite3.Connection.

Google's terms shape this module. A candidate's g_* columns are a cache of at most 30 days (google.tidy clears them);
a place's stored fields never come from it. Accepting builds the place from the OSM match (or a Where to Eat
candidate's own fields) plus what the editor typed. A Google name or address is used only when the request says
{"from_google": true} for that field and GOOGLE_PREFILL is on: the editor tapped "Use this". The pin never comes from
Google: it is OSM's, or the geocoded confirmed address (Nominatim, then Photon), or none.

Every Google call here runs with no db.transaction open (google.py's counter opens its own), and only for what is on
screen: an inbox row with no OSM match whose Google cache is empty or expired, or the add-by-hand lookup's top three."""
import json
import math

import common
import db
import google
import osm
import places
import settings
from common import ApiError

TABS = ("new", "backlog", "chain_hidden", "flags")
PAGE = 20
PAGE_MAX = 50
OFFSET_MAX = 1_000_000
CAP_NOTE = "Google details wait for next month."
GOOGLE_DOWN = "Google didn't answer. Reload to try again."
OSM_DOWN = "OpenStreetMap didn't answer. Try again in a minute."
FLAG_REASON = "Google lists it as closed"
FLAG_STANDING = {"CLOSED_PERMANENTLY": "closed", "CLOSED_TEMPORARILY": "temp_closed"}
# what an editor can set while accepting; the place gets the rest from the candidate
FIELDS = ("name", "town", "address", "lat", "lng", "cuisine", "phone", "website", "hours", "notes")
GOOGLE_FIELDS = ("name", "address")   # "Use this" offers text only; the pin is OSM's or the geocoded address
NEAR_KM = 0.15
LOOKUP_GOOGLE = 3
KM_PER_DEG_LAT = 111.32


# ---------------------------------------------------------------- the area
def town_box(town) -> tuple[float, float, float, float]:
    """(south, west, north, east): the town's centre +- its radius each way, as the scan boxes it."""
    dlat = town.radius_km / KM_PER_DEG_LAT
    dlng = town.radius_km / (KM_PER_DEG_LAT * math.cos(math.radians(town.lat)))
    return (town.lat - dlat, town.lng - dlng, town.lat + dlat, town.lng + dlng)


def area_box() -> tuple[float, float, float, float]:
    """The box holding every town's box."""
    boxes = [town_box(t) for t in settings.TOWNS]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def find_town(name):
    """The settings.TOWNS entry named `name` (case and accents ignored); None for no name; 400 for an unknown one."""
    if not (name or "").strip():
        return None
    for t in settings.TOWNS:
        if common.fold(t.name) == common.fold(name):
            return t
    raise ApiError(400, f"Pick one of the towns: {', '.join(t.name for t in settings.TOWNS)}.")


# ---------------------------------------------------------------- reading a candidate
def _fresh(c) -> bool:
    """The candidate's Google cache is there and younger than Google's 30 days."""
    return c["g_fetched_at"] is not None and c["g_fetched_at"] > common.now() - google.TIDY_DAYS * 86400


def _osm(c) -> dict | None:
    if not c["osm_ref"] and not c["osm_name"]:
        return None
    try:
        extra = json.loads(c["osm_extra"] or "{}")
    except ValueError:
        extra = {}
    extra = extra if isinstance(extra, dict) else {}
    return {"ref": c["osm_ref"], "name": c["osm_name"], "address": c["osm_address"], "lat": c["osm_lat"],
            "lng": c["osm_lng"], "cuisine": extra.get("cuisine"), "phone": extra.get("phone"),
            "website": extra.get("website"), "hours": extra.get("opening_hours") or extra.get("hours")}


def _wte(c) -> dict | None:
    if not c["wte_json"]:
        return None
    try:
        w = json.loads(c["wte_json"])
    except ValueError:
        return None
    return w if isinstance(w, dict) else None


def _google(c) -> dict | None:
    if not _fresh(c):
        return None
    return {"name": c["g_name"], "address": c["g_address"], "lat": c["g_lat"], "lng": c["g_lng"],
            "status": c["g_status"], "type": c["g_type"]}


def _sources(c, osm_part, wte_part):
    """The candidate's own sources in the order its fields come from: a Where to Eat candidate's fields first."""
    named = [("osm", osm_part), ("where_to_eat", wte_part)]
    if c["origin"] == "where_to_eat":
        named.reverse()
    return [(n, s) for n, s in named if s]


def _base(c) -> dict:
    """The place fields a candidate brings with it: OSM's match, or a Where to Eat candidate's own fields, never
    Google's."""
    out = dict.fromkeys(FIELDS)
    out.pop("notes")
    sources = _sources(c, _osm(c), _wte(c))
    for _name, src in reversed(sources):
        for key in out:
            if src.get(key) not in (None, ""):
                out[key] = src[key]
    out["lat"], out["lng"] = _pin_of(sources)
    return out


def _pin_of(sources) -> tuple:
    """The first source's pin that has both halves (never half of one and half of another), else (None, None)."""
    return next(((s["lat"], s["lng"]) for _n, s in sources if s.get("lat") is not None and s.get("lng") is not None),
                (None, None))


def _lists(con, candidate_id) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT DISTINCT l.id, l.name FROM list_entry e JOIN source_list l ON l.id = e.list_id "
        "WHERE e.candidate_id = ? ORDER BY l.name, l.id", (candidate_id,))]


def _row(con, c, note=None) -> dict:
    osm_part, wte_part, g_part = _osm(c), _wte(c), _google(c)
    shown = _sources(c, osm_part, wte_part) + ([("google", g_part)] if g_part else [])

    def pick(key):
        return next(((src.get(key), n) for n, src in shown if src.get(key) not in (None, "")), (None, None))

    name, name_from = pick("name")
    address, address_from = pick("address")
    lat, lng = _pin_of(shown)
    return {"id": c["id"], "status": c["status"], "origin": c["origin"], "first_seen_at": c["first_seen_at"],
            "google_place_id": c["google_place_id"], "osm_match": bool(c["osm_ref"]), "chain_hint": bool(c["chain_hint"]),
            "name": name, "name_from": name_from, "address": address, "address_from": address_from,
            "town": common.town_for(address, lat, lng), "type": g_part["type"] if g_part else None,
            "osm": osm_part, "wte": wte_part, "google": g_part, "lists": _lists(con, c["id"]), "note": note}


def _store_google(con, candidate_id, d):
    """Cache a details answer on the candidate (google.tidy clears it after 30 days). None (404) caches the miss."""
    d = d or {}
    with db.transaction(con):
        con.execute("UPDATE candidate SET g_name = ?, g_address = ?, g_lat = ?, g_lng = ?, g_status = ?, g_type = ?, "
                    "g_fetched_at = ? WHERE id = ?", (d.get("name"), d.get("address"), d.get("lat"), d.get("lng"),
                                                      d.get("status"), d.get("type"), common.now(), candidate_id))
    return con.execute("SELECT * FROM candidate WHERE id = ?", (candidate_id,)).fetchone()


def _needs_google(c) -> bool:
    return google.enabled() and bool(c["google_place_id"]) and not c["osm_ref"] and not _fresh(c)


# ---------------------------------------------------------------- the tabs
def counts(con) -> dict:
    out = {"new": 0, "backlog": 0, "flags": 0, "chain_hidden": 0}
    for status, n in con.execute("SELECT status, COUNT(*) FROM candidate WHERE status IN ('new', 'backlog', "
                                 "'chain_hidden') GROUP BY status"):
        out[status] = n
    out["flags"] = con.execute("SELECT COUNT(*) FROM place WHERE closure_flag IS NOT NULL").fetchone()[0]
    return out


def _flag_row(p) -> dict:
    return {"id": p["id"], "place_id": p["id"], "name": p["name"], "town": p["town"], "address": p["address"],
            "standing": p["standing"], "closure_flag": p["closure_flag"], "closure_checked_at": p["closure_checked_at"]}


def rows(con, tab, offset=0, limit=PAGE) -> dict:
    """One page of a tab: `new`, `backlog`, `chain_hidden` (candidates, newest first) or `flags` (places Google lists
    as closed). `pending`, `dismissed` and `accepted` candidates never show. A row on this page with no OSM match whose
    Google cache is empty or expired is re-fetched (one counted Pro call each); at the monthly cap it keeps its id and
    says CAP_NOTE."""
    if tab not in TABS:
        raise ApiError(400, f"tab must be one of: {', '.join(TABS)}.")
    tally = counts(con)
    if tab == "flags":
        found = con.execute("SELECT * FROM place WHERE closure_flag IS NOT NULL ORDER BY closure_checked_at DESC, id DESC "
                            "LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return {"tab": tab, "total": tally["flags"], "rows": [_flag_row(p) for p in found], "counts": tally}
    found = con.execute("SELECT * FROM candidate WHERE status = ? ORDER BY first_seen_at DESC, id DESC LIMIT ? OFFSET ?",
                        (tab, limit, offset)).fetchall()
    out, capped = [], False
    for c in found:
        note = None
        if _needs_google(c):
            if capped:
                note = CAP_NOTE
            else:
                try:
                    c = _store_google(con, c["id"], google.details(con, c["google_place_id"]))
                except google.CapReached:
                    capped, note = True, CAP_NOTE
                except common.UpstreamError:
                    note = GOOGLE_DOWN
        out.append(_row(con, c, note))
    return {"tab": tab, "total": tally[tab], "rows": out, "counts": tally}


# ---------------------------------------------------------------- sorting a candidate
def _candidate(con, candidate_id):
    c = con.execute("SELECT * FROM candidate WHERE id = ?", (candidate_id,)).fetchone()
    if c is None:
        raise ApiError(404, "That inbox entry isn't there any more.")
    return c


def _live_google(con, c) -> dict:
    """The Google name and address for "Use this": the cache while fresh, else a live (counted) details call."""
    if _fresh(c):
        return {"name": c["g_name"], "address": c["g_address"]}
    if not google.enabled() or not c["google_place_id"]:
        return {}
    try:
        d = google.details(con, c["google_place_id"])
    except google.CapReached:
        raise ApiError(409, f"{CAP_NOTE} Type it in instead.") from None
    except common.UpstreamError:
        raise ApiError(502, "Google didn't answer. Try again, or type it in.") from None
    _store_google(con, c["id"], d)
    return d or {}


def _overrides(con, c, data, fields) -> None:
    """Apply what the editor sent for each field: a value (typed; "" or null clears it) or {"from_google": true}."""
    live = None
    for key in FIELDS:
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, dict):
            if value.get("from_google") is not True:
                raise ApiError(400, f'{key} must be a value or {{"from_google": true}}.')
            if key not in GOOGLE_FIELDS:
                raise ApiError(400, "Only the name and address can come from Google Maps. The pin comes from "
                                    "OpenStreetMap or the address.")
            if not settings.GOOGLE_PREFILL:
                raise ApiError(400, "Google values are turned off here (GOOGLE_PREFILL). Type it in.")
            if live is None:
                live = _live_google(con, c)
            if not live.get(key):
                raise ApiError(400, f"Google Maps has no {key} for this place. Type it in.")
            value = live[key]
        fields[key] = value


def _pin(fields) -> None:
    """No pin from OSM or Where to Eat: geocode the confirmed address. A geocoder that is down leaves it without one."""
    if fields.get("lat") is not None and fields.get("lng") is not None:
        return
    address = common.text(fields, "address", places.TEXT_FIELDS["address"])
    town = common.text(fields, "town", places.TEXT_FIELDS["town"]) or common.town_for(address, None, None)
    try:
        found = osm.geocode(address, town) if address else None
    except common.UpstreamError:
        found = None
    fields["lat"], fields["lng"] = found if found else (None, None)


def accept(con, candidate_id, data, by) -> int:
    """Make a place from a candidate with the editor's verdict (`ownership`, `ownership_reason`), optional
    `want_to_try`, `tags` (tag ids) and field overrides (see _overrides). `add_anyway` passes a duplicate warning.
    The candidate becomes `accepted` with `place_id`, and its list entries move to the place. `ownership` "no" still
    makes the place (straight into Past places), so the next scan doesn't offer it again. Returns the place id."""
    c = _candidate(con, candidate_id)
    if c["status"] == "accepted":
        raise ApiError(409, "That one is already on the list.", place_id=c["place_id"])
    if data.get("ownership") not in places.OWNERSHIP:
        raise ApiError(400, f"ownership must be one of: {', '.join(places.OWNERSHIP)}.")
    reason = common.text(data, "ownership_reason", 200, required=True)
    fields = _base(c)
    _overrides(con, c, data, fields)
    if not isinstance(fields.get("name"), str) or not fields["name"].strip():
        raise ApiError(400, "name is required.")
    _pin(fields)
    body = {**fields, "ownership": data["ownership"], "ownership_reason": reason,
            "want_to_try": bool(data.get("want_to_try")), "tag_ids": data.get("tags", data.get("tag_ids", [])),
            "add_anyway": bool(data.get("add_anyway")), "origin": c["origin"], "first_seen_at": c["first_seen_at"],
            "google_place_id": c["google_place_id"], "osm_ref": c["osm_ref"], "wte_id": c["wte_id"]}
    # Claim the candidate first, so two editors accepting it at once make one place.
    with db.transaction(con):
        claimed = con.execute("UPDATE candidate SET status = 'accepted', decided_at = ? WHERE id = ? AND status = ?",
                              (common.now(), candidate_id, c["status"])).rowcount
    if not claimed:
        raise ApiError(409, "Someone else just sorted this one. Reload the inbox.")
    try:
        place_id = places.create(con, body, by)
    except BaseException:
        with db.transaction(con):
            con.execute("UPDATE candidate SET status = ?, decided_at = ? WHERE id = ?",
                        (c["status"], c["decided_at"], candidate_id))
        raise
    with db.transaction(con):
        con.execute("UPDATE candidate SET place_id = ? WHERE id = ?", (place_id, candidate_id))
        con.execute("UPDATE list_entry SET place_id = ? WHERE candidate_id = ?", (place_id, candidate_id))
    return place_id


def dismiss(con, candidate_id) -> dict:
    """Not a restaurant, or not ours: `dismissed`, never shown again unless someone looks it up."""
    with db.transaction(con):
        c = _candidate(con, candidate_id)
        if c["status"] == "accepted":
            raise ApiError(409, "That one is already on the list.", place_id=c["place_id"])
        con.execute("UPDATE candidate SET status = 'dismissed', decided_at = ? WHERE id = ?", (common.now(), candidate_id))
    return {"id": candidate_id, "status": "dismissed"}


def unhide(con, candidate_id) -> dict:
    """A candidate hidden as a chain that isn't one goes to New."""
    with db.transaction(con):
        c = _candidate(con, candidate_id)
        if c["status"] != "chain_hidden":
            raise ApiError(409, "Only a place hidden as a chain can be un-hidden.")
        con.execute("UPDATE candidate SET status = 'new', decided_at = NULL WHERE id = ?", (candidate_id,))
    return {"id": candidate_id, "status": "new"}


# ---------------------------------------------------------------- closure flags
def _flag(con, place_id) -> str:
    row = con.execute("SELECT closure_flag FROM place WHERE id = ?", (place_id,)).fetchone()
    if row is None:
        raise ApiError(404, "Place not found.")
    if not row["closure_flag"]:
        raise ApiError(409, "This place has no closure flag.")
    return row["closure_flag"]


def confirm_flag(con, place_id, by) -> dict:
    """Google is right: set the standing (closed, or temp_closed for a temporary closure) with the history line
    FLAG_REASON. set_standing clears the flag."""
    flag = _flag(con, place_id)
    return places.set_standing(con, place_id, FLAG_STANDING[flag], FLAG_REASON, by)


def dismiss_flag(con, place_id) -> dict:
    """Google is wrong: clear the flag and remember the status, so the scan doesn't raise it again until Google's
    status changes."""
    with db.transaction(con):
        flag = _flag(con, place_id)
        con.execute("UPDATE place SET closure_dismissed = ?, closure_flag = NULL, updated_at = ? WHERE id = ?",
                    (flag, common.now(), place_id))
    return places.get(con, place_id)


# ---------------------------------------------------------------- add by hand
def _near(name, lat, lng, other_name, other_lat, other_lng) -> bool:
    return None not in (lat, lng, other_lat, other_lng) \
        and common.km_between(lat, lng, other_lat, other_lng) <= NEAR_KM and common.names_match(name, other_name)


def _same(r, ref, gid, pins) -> bool:
    """A lookup result is the same place as a place or candidate with this OSM ref, Google id and named pins."""
    return bool((r["osm_ref"] and ref == r["osm_ref"]) or (r["google_place_id"] and gid == r["google_place_id"])
                or any(_near(r["name"], r["lat"], r["lng"], *pin) for pin in pins))


def _mark(con, results) -> None:
    """Set `in_list` (an existing place's id) and `in_inbox` / `inbox_status` (a candidate not yet accepted) on each
    lookup result: same OSM element, same Google id, or a matching name within 150 m."""
    known = con.execute("SELECT id, name, lat, lng, osm_ref, google_place_id FROM place").fetchall()
    waiting = []
    for c in con.execute("SELECT * FROM candidate WHERE status <> 'accepted'").fetchall():
        pins = [(c["osm_name"], c["osm_lat"], c["osm_lng"]), (c["g_name"], c["g_lat"], c["g_lng"])]
        w = _wte(c)
        if w:
            pins.append((w.get("name"), w.get("lat"), w.get("lng")))
        waiting.append((c, pins))
    for r in results:
        hit = next((p for p in known if _same(r, p["osm_ref"], p["google_place_id"], [(p["name"], p["lat"], p["lng"])])),
                   None)
        r["in_list"] = hit["id"] if hit else None
        cand = next((c for c, pins in waiting if _same(r, c["osm_ref"], c["google_place_id"], pins)), None)
        r["in_inbox"] = cand["id"] if cand else None
        r["inbox_status"] = cand["status"] if cand else None


def _osm_result(p) -> dict:
    return {"source": "osm", "name": p["name"], "address": p["address"], "lat": p["lat"], "lng": p["lng"],
            "town": common.town_for(p["address"], p["lat"], p["lng"]), "cuisine": p["cuisine"], "phone": p["phone"],
            "website": p["website"], "hours": p["opening_hours"], "osm_ref": p["ref"], "google_place_id": None,
            "is_chain": p["is_chain"], "google": None}


def _google_result(gid, d) -> dict:
    return {"source": "google", "name": d["name"], "address": d["address"], "lat": d["lat"], "lng": d["lng"],
            "town": common.town_for(d["address"], d["lat"], d["lng"]), "cuisine": None, "phone": None,
            "website": None, "hours": None, "osm_ref": None, "google_place_id": gid, "is_chain": False,
            "google": {"name": d["name"], "address": d["address"], "status": d["status"], "type": d["type"]}}


def lookup(con, name, town=None) -> dict:
    """Add place's "look it up": OSM (one Nominatim search in the town's box, or the whole area's), plus, with a key,
    a Google Text Search (IDs only) and details for the top three (counted). A Google result near an OSM result with a
    matching name is the same place: the OSM result gains its id. Returns {"results", "notes"}; a service that fails
    or the cap adds a note rather than failing the lookup."""
    name = common.text({"name": name}, "name", places.TEXT_FIELDS["name"], required=True)
    box = town_box(t) if (t := find_town(town)) else area_box()
    notes = []
    try:
        results = [_osm_result(p) for p in osm.search(name, box)]
    except common.UpstreamError:
        results = []
        notes.append(OSM_DOWN)
    if google.enabled():
        try:
            ids, moves = google.search_ids(con, text=name, rect=box)
        except common.UpstreamError:
            ids, moves = [], {}
            notes.append(GOOGLE_DOWN)
        for gid in [moves.get(i, i) for i in ids][:LOOKUP_GOOGLE]:
            try:
                d = google.details(con, gid)
            except google.CapReached:
                notes.append(CAP_NOTE)
                break
            except common.UpstreamError:
                if GOOGLE_DOWN not in notes:
                    notes.append(GOOGLE_DOWN)
                continue
            if not d or not d.get("name") or d.get("lat") is None or d.get("lng") is None:
                continue
            g = _google_result(gid, d)
            same = osm.match(d["name"], d["lat"], d["lng"],
                             [r for r in results if r["source"] == "osm" and not r["google_place_id"]])
            if same:
                same["google_place_id"], same["google"] = gid, g["google"]
            else:
                results.append(g)
    _mark(con, results)
    return {"results": results, "notes": notes}
