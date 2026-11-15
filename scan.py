"""The weekly discovery scan (spec §8.2), run by `python3 manage.py scan` from ops/hometown-scan.

In order:
1. Search: every town box is cut into square tiles of SCAN_TILE_KM; each tile x SCAN_TYPES is one IDs-only Text
   Search (free, never capped). A tile that returns Google's 60-result limit is split in four and searched again, as
   long as a quarter is at least SCAN_MIN_TILE_KM.
2. Moves (`movedPlaceId`) update the id stored on a place or candidate; every id not already a place or a candidate
   becomes a `pending` candidate (one transaction).
3. Sort: `pending` candidates, newest first, get Place Details (Pro, counted against the monthly cap) until
   `google.CapReached`; whatever is left stays `pending`, hidden from the inbox, for a later run (Review Focus 2). A
   pending candidate whose details are already cached (a run that failed after buying them) is not fetched again.
   Each is matched to OSM (one Overpass query per town box per run, made only when a candidate needs it), then
   attached to an id-less place or `where_to_eat` candidate within 150 m with a matching name, or else sorted:
   `chain_hidden` when OSM says it is a brand, `backlog` when it was first seen before `meta.baseline_at` (or there is
   no baseline yet), else `new`.
4. Closure check: places with a Google id not checked in CLOSURE_CHECK_DAYS get Details; a closed status sets
   `closure_flag` (never the standing: an editor confirms it from the inbox).
5. The `scan_run` row gets the counts; the first run that finishes sets `meta.baseline_at`. Any error is recorded in
   `scan_run.error` and re-raised; everything written before it stays, so the next run carries on from there.

Google calls are never made inside a `db.transaction` (google.py's counter opens its own). OSM's servers are shared:
Overpass queries are paced by OSM_PAUSE_S and a busy one is retried once after OSM_RETRY_S; `sleep` is a module
attribute so tests replace it. Settings are read at call time."""
import json
import math
import time

import common
import db
import google
import osm
import settings

KM_PER_DEG = 111.32          # km per degree of latitude (and of longitude at the equator)
FULL = google.MAX_PAGES * 20  # Text Search never returns more than 60: a tile that returns this many may hold more
ATTACH_KM = 0.15             # a scan id and a place or Where to Eat candidate are the same within 150 m
CLOSED = ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY")
CHECK_SLACK_S = 12 * 3600    # a weekly cron starts a little later or earlier each time: 28 days stays 4 runs, not 5
OSM_PAUSE_S = 1.0            # between two Overpass queries in one run
OSM_RETRY_S = 30.0           # before retrying a busy Overpass query once
ERROR_MAX = 500
sleep = time.sleep


# ---------------------------------------------------------------- tiles
def box(town) -> tuple[float, float, float, float]:
    """A town's scan box, (south, west, north, east): its centre +- radius_km each way."""
    dlat = town.radius_km / KM_PER_DEG
    dlng = town.radius_km / (KM_PER_DEG * math.cos(math.radians(town.lat)))
    return (round(town.lat - dlat, 6), round(town.lng - dlng, 6), round(town.lat + dlat, 6), round(town.lng + dlng, 6))


def tiles(town, tile_km) -> list[tuple[float, float, float, float]]:
    """The town box cut into k x k equal squares, k the fewest that keeps a side at most tile_km. Neighbours share
    their edges exactly, so the tiles cover the box with no gap."""
    s, w, n, e = box(town)
    k = max(1, math.ceil(2 * town.radius_km / tile_km - 1e-9))
    lats = [round(s + (n - s) * i / k, 6) for i in range(k)] + [n]
    lngs = [round(w + (e - w) * j / k, 6) for j in range(k)] + [e]
    return [(lats[i], lngs[j], lats[i + 1], lngs[j + 1]) for i in range(k) for j in range(k)]


def split(rect) -> list[tuple[float, float, float, float]]:
    """The four quarters of a rectangle."""
    s, w, n, e = rect
    mlat, mlng = round((s + n) / 2, 6), round((w + e) / 2, 6)
    return [(s, w, mlat, mlng), (s, mlng, mlat, e), (mlat, w, n, mlng), (mlat, mlng, n, e)]


