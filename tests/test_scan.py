"""Task 9: the weekly scan (spec §8.2). Google and OSM are faked through fake_http with the Task 7 and 8 helpers:
`fake_scan` is a small world (what Google and OSM know) around Concord's centre, served by rules on fake_http."""
import json
import math
import urllib.parse

import pytest
from conftest import mk_candidate, mk_place
from plugins.google import g_details, g_ids_page
from plugins.osm import overpass_el

import common
import google
import manage
import scan
import settings

CENTRE = (37.9780, -122.0311)  # Concord


def near(north_m, east_m, centre=CENTRE):
    """A pin this many metres north and east of a centre (Concord's by default)."""
    lat = centre[0] + north_m / 1000 / scan.KM_PER_DEG
    return round(lat, 7), round(centre[1] + east_m / 1000 / (scan.KM_PER_DEG * math.cos(math.radians(lat))), 7)


class World:
    """What Google and OSM know. Text Search answers the ids whose pin is inside the rectangle, 20 a page and 60 at
    most (Google's limit); Place Details answers an id it knows (or the new id of a moved one) and 404 otherwise;
    Overpass answers every OSM element."""

    def __init__(self, fake_http):
        self.http = fake_http
        self.google = {}      # id -> [name, lat, lng, status]
        self.moved = {}       # old id -> new id (Text Search reports the old id with movedPlaceId)
        self.osm = []
        self.searched = []    # each rectangle searched (first pages only), as (south, west, north, east)
        self.detailed = []    # each id Place Details was asked for
        self.search_error = None
        fake_http.on("places:searchText", self._search)
        fake_http.on("places/", self._details)
        fake_http.on("overpass-api.de", self._overpass)

    def add(self, gid, name, lat, lng, status="OPERATIONAL", osm=None, osm_name=None, **tags):
        self.google[gid] = [name, lat, lng, status]
        if osm:
            self.osm.append(overpass_el(osm, osm_name or name, lat, lng, **tags))

    def overpass_calls(self):
        return [c for c in self.http.calls if "overpass-api.de" in c.url]

    def _search(self, call):
        if self.search_error:
            raise self.search_error
        rect = call.data["locationRestriction"]["rectangle"]
        s, w = rect["low"]["latitude"], rect["low"]["longitude"]
        n, e = rect["high"]["latitude"], rect["high"]["longitude"]
        inside = [g for g, (_n, lat, lng, _s) in self.google.items() if s <= lat <= n and w <= lng <= e][:60]
        start = int(call.data.get("pageToken") or 0)
        if start == 0:
            self.searched.append((s, w, n, e))
        page = [(g, self.moved[g]) if g in self.moved else g for g in inside[start:start + 20]]
        return g_ids_page(page, str(start + 20) if start + 20 < len(inside) else None)

    def _details(self, call):
        gid = urllib.parse.unquote(call.url.rsplit("/", 1)[1])
        self.detailed.append(gid)
        known = gid if gid in self.google else next((o for o, n in self.moved.items() if n == gid), None)
        if known is None:
            raise common.UpstreamError("places.googleapis.com answered 404", status=404)
        name, lat, lng, status = self.google[known]
        return g_details(gid, name, lat, lng, status)

    def _overpass(self, call):
        return {"elements": self.osm}


@pytest.fixture
def pauses(monkeypatch):
    slept = []
    monkeypatch.setattr(scan, "sleep", slept.append)
    return slept


@pytest.fixture
def fake_scan(fake_http, monkeypatch, clock, pauses):
    """One town (Concord, a 1 km box) cut into 0.5 km tiles, the type `restaurant`, a key set, no real sleeping."""
    clock.set("2026-11-14T03:00")
    monkeypatch.setattr(settings, "GOOGLE_PLACES_KEY", "test-key-not-real")
    monkeypatch.setattr(settings, "TOWNS", (settings.Town("Concord", *CENTRE, 0.5),))
    monkeypatch.setattr(settings, "SCAN_TILE_KM", 0.5)
    monkeypatch.setattr(settings, "SCAN_MIN_TILE_KM", 0.25)
    monkeypatch.setattr(settings, "SCAN_TYPES", ("restaurant",))
    return World(fake_http)


def candidates(con):
    return {r["google_place_id"]: dict(r) for r in con.execute("SELECT * FROM candidate")}


