"""Curated lists and paste import (spec §8.5). Route-free; every function takes a Connection.

A list is a name (unique folded) and an optional URL. Importing pasted lines turns each into a `list_entry`: matched
to a place or candidate (by folded name, else by Google Text Search IDs-only), or kept as an unmatched line the editor
can retype (`retry`) or `drop`. Nothing skips the inbox: a new id becomes a `list` candidate with status `new`.

Google is asked first and nothing is written until it has answered (google.py: no transaction open while calling
it); the writes then happen in one transaction."""
import json
import math
import re

import common
import db
import google
import settings
from common import ApiError

NAME_MAX = 100
URL_MAX = 500
LINE_MAX = 200
IMPORT_MAX = 200
_BULLET = re.compile(r"^(?:[-*•]|\d+[.)])\s*")
_COMMENT = re.compile(r"\s[-–—]\s|:\s")


def clean_line(line) -> str | None:
    """A pasted line's place name: bullets (- * •) and numbering (1. 1)) stripped, and anything after " - ", " – ",
    " — " or ": " that follows a name dropped. Nothing left is None."""
    s = (line or "").strip()
    while True:
        stripped = _BULLET.sub("", s, count=1).strip()
        if stripped == s:
            break
        s = stripped
    m = _COMMENT.search(s)
    if m and m.start() > 0:
        s = s[:m.start()]
    s = re.sub(r"\s+", " ", s).strip(" \t,;")
    return s or None


# ---------------------------------------------------------------- lists
def _row(r):
    return {"id": r["id"], "name": r["name"], "url": r["url"], "created_at": r["created_at"]}


def _find(con, name):
    key = common.fold(name)
    for r in con.execute("SELECT * FROM source_list ORDER BY id"):
        if common.fold(r["name"]) == key:
            return r
    return None


def _need(con, list_id):
    r = con.execute("SELECT * FROM source_list WHERE id = ?", (list_id,)).fetchone()
    if r is None:
        raise ApiError(404, "That list isn't here.")
    return r


def _check_url(url):
    if url is not None and not re.match(r"https?://\S+$", url):
        raise ApiError(400, "url must start with http:// or https://.")
    return url


def create(con, name, url=None) -> int:
    name = (name or "").strip() if isinstance(name, str) else ""
    if not name:
        raise ApiError(400, "name is required.")
    if len(name) > NAME_MAX:
        raise ApiError(400, f"name must be at most {NAME_MAX} characters.")
    if url is not None and (not isinstance(url, str) or len(url.strip()) > URL_MAX):
        raise ApiError(400, f"url must be text of at most {URL_MAX} characters.")
    url = _check_url((url or "").strip() or None)
    with db.transaction(con):
        if _find(con, name):
            raise ApiError(409, f"There's already a list called {name}.")
        return con.execute("INSERT INTO source_list (name, url, created_at) VALUES (?, ?, ?)",
                           (name, url, common.now())).lastrowid


def get_or_create(con, name, url=None) -> int:
    existing = _find(con, (name or "").strip() if isinstance(name, str) else "")
    return existing["id"] if existing else create(con, name, url)


def all(con) -> list[dict]:
    rows = con.execute(
        "SELECT l.*,"
        " (SELECT COUNT(DISTINCT COALESCE(e.place_id, c.place_id)) FROM list_entry e"
        "    LEFT JOIN candidate c ON c.id = e.candidate_id"
        "    WHERE e.list_id = l.id AND COALESCE(e.place_id, c.place_id) IS NOT NULL) AS places,"
        " (SELECT COUNT(*) FROM list_entry e JOIN candidate c ON c.id = e.candidate_id"
        "    WHERE e.list_id = l.id AND e.place_id IS NULL AND c.place_id IS NULL) AS candidates,"
        " (SELECT COUNT(*) FROM list_entry e WHERE e.list_id = l.id AND e.status = 'unmatched') AS unmatched"
        " FROM source_list l ORDER BY l.name COLLATE NOCASE, l.id").fetchall()
    return [{**_row(r), "places": r["places"], "candidates": r["candidates"], "unmatched": r["unmatched"]}
            for r in rows]


def _wte_name(raw):
    try:
        return (json.loads(raw) or {}).get("name") if raw else None
    except ValueError:
        return None