def side_km(rect) -> float:
    """A tile's side in km (tiles are square, so its north-south extent)."""
    return (rect[2] - rect[0]) * KM_PER_DEG


def _inside(rect, lat, lng, margin_km=0.0) -> bool:
    s, w, n, e = rect
    dlat = margin_km / KM_PER_DEG
    dlng = margin_km / (KM_PER_DEG * math.cos(math.radians(lat)))
    return s - dlat <= lat <= n + dlat and w - dlng <= lng <= e + dlng


# ---------------------------------------------------------------- closure flags
def place_closure_flag(con, place_id, status) -> None:
    """Apply a Google business status to a place's closure flag (never its standing). CLOSED_PERMANENTLY or
    CLOSED_TEMPORARILY sets the flag, unless an editor dismissed that same status or the standing already says so
    (closed; or temp_closed for a temporary closure). OPERATIONAL clears the dismissal, so a later closure is flagged
    again, and a flag nobody confirmed. Anything else changes nothing. One statement: safe inside or outside a
    transaction. The place's version is not bumped (an editor's open form stays valid)."""
    if status in CLOSED:
        settled = ("closed",) if status == "CLOSED_PERMANENTLY" else ("temp_closed", "closed")
        marks = ", ".join("?" * len(settled))
        con.execute(f"UPDATE place SET closure_flag = ? WHERE id = ? AND closure_dismissed IS NOT ? "
                    f"AND standing NOT IN ({marks})", (status, place_id, status, *settled))
    elif status == "OPERATIONAL":
        con.execute("UPDATE place SET closure_flag = NULL, closure_dismissed = NULL WHERE id = ?", (place_id,))