def baseline(con):
    row = con.execute("SELECT value FROM meta WHERE key = 'baseline_at'").fetchone()
    return int(row[0]) if row else None


# ---------------------------------------------------------------- tiles
def test_tiles_cover_the_town_box_in_squares_no_bigger_than_the_tile():
    town = settings.Town("Concord", *CENTRE, 4.0)
    s, w, n, e = scan.box(town)
    assert common.km_between(s, CENTRE[1], n, CENTRE[1]) == pytest.approx(8.0, rel=0.01)
    assert common.km_between(CENTRE[0], w, CENTRE[0], e) == pytest.approx(8.0, rel=0.01)
    tiles = scan.tiles(town, 1.5)
    assert len(tiles) == 36   # 8 km / 1.5 km -> 6 a side
    assert min(t[0] for t in tiles) == s and max(t[2] for t in tiles) == n
    assert min(t[1] for t in tiles) == w and max(t[3] for t in tiles) == e
    assert all(scan.side_km(t) <= 1.5 for t in tiles)
    assert sum((t[2] - t[0]) * (t[3] - t[1]) for t in tiles) == pytest.approx((n - s) * (e - w))


def test_split_makes_four_quarters():
    quarters = scan.split((37.0, -122.0, 37.2, -121.8))
    assert sorted(quarters) == sorted([(37.0, -122.0, 37.1, -121.9), (37.0, -121.9, 37.1, -121.8),
                                       (37.1, -122.0, 37.2, -121.9), (37.1, -121.9, 37.2, -121.8)])


# ---------------------------------------------------------------- the plan's tests
def test_first_scan_is_backlog_and_sets_baseline(con, fake_scan, clock):
    fake_scan.add("g1", "Casa Ejemplo", *near(100, 100), osm="node/1", cuisine="mexican", phone="+1 925 555 0100",
                  website="https://casa.example.com", opening_hours="Mo-Su 11:00-21:00",
                  **{"addr:housenumber": "100", "addr:street": "Example Street", "addr:city": "Concord"})
    fake_scan.add("g2", "Pho Example", *near(-200, -150))
    row = scan.run(con)
    assert row["error"] is None and row["finished_at"] is not None
    assert row["tiles"] == 4 and row["search_calls"] == 4 and row["detail_calls"] == 2 and row["new_candidates"] == 2
    found = candidates(con)
    assert {g: c["status"] for g, c in found.items()} == {"g1": "backlog", "g2": "backlog"}
    assert baseline(con) == row["finished_at"]
    casa = found["g1"]
    assert casa["origin"] == "scan" and casa["first_seen_at"] == row["started_at"]
    assert (casa["osm_ref"], casa["osm_name"], casa["osm_address"]) == ("node/1", "Casa Ejemplo",
                                                                       "100 Example Street, Concord")
    assert (casa["osm_lat"], casa["osm_lng"]) == near(100, 100)
    assert json.loads(casa["osm_extra"]) == {"cuisine": "mexican", "phone": "+1 925 555 0100",
                                             "website": "https://casa.example.com",
                                             "opening_hours": "Mo-Su 11:00-21:00"}
    assert casa["chain_hint"] == 0
    assert (casa["g_name"], casa["g_status"], casa["g_type"], casa["g_fetched_at"]) == \
        ("Casa Ejemplo", "OPERATIONAL", "mexican_restaurant", clock.t)
    assert found["g2"]["osm_ref"] is None and found["g2"]["g_name"] == "Pho Example"
    assert len(fake_scan.overpass_calls()) == 1
    assert dict(con.execute("SELECT * FROM scan_run").fetchone()) == row


def test_second_scan_new_ids_are_new(con, fake_scan, clock):
    fake_scan.add("g1", "Casa Ejemplo", *near(100, 100))
    first = scan.run(con)
    clock.advance(7 * 86400)
    fake_scan.add("g2", "Pho Example", *near(-200, -150))
    fake_scan.detailed.clear()
    row = scan.run(con)
    found = candidates(con)
    assert found["g1"]["status"] == "backlog" and found["g2"]["status"] == "new"
    assert fake_scan.detailed == ["g2"] and row["detail_calls"] == 1 and row["new_candidates"] == 1
    assert baseline(con) == first["finished_at"]