def get(con, list_id) -> dict:
    """{list, places, candidates, unmatched}. A place is one the list's entries reach (directly, or through a
    candidate that was accepted); a candidate is one not yet a place, named by Google or OSM when it has details, else
    by the pasted line."""
    lst = _need(con, list_id)
    places, candidates, unmatched, seen = [], [], [], set()
    for e in con.execute(
            "SELECT e.id, e.line, e.status, e.candidate_id, COALESCE(e.place_id, c.place_id) AS place_id,"
            " c.status AS cstatus, c.g_name, c.osm_name, c.g_address, c.osm_address, c.wte_json"
            " FROM list_entry e LEFT JOIN candidate c ON c.id = e.candidate_id WHERE e.list_id = ? ORDER BY e.id",
            (list_id,)):
        if e["place_id"] is not None:
            if e["place_id"] in seen:
                continue
            seen.add(e["place_id"])
            p = con.execute("SELECT id, name, town, cuisine, ownership, standing FROM place WHERE id = ?",
                            (e["place_id"],)).fetchone()
            places.append(dict(p))
        elif e["candidate_id"] is not None:
            candidates.append({"id": e["candidate_id"], "entry_id": e["id"], "status": e["cstatus"],
                               "name": e["g_name"] or e["osm_name"] or _wte_name(e["wte_json"]) or e["line"],
                               "address": e["g_address"] or e["osm_address"]})
        else:
            unmatched.append({"id": e["id"], "line": e["line"]})
    return {"list": _row(lst), "places": places, "candidates": candidates, "unmatched": unmatched}


def delete(con, list_id):
    """The list and its entries go; places and candidates stay."""
    _need(con, list_id)
    with db.transaction(con):
        con.execute("DELETE FROM source_list WHERE id = ?", (list_id,))


# ---------------------------------------------------------------- matching
def _rect():
    """(south, west, north, east): the bounding box of every town's scan box."""
    s = w = n = e = None
    for t in settings.TOWNS:
        dlat = t.radius_km / 111.32
        dlng = t.radius_km / (111.32 * math.cos(math.radians(t.lat)))
        s = t.lat - dlat if s is None else min(s, t.lat - dlat)
        n = t.lat + dlat if n is None else max(n, t.lat + dlat)
        w = t.lng - dlng if w is None else min(w, t.lng - dlng)
        e = t.lng + dlng if e is None else max(e, t.lng + dlng)
    return s, w, n, e


class _Names:
    """Folded-name lookups over every place and candidate (word sets equal, so "The Casa Ejemplo Cafe" is "Casa
    Ejemplo"; a subset is not enough, "Taco" must not match every taqueria)."""

    def __init__(self, con):
        self.places, self.candidates = {}, {}
        for r in con.execute("SELECT id, name FROM place ORDER BY id DESC"):
            self.places[common.name_words(r["name"])] = r["id"]
        for r in con.execute("SELECT id, g_name, osm_name, wte_json FROM candidate ORDER BY id DESC"):
            for name in (r["g_name"], r["osm_name"], _wte_name(r["wte_json"])):
                self.candidates[common.name_words(name)] = r["id"]

    def find(self, line):
        words = common.name_words(line)
        if not words:
            return None
        if words in self.places:
            return ("place", self.places[words])
        if words in self.candidates:
            return ("candidate", self.candidates[words])
        return None


def _resolve(con, line, names, state):
    """What a line points at, before anything is written: ("place", id), ("candidate", id), ("gid", id, old_id) or None.
    `state["google_down"]` stops further Google calls after a failure."""
    hit = names.find(line)
    if hit:
        return hit
    if not google.enabled() or state["google_down"]:
        return None
    try:
        ids, moves = google.search_ids(con, text=line, rect=_rect())
    except common.UpstreamError:
        state["google_down"] = True
        return None
    if not ids:
        return None
    return ("gid", moves.get(ids[0], ids[0]), ids[0])


def _target(con, target):
    """Settle a resolved target inside the write transaction: -> ("place", id) | ("candidate", id, created)."""
    kind = target[0]
    if kind == "place":
        return ("place", target[1])
    if kind == "candidate":
        return _candidate_target(con, target[1])
    _, gid, old = target
    p = con.execute("SELECT id FROM place WHERE google_place_id IN (?, ?)", (gid, old)).fetchone()
    if p:
        return ("place", p["id"])
    c = con.execute("SELECT id, google_place_id FROM candidate WHERE google_place_id IN (?, ?) "
                    "ORDER BY google_place_id = ? DESC", (gid, old, gid)).fetchone()
    if c:
        if c["google_place_id"] != gid:
            con.execute("UPDATE candidate SET google_place_id = ? WHERE id = ?", (gid, c["id"]))
        return _candidate_target(con, c["id"])
    cid = con.execute("INSERT INTO candidate (google_place_id, origin, status, first_seen_at) VALUES (?, 'list', 'new', ?)",
                      (gid, common.now())).lastrowid
    return ("candidate", cid, True)


def _candidate_target(con, cid):
    c = con.execute("SELECT status, place_id FROM candidate WHERE id = ?", (cid,)).fetchone()
    if c["status"] == "accepted" and c["place_id"] is not None:
        return ("place", c["place_id"])
    if c["status"] in ("backlog", "dismissed"):
        con.execute("UPDATE candidate SET status = 'new', decided_at = NULL WHERE id = ?", (cid,))
    return ("candidate", cid, False)       # chain_hidden stays hidden; new and pending stay as they are