# ---------------------------------------------------------------- the run
class _Run:
    def __init__(self, con, started):
        self.con = con
        self.started = started
        self.tiles = self.search_calls = self.detail_calls = 0
        self.inserted, self.removed = set(), set()
        self.capped = False
        self.food = {}           # town name -> osm.food_places of its box, fetched once per run when needed
        self.overpass_calls = 0
        row = con.execute("SELECT value FROM meta WHERE key = 'baseline_at'").fetchone()
        self.baseline = int(row[0]) if row else None

    # -- step 1 and 2
    def _id_calls(self):
        u = google.usage(self.con)
        return u["month"], u["id_calls"]

    def _search(self, rect, kind, found, moves):
        month, before = self._id_calls()
        ids, moved = google.search_ids(self.con, text=kind.replace("_", " "), rect=rect, included_type=kind)
        after_month, after = self._id_calls()
        self.tiles += 1
        self.search_calls += after - before if after_month == month else after
        found.update(dict.fromkeys(ids))
        moves.update(moved)
        if len(ids) >= FULL and side_km(rect) / 2 >= settings.SCAN_MIN_TILE_KM - 1e-3:
            for quarter in split(rect):
                self._search(quarter, kind, found, moves)

    def search(self):
        found, moves = {}, {}
        for town in settings.TOWNS:
            for rect in tiles(town, settings.SCAN_TILE_KM):
                for kind in settings.SCAN_TYPES:
                    self._search(rect, kind, found, moves)
        con = self.con
        with db.transaction(con):
            for old, new in moves.items():
                if not con.execute("SELECT 1 FROM place WHERE google_place_id = ?", (new,)).fetchone():
                    con.execute("UPDATE place SET google_place_id = ? WHERE google_place_id = ?", (new, old))
                    if not con.execute("SELECT 1 FROM candidate WHERE google_place_id = ?", (new,)).fetchone():
                        con.execute("UPDATE candidate SET google_place_id = ? WHERE google_place_id = ?", (new, old))
            for gid in dict.fromkeys(moves.get(g, g) for g in found):
                if con.execute("SELECT 1 FROM place WHERE google_place_id = ? UNION ALL "
                               "SELECT 1 FROM candidate WHERE google_place_id = ?", (gid, gid)).fetchone():
                    continue
                self.inserted.add(con.execute(
                    "INSERT INTO candidate (google_place_id, origin, status, first_seen_at) "
                    "VALUES (?, 'scan', 'pending', ?)", (gid, self.started)).lastrowid)

    # -- step 3
    def _details(self, gid):
        """google.details, counted in this run's detail_calls; None for an id Google doesn't know. CapReached (no call
        made) is passed on and stops every later Pro call of this run."""
        try:
            d = google.details(self.con, gid)
        except google.CapReached:
            self.capped = True
            raise
        except Exception:
            self.detail_calls += 1   # made, and counted by google.py
            raise
        self.detail_calls += 1
        return d

    def _food_places(self, town):
        if town.name not in self.food:
            if self.overpass_calls:
                sleep(OSM_PAUSE_S)
            self.overpass_calls += 1
            try:
                self.food[town.name] = osm.food_places(box(town))
            except common.UpstreamError:
                sleep(OSM_RETRY_S)
                self.food[town.name] = osm.food_places(box(town))
        return self.food[town.name]

    def _osm_match(self, name, lat, lng):
        if not name or lat is None or lng is None:
            return None
        elements = [el for town in settings.TOWNS if _inside(box(town), lat, lng, osm.MATCH_KM)
                    for el in self._food_places(town)]
        return osm.match(name, lat, lng, elements)

    def sort(self):
        con = self.con
        rows = con.execute("SELECT * FROM candidate WHERE status = 'pending' AND google_place_id IS NOT NULL "
                           "ORDER BY first_seen_at DESC, id DESC").fetchall()
        for c in rows:
            if c["g_fetched_at"] is not None:
                g = {"name": c["g_name"], "address": c["g_address"], "lat": c["g_lat"], "lng": c["g_lng"],
                     "status": c["g_status"], "type": c["g_type"]}
                fetched = c["g_fetched_at"]
            else:
                try:
                    g = self._details(c["google_place_id"])
                except google.CapReached:
                    return
                if g is None:   # Google no longer knows the id
                    con.execute("DELETE FROM candidate WHERE id = ? AND status = 'pending'", (c["id"],))
                    self.removed.add(c["id"])
                    continue
                fetched = common.now()
                con.execute("UPDATE candidate SET g_name = ?, g_address = ?, g_lat = ?, g_lng = ?, g_status = ?, "
                            "g_type = ?, g_fetched_at = ? WHERE id = ?",
                            (g["name"], g["address"], g["lat"], g["lng"], g["status"], g["type"], fetched, c["id"]))
            el = self._osm_match(g["name"], g["lat"], g["lng"])
            with db.transaction(con):
                now = con.execute("SELECT status FROM candidate WHERE id = ?", (c["id"],)).fetchone()
                if now is None or now["status"] != "pending":
                    continue   # someone sorted it meanwhile (a list import, say)
                if self._attach(c, g, el, fetched):
                    self.removed.add(c["id"])
                    continue
                if el and el["is_chain"]:
                    status = "chain_hidden"
                elif self.baseline is None or c["first_seen_at"] <= self.baseline:
                    status = "backlog"
                else:
                    status = "new"
                con.execute(f"UPDATE candidate SET status = ?, {_OSM_SET} WHERE id = ?",
                            (status, *_osm_values(el), c["id"]))

    def _attach(self, c, g, el, fetched) -> bool:
        """Give this id to an id-less place, else an id-less Where to Eat candidate, within 150 m with a matching
        name (the nearest), and drop the pending row. Inside the caller's transaction."""
        con, lat, lng = self.con, g["lat"], g["lng"]
        if lat is None or lng is None or not g["name"]:
            return False
        dlat = ATTACH_KM / KM_PER_DEG
        dlng = ATTACH_KM / (KM_PER_DEG * math.cos(math.radians(lat)))
        places = con.execute("SELECT id, name, lat, lng FROM place WHERE google_place_id IS NULL "
                             "AND lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?",
                             (lat - dlat, lat + dlat, lng - dlng, lng + dlng)).fetchall()
        place = _nearest(g, [(p["id"], p["name"], p["lat"], p["lng"]) for p in places])
        if place is not None:
            con.execute("DELETE FROM candidate WHERE id = ?", (c["id"],))
            con.execute("UPDATE place SET google_place_id = ?, osm_ref = COALESCE(osm_ref, ?), closure_checked_at = ? "
                        "WHERE id = ?", (c["google_place_id"], el["ref"] if el else None, fetched, place))
            place_closure_flag(con, place, g["status"])
            return True
        wte = []
        for r in con.execute("SELECT id, wte_json FROM candidate WHERE origin = 'where_to_eat' AND "
                             "google_place_id IS NULL AND status <> 'accepted' AND wte_json IS NOT NULL"):
            try:
                w = json.loads(r["wte_json"])
                wte.append((r["id"], w.get("name"), float(w["lat"]), float(w["lng"])))
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
        other = _nearest(g, wte)
        if other is None:
            return False
        con.execute("DELETE FROM candidate WHERE id = ?", (c["id"],))
        sets = "google_place_id = ?, g_name = ?, g_address = ?, g_lat = ?, g_lng = ?, g_status = ?, g_type = ?, " \
               "g_fetched_at = ?"
        values = [c["google_place_id"], g["name"], g["address"], lat, lng, g["status"], g["type"], fetched]
        if el:   # keep its status: Where to Eat already listed it, so it is not hidden as a chain, only hinted
            sets += f", {_OSM_SET}"
            values += _osm_values(el)
        con.execute(f"UPDATE candidate SET {sets} WHERE id = ?", (*values, other))
        return True

    # -- step 4
    def closure_check(self):
        if self.capped:
            return
        con = self.con
        cutoff = self.started - settings.CLOSURE_CHECK_DAYS * 86400 + CHECK_SLACK_S
        rows = con.execute("SELECT id, google_place_id FROM place WHERE google_place_id IS NOT NULL AND "
                           "(closure_checked_at IS NULL OR closure_checked_at <= ?) "
                           "ORDER BY closure_checked_at IS NOT NULL, closure_checked_at, id", (cutoff,)).fetchall()
        for p in rows:
            try:
                g = self._details(p["google_place_id"])
            except google.CapReached:
                return
            with db.transaction(con):
                if g is not None:
                    place_closure_flag(con, p["id"], g["status"])
                con.execute("UPDATE place SET closure_checked_at = ? WHERE id = ?", (self.started, p["id"]))

    # -- step 5
    def save(self, run_id, error=None) -> int:
        finished = max(common.now(), self.started)
        with db.transaction(self.con):
            self.con.execute("UPDATE scan_run SET finished_at = ?, tiles = ?, search_calls = ?, detail_calls = ?, "
                             "new_candidates = ?, error = ? WHERE id = ?",
                             (finished, self.tiles, self.search_calls, self.detail_calls,
                              len(self.inserted - self.removed), error, run_id))
            if error is None:
                self.con.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('baseline_at', ?)", (str(finished),))
        return finished