def test_full_tile_is_split_until_min_size(con, fake_scan, monkeypatch):
    monkeypatch.setattr(settings, "SCAN_TILE_KM", 1.0)          # the 1 km box is one tile
    for i in range(65):                                          # more than Google ever returns, all on one spot
        fake_scan.add(f"g{i:02}", f"Example Kitchen {i}", *near(-300, -300))
    row = scan.run(con)
    sizes = sorted((round(scan.side_km(r), 3) for r in fake_scan.searched), reverse=True)
    assert sizes == [1.0] + [0.5] * 4 + [0.25] * 4               # 1 km -> 0.5 -> 0.25, never below SCAN_MIN_TILE_KM
    assert row["tiles"] == 9 and len(candidates(con)) == 60


def test_full_tile_split_finds_what_one_search_cannot(con, fake_scan, monkeypatch):
    monkeypatch.setattr(settings, "SCAN_TILE_KM", 1.0)
    for i in range(70):                                          # spread over the box: no quarter holds 60
        fake_scan.add(f"g{i:02}", f"Example Kitchen {i}", *near(-450 + (i % 10) * 100, -450 + (i // 10) * 130))
    row = scan.run(con)
    assert row["tiles"] == 5 and len(candidates(con)) == 70


def test_known_ids_are_not_duplicated_and_moves_update_the_id(con, fake_scan):
    mk_place(con, "Casa Ejemplo", google_place_id="g1", closure_checked_at=common.now())
    dismissed = mk_candidate(con, google_place_id="g2", status="dismissed")
    moved_place = mk_place(con, "Pho Example", google_place_id="old3", closure_checked_at=common.now())
    moved_cand = mk_candidate(con, google_place_id="old4", status="new")
    fake_scan.add("g1", "Casa Ejemplo", *near(0, 0))
    fake_scan.add("g2", "Example Gas Station", *near(50, 50))
    fake_scan.add("old3", "Pho Example", *near(-100, 0))
    fake_scan.add("old4", "Taqueria Example", *near(100, -100))
    fake_scan.moved.update(old3="new3", old4="new4")
    row = scan.run(con)
    assert row["new_candidates"] == 0 and row["detail_calls"] == 0 and fake_scan.detailed == []
    rows = con.execute("SELECT id, google_place_id, status FROM candidate ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [(dismissed, "g2", "dismissed"), (moved_cand, "new4", "new")]
    assert con.execute("SELECT google_place_id FROM place WHERE id = ?", (moved_place,)).fetchone()[0] == "new3"
    scan.run(con)
    assert con.execute("SELECT COUNT(*) FROM candidate").fetchone()[0] == 2


def test_chain_brand_is_hidden(con, fake_scan, clock):
    fake_scan.add("g1", "Chain Burger Example", *near(0, 0), osm="node/9", brand="Chain Burger")
    fake_scan.add("g2", "Casa Ejemplo", *near(200, 0), osm="way/10")
    scan.run(con)
    clock.advance(7 * 86400)
    fake_scan.add("g3", "Chain Burger Example", *near(-300, 300), osm="node/11", **{"brand:wikidata": "Q0"})
    scan.run(con)
    found = candidates(con)
    assert (found["g1"]["status"], found["g1"]["chain_hint"]) == ("chain_hidden", 1)
    assert (found["g3"]["status"], found["g3"]["chain_hint"]) == ("chain_hidden", 1)
    assert (found["g2"]["status"], found["g2"]["chain_hint"]) == ("backlog", 0)


def test_attaches_to_where_to_eat_candidate_and_hand_place(con, fake_scan, clock):
    hand = mk_place(con, "Casa Ejemplo", lat=near(0, 0)[0], lng=near(0, 0)[1])
    wte = mk_candidate(con, origin="where_to_eat", status="new", wte_id=7,
                       wte_json=json.dumps({"name": "Pho Example", "lat": near(200, 200)[0], "lng": near(200, 200)[1]}))
    fake_scan.add("g1", "Casa Ejemplo Restaurant", *near(60, 60), osm="node/1")    # 85 m from the hand place
    fake_scan.add("g2", "Pho Example", *near(290, 290), osm="node/2")              # 127 m from the WTE pin
    fake_scan.add("g3", "Casa Ejemplo", *near(-160, 0))                             # 160 m: too far to be the same
    fake_scan.add("g4", "Taqueria Example", *near(10, 0))                           # near, but another name
    row = scan.run(con)
    place = con.execute("SELECT * FROM place WHERE id = ?", (hand,)).fetchone()
    assert place["google_place_id"] == "g1" and place["osm_ref"] == "node/1"
    assert place["closure_checked_at"] == clock.t and place["closure_flag"] is None
    found = candidates(con)
    assert set(found) == {"g2", "g3", "g4"}
    assert found["g2"]["id"] == wte and found["g2"]["status"] == "new" and found["g2"]["origin"] == "where_to_eat"
    assert found["g2"]["g_name"] == "Pho Example" and found["g2"]["osm_ref"] == "node/2"
    assert found["g3"]["status"] == found["g4"]["status"] == "backlog"
    assert row["new_candidates"] == 2 and row["detail_calls"] == 4   # the attached place needs no closure check


def test_cap_leaves_pending_hidden_and_next_run_fills_them(con, fake_scan, monkeypatch, clock):   # Review Focus 2
    monkeypatch.setattr(settings, "GOOGLE_MAX_PRO_PER_MONTH", 2)
    clock.set("2026-11-20T03:00")
    for i, pin in enumerate([near(100, 100), near(-100, 100), near(100, -100)], 1):
        fake_scan.add(f"g{i}", f"Example Kitchen {i}", *pin)
    mk_place(con, "Casa Ejemplo", google_place_id="gp", lat=near(-200, -200)[0], lng=near(-200, -200)[1])
    fake_scan.add("gp", "Casa Ejemplo", *near(-200, -200))
    row = scan.run(con)
    assert row["error"] is None and row["detail_calls"] == 2 and google.usage(con)["pro_calls"] == 2
    assert len(fake_scan.detailed) == 2
    found = candidates(con)
    waiting = [g for g, c in found.items() if c["status"] == "pending"]
    assert len(waiting) == 1 and found[waiting[0]]["g_fetched_at"] is None
    oldest = min(found.values(), key=lambda c: c["id"])
    assert oldest["google_place_id"] == waiting[0]                   # newest first: the oldest one waits
    assert con.execute("SELECT closure_checked_at FROM place").fetchone()[0] is None   # nothing left for closures
    assert baseline(con) == row["finished_at"]

    clock.advance(7 * 86400)                                         # still November: nothing more is billed
    fake_scan.detailed.clear()
    again = scan.run(con)
    assert again["detail_calls"] == 0 and fake_scan.detailed == [] and google.usage(con)["pro_calls"] == 2

    clock.set("2026-12-04T03:00")
    fake_scan.add("g9", "Example Kitchen 9", *near(-100, -100))
    row = scan.run(con)
    found = candidates(con)
    assert found[waiting[0]]["status"] == "backlog"                  # first seen before the baseline
    assert found["g9"]["status"] == "new"
    assert not [c for c in found.values() if c["status"] == "pending"]
    assert row["detail_calls"] == 2 and google.usage(con)["pro_calls"] == 2   # the cap holds in December too
    assert con.execute("SELECT closure_checked_at FROM place").fetchone()[0] is None


def test_closure_flag_set_not_standing(con, fake_scan, clock):
    pid = mk_place(con, "Casa Ejemplo", google_place_id="g1")
    fake_scan.add("g1", "Casa Ejemplo", *near(0, 0), status="CLOSED_PERMANENTLY")
    scan.run(con)
    place = con.execute("SELECT * FROM place WHERE id = ?", (pid,)).fetchone()
    assert place["closure_flag"] == "CLOSED_PERMANENTLY" and place["standing"] == "open"
    assert place["closure_checked_at"] == clock.t and place["version"] == 1
    assert con.execute("SELECT COUNT(*) FROM place_event").fetchone()[0] == 0
    clock.advance(7 * 86400)                                         # checked a week ago: not again yet
    fake_scan.detailed.clear()
    scan.run(con)
    assert fake_scan.detailed == []
    clock.advance(21 * 86400)                                        # 28 days on: checked again
    scan.run(con)
    assert fake_scan.detailed == ["g1"]


def test_place_closure_flag_rules(con):
    def flag(status, **cols):
        pid = mk_place(con, f"Example {flag.n}", **cols)
        flag.n += 1
        scan.place_closure_flag(con, pid, status)
        return tuple(con.execute("SELECT closure_flag, closure_dismissed FROM place WHERE id = ?", (pid,)).fetchone())
    flag.n = 0
    assert flag("CLOSED_TEMPORARILY") == ("CLOSED_TEMPORARILY", None)
    assert flag("CLOSED_PERMANENTLY", closure_flag="CLOSED_TEMPORARILY") == ("CLOSED_PERMANENTLY", None)
    # an editor dismissed this status: not flagged again until Google says something else
    assert flag("CLOSED_PERMANENTLY", closure_dismissed="CLOSED_PERMANENTLY") == (None, "CLOSED_PERMANENTLY")
    assert flag("CLOSED_PERMANENTLY", closure_dismissed="CLOSED_TEMPORARILY") == ("CLOSED_PERMANENTLY",
                                                                                  "CLOSED_TEMPORARILY")
    # OPERATIONAL clears the dismissal (and a flag nobody confirmed): a later closure flags again
    assert flag("OPERATIONAL", closure_dismissed="CLOSED_PERMANENTLY") == (None, None)
    assert flag("OPERATIONAL", closure_flag="CLOSED_TEMPORARILY") == (None, None)
    # the standing already says so: nothing to confirm
    assert flag("CLOSED_PERMANENTLY", standing="closed") == (None, None)
    assert flag("CLOSED_TEMPORARILY", standing="temp_closed") == (None, None)
    assert flag("CLOSED_TEMPORARILY", standing="closed") == (None, None)
    assert flag("CLOSED_PERMANENTLY", standing="temp_closed") == ("CLOSED_PERMANENTLY", None)
    assert flag(None) == (None, None)


def test_dismissed_closure_is_not_flagged_again_until_the_status_changes(con, fake_scan, clock):
    pid = mk_place(con, "Casa Ejemplo", google_place_id="g1", closure_dismissed="CLOSED_TEMPORARILY")
    fake_scan.add("g1", "Casa Ejemplo", *near(0, 0), status="CLOSED_TEMPORARILY")
    scan.run(con)
    assert con.execute("SELECT closure_flag FROM place WHERE id = ?", (pid,)).fetchone()[0] is None
    fake_scan.google["g1"][3] = "OPERATIONAL"
    clock.advance(28 * 86400)
    scan.run(con)
    fake_scan.google["g1"][3] = "CLOSED_TEMPORARILY"
    clock.advance(28 * 86400)
    scan.run(con)
    assert tuple(con.execute("SELECT closure_flag, closure_dismissed FROM place WHERE id = ?", (pid,)).fetchone()) \
        == ("CLOSED_TEMPORARILY", None)


def test_error_is_recorded_and_exit_code_1(con, fake_scan, capsys):
    fake_scan.search_error = common.UpstreamError("places.googleapis.com answered 500", status=500)
    with pytest.raises(common.UpstreamError):
        scan.run(con)
    row = con.execute("SELECT * FROM scan_run").fetchone()
    assert row["error"] == "places.googleapis.com answered 500" and row["finished_at"] is not None
    assert baseline(con) is None
    assert manage.main(["scan"]) == 1
    assert "places.googleapis.com answered 500" in capsys.readouterr().err
    assert con.execute("SELECT COUNT(*) FROM scan_run WHERE error IS NOT NULL").fetchone()[0] == 2
    assert "test-key-not-real" not in json.dumps([dict(r) for r in con.execute("SELECT * FROM scan_run")])


def test_no_key_does_nothing(con, fake_http, capsys):
    assert not google.enabled()
    mk_place(con, "Casa Ejemplo", google_place_id="g1")
    mk_candidate(con, google_place_id="g2", status="pending")
    assert scan.run(con) is None
    assert manage.main(["scan"]) == 0
    assert "GOOGLE_PLACES_KEY" in capsys.readouterr().out
    assert fake_http.calls == []
    assert con.execute("SELECT COUNT(*) FROM scan_run").fetchone()[0] == 0 and baseline(con) is None


# ---------------------------------------------------------------- more of §8.2
def test_command_prints_what_it_did(con, fake_scan, capsys):
    fake_scan.add("g1", "Casa Ejemplo", *near(0, 0))
    assert manage.main(["scan"]) == 0
    out = capsys.readouterr().out
    assert "4 tiles" in out and "1 new candidate" in out


def test_one_overpass_query_per_town_box_paced(con, fake_scan, monkeypatch, pauses):
    other = (38.0194, -122.1341)    # Martinez
    monkeypatch.setattr(settings, "TOWNS", (settings.Town("Concord", *CENTRE, 0.5),
                                            settings.Town("Martinez", *other, 0.5)))
    for i in range(3):
        fake_scan.add(f"c{i}", f"Concord Kitchen {i}", *near(100 * i, 0))
        fake_scan.add(f"m{i}", f"Martinez Kitchen {i}", *near(100 * i, 0, centre=other))
    scan.run(con)
    calls = fake_scan.overpass_calls()
    assert len(calls) == 2 and pauses == [scan.OSM_PAUSE_S]
    assert all(c.headers["User-Agent"] == "HometownTable/1 (+https://github.com/SDNick484/hometown-table)" for c in calls)
    assert {c["status"] for c in candidates(con).values()} == {"backlog"}


def test_no_pending_means_no_overpass_query(con, fake_scan):
    scan.run(con)
    assert fake_scan.overpass_calls() == []


def test_busy_overpass_is_retried_once_then_fails_the_run_keeping_the_details(con, fake_scan, pauses, clock):
    fake_scan.add("g1", "Casa Ejemplo", *near(0, 0), osm="node/1")
    busy = common.UpstreamError("overpass-api.de answered 504", status=504)
    fake_scan.http.on("overpass-api.de", [busy, busy])
    with pytest.raises(common.UpstreamError):
        scan.run(con)
    assert pauses == [scan.OSM_RETRY_S]
    cand = candidates(con)["g1"]
    assert cand["status"] == "pending" and cand["g_name"] == "Casa Ejemplo"
    assert con.execute("SELECT error FROM scan_run").fetchone()[0] == "overpass-api.de answered 504"
    fake_scan.http.on("overpass-api.de", {"elements": fake_scan.osm})
    fake_scan.detailed.clear()
    clock.advance(86400)
    row = scan.run(con)
    assert fake_scan.detailed == [] and row["detail_calls"] == 0     # the stored details are used, not bought again
    assert candidates(con)["g1"]["status"] == "backlog" and candidates(con)["g1"]["osm_ref"] == "node/1"


def test_overpass_retry_that_succeeds_keeps_going(con, fake_scan, pauses):
    fake_scan.add("g1", "Casa Ejemplo", *near(0, 0), osm="node/1")
    fake_scan.http.on("overpass-api.de", [common.UpstreamError("overpass-api.de answered 429", status=429),
                                          {"elements": fake_scan.osm}])
    row = scan.run(con)
    assert row["error"] is None and candidates(con)["g1"]["osm_ref"] == "node/1"


def test_an_id_google_no_longer_knows_is_dropped(con, fake_scan):
    fake_scan.add("g1", "Casa Ejemplo", *near(0, 0))
    fake_scan.http.on("places/g1", common.UpstreamError("places.googleapis.com answered 404", status=404))
    row = scan.run(con)
    assert candidates(con) == {} and row["detail_calls"] == 1 and row["new_candidates"] == 0


def test_scan_types_are_each_searched(con, fake_scan, monkeypatch):
    monkeypatch.setattr(settings, "SCAN_TYPES", ("restaurant", "bakery"))
    scan.run(con)
    bodies = [c.data for c in fake_scan.http.calls if "searchText" in c.url]
    assert sorted({(b["textQuery"], b["includedType"], b["strictTypeFiltering"]) for b in bodies}) == \
        [("bakery", "bakery", True), ("restaurant", "restaurant", True)]
    assert len(bodies) == 8


def test_ops_job_runs_the_scan_weekly_as_hometown_with_the_config():
    with open(f"{scan.settings.BASE}/ops/hometown-scan", encoding="utf-8") as f:
        job = f.read()
    assert job.startswith("#!/bin/sh\n")
    assert ". \"$CONF\"" in job and "/etc/conf.d/hometown" in job
    assert "su -s /bin/sh hometown -c" in job and "manage.py scan" in job