def _entry_exists(con, list_id, kind, target_id, skip=None):
    column = "place_id" if kind == "place" else "candidate_id"
    return con.execute(f"SELECT 1 FROM list_entry WHERE list_id = ? AND {column} = ? AND id IS NOT ?",
                       (list_id, target_id, skip)).fetchone() is not None


def _write(con, list_id, line, target, counts, entry_id=None):
    """Record one line against its resolved target (or unmatched), and count it. With entry_id, rewrite that existing
    unmatched entry instead of adding one."""
    if target is None:
        counts["unmatched"] += 1
        counts["unmatched_lines"].append(line)
        if entry_id is not None:
            con.execute("UPDATE list_entry SET line = ? WHERE id = ?", (line, entry_id))
        elif not con.execute("SELECT 1 FROM list_entry WHERE list_id = ? AND status = 'unmatched' AND line = ?",
                             (list_id, line)).fetchone():
            con.execute("INSERT INTO list_entry (list_id, line, status, created_at) VALUES (?, ?, 'unmatched', ?)",
                        (list_id, line, common.now()))
        return
    settled = _target(con, target)
    kind, target_id = settled[0], settled[1]
    if kind == "place":
        counts["matched_places"] += 1
    elif len(settled) > 2 and settled[2]:
        counts["new_candidates"] += 1
    else:
        counts["linked_candidates"] += 1
    if _entry_exists(con, list_id, kind, target_id, entry_id):
        if entry_id is not None:
            con.execute("DELETE FROM list_entry WHERE id = ?", (entry_id,))
        return
    place_id, cand_id = (target_id, None) if kind == "place" else (None, target_id)
    if entry_id is not None:
        con.execute("UPDATE list_entry SET line = ?, status = 'matched', place_id = ?, candidate_id = ? WHERE id = ?",
                    (line, place_id, cand_id, entry_id))
    else:
        con.execute("INSERT INTO list_entry (list_id, line, candidate_id, place_id, status, created_at) "
                    "VALUES (?, ?, ?, ?, 'matched', ?)", (list_id, line, cand_id, place_id, common.now()))


def _counts():
    return {"matched_places": 0, "new_candidates": 0, "linked_candidates": 0, "unmatched": 0, "unmatched_lines": []}


def import_lines(con, list_id, lines) -> dict:
    """Import pasted lines (at most 200). Returns the counts and the unmatched lines. A line already on the list is not
    added twice, but still counts in the result."""
    _need(con, list_id)
    if not isinstance(lines, list) or any(not isinstance(x, str) for x in lines):
        raise ApiError(400, "lines must be a list of text lines.")
    cleaned, seen = [], set()
    for raw in lines:
        line = clean_line(raw)
        if line is None:
            continue
        if len(line) > LINE_MAX:
            raise ApiError(400, f"A line is longer than {LINE_MAX} characters: {line[:30]}...")
        cleaned.append(line)
    if len(cleaned) > IMPORT_MAX:
        raise ApiError(400, f"Paste at most {IMPORT_MAX} lines at a time.")
    unique = [x for x in cleaned if not (common.fold(x) in seen or seen.add(common.fold(x)))]
    names, state = _Names(con), {"google_down": False}
    resolved = [(line, _resolve(con, line, names, state)) for line in unique]        # Google first, no transaction
    counts = _counts()
    with db.transaction(con):
        for line, target in resolved:
            _write(con, list_id, line, target, counts)
    return counts


def _entry(con, entry_id):
    e = con.execute("SELECT * FROM list_entry WHERE id = ?", (entry_id,)).fetchone()
    if e is None:
        raise ApiError(404, "That line isn't here.")
    return e


def retry(con, entry_id, text) -> dict:
    """Search again for an unmatched line, with the editor's corrected text. Same result shape as import_lines."""
    e = _entry(con, entry_id)
    if e["status"] != "unmatched":
        raise ApiError(409, "That line is already matched.")
    line = clean_line(text)
    if line is None:
        raise ApiError(400, "Type the place's name.")
    if len(line) > LINE_MAX:
        raise ApiError(400, f"text must be at most {LINE_MAX} characters.")
    target = _resolve(con, line, _Names(con), {"google_down": False})
    counts = _counts()
    with db.transaction(con):
        if _entry(con, entry_id)["status"] != "unmatched":
            raise ApiError(409, "That line is already matched.")
        _write(con, e["list_id"], line, target, counts, entry_id=entry_id)
    return counts


def drop(con, entry_id):
    _entry(con, entry_id)
    with db.transaction(con):
        con.execute("DELETE FROM list_entry WHERE id = ?", (entry_id,))