_OSM_SET = "osm_ref = ?, osm_name = ?, osm_address = ?, osm_lat = ?, osm_lng = ?, osm_extra = ?, chain_hint = ?"


def _osm_values(el) -> list:
    if not el:
        return [None] * 6 + [0]
    extra = {k: el.get(k) for k in ("cuisine", "phone", "website", "opening_hours")}
    return [el["ref"], el["name"], el["address"], el["lat"], el["lng"], json.dumps(extra), int(bool(el["is_chain"]))]


def _nearest(g, rows):
    """The id of the nearest (id, name, lat, lng) within ATTACH_KM of g whose name matches g's, or None."""
    best, best_km = None, ATTACH_KM
    for rid, name, lat, lng in rows:
        if lat is None or lng is None or not common.names_match(g["name"], name):
            continue
        km = common.km_between(g["lat"], g["lng"], lat, lng)
        if km <= best_km:
            best, best_km = rid, km
    return best


def run(con, now=None) -> dict | None:
    """One scan (spec §8.2; see the module docstring). Returns the `scan_run` row as a dict, or None without a Google
    key (scanning is off: no row, no call). Records an error in `scan_run.error` and re-raises it."""
    if not google.enabled():
        return None
    started = common.now() if now is None else int(now)
    run_id = con.execute("INSERT INTO scan_run (started_at) VALUES (?)", (started,)).lastrowid
    r = _Run(con, started)
    try:
        r.search()
        r.sort()
        r.closure_check()
    except Exception as e:
        r.save(run_id, error=(str(e) or type(e).__name__)[:ERROR_MAX])
        raise
    r.save(run_id)
    return dict(con.execute("SELECT * FROM scan_run WHERE id = ?", (run_id,)).fetchone())
