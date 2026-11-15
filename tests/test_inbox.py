"""Task 10: the inbox (New, Backlog, Flags, Hidden as chains), accepting with a verdict, closure flags, and the add-by-hand
lookup. Google and OSM go through fake_http; stored place fields never come from Google unless an editor says so."""
import json

import pytest
from conftest import mk_candidate, mk_list, mk_place
from plugins.google import g_details, g_ids_page

import common
import inbox
import settings

DAY = 86400
OSM_EXTRA = json.dumps({"cuisine": "mexican", "phone": "+1 555 0100", "website": "https://casa.example.com",
                        "opening_hours": "Mo-Su 11:00-21:00"})


def osm_candidate(con, **cols):
    """A scan candidate Google found and the scan matched to OSM, with a Google cache that differs from OSM."""
    row = dict(google_place_id="g-casa", osm_ref="node/1001", osm_name="Casa Ejemplo",
               osm_address="100 Example Street, Concord, CA 94520", osm_lat=37.9781, osm_lng=-122.0312,
               osm_extra=OSM_EXTRA, g_name="Casa Ejemplo Google Name", g_address="100 Example Way, Concord, CA",
               g_lat=37.97, g_lng=-122.03, g_status="OPERATIONAL", g_type="mexican_restaurant",
               g_fetched_at=common.now())
    return mk_candidate(con, **{**row, **cols})


def google_only(con, **cols):
    """A scan candidate with no OSM match, its Google cache fresh."""
    row = dict(google_place_id="g-pho", g_name="Pho Example", g_address="200 Example Street, Concord, CA 94520",
               g_lat=37.9795, g_lng=-122.0305, g_status="OPERATIONAL", g_type="vietnamese_restaurant",
               g_fetched_at=common.now())
    return mk_candidate(con, **{**row, **cols})


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_PLACES_KEY", "test-key-not-real")


def candidate(con, cid):
    return dict(con.execute("SELECT * FROM candidate WHERE id = ?", (cid,)).fetchone())


# ---------------------------------------------------------------- accepting
def test_accept_builds_place_from_osm_and_keeps_lists(editor, con, clock):
    clock.set("2026-11-14T12:00")
    seen = common.now() - 10 * DAY
    cid = osm_candidate(con, first_seen_at=seen)
    lid = mk_list(con, "Example Taco Trail")
    con.execute("INSERT INTO list_entry (list_id, line, candidate_id, status, created_at) VALUES (?, ?, ?, 'matched', ?)",
                (lid, "Casa Ejemplo", cid, common.now()))
    r = editor.post(f"/api/inbox/{cid}/accept", json={"ownership": "counts", "ownership_reason": "owner at the counter",
                                                      "want_to_try": True})
    assert r.status_code == 201, r.json
    p = r.json
    assert (p["name"], p["address"], p["lat"], p["lng"]) == ("Casa Ejemplo", "100 Example Street, Concord, CA 94520",
                                                             37.9781, -122.0312)
    assert (p["cuisine"], p["phone"], p["website"], p["hours"]) == ("mexican", "+1 555 0100", "https://casa.example.com",
                                                                    "Mo-Su 11:00-21:00")
    assert (p["town"], p["osm_ref"], p["google_place_id"]) == ("Concord", "node/1001", "g-casa")
    assert (p["origin"], p["first_seen_at"], p["want_to_try"]) == ("scan", seen, 1)
    assert (p["ownership"], p["ownership_reason"], p["added_by"]) == ("counts", "owner at the counter", "alex@example.com")
    assert p["lists"] == [{"id": lid, "name": "Example Taco Trail"}]
    c = candidate(con, cid)
    assert (c["status"], c["place_id"], c["decided_at"]) == ("accepted", p["id"], common.now())
    # no Google value reached the place
    assert "Google" not in json.dumps({k: v for k, v in p.items() if k != "google_place_id"})


def test_accept_takes_overrides_tags_and_needs_a_verdict(editor, con):
    cid = osm_candidate(con)
    tag = con.execute("SELECT id FROM tag ORDER BY id LIMIT 1").fetchone()[0]
    assert editor.post(f"/api/inbox/{cid}/accept", json={"ownership_reason": "x"}).status_code == 400
    r = editor.post(f"/api/inbox/{cid}/accept", json={"ownership": "counts"})
    assert r.status_code == 400 and "ownership_reason" in r.json["error"]
    assert candidate(con, cid)["status"] == "new"
    r = editor.post(f"/api/inbox/{cid}/accept", json={"ownership": "unsure", "ownership_reason": "one location?",
                                                      "name": "Casa Ejemplo Taqueria", "phone": "", "tags": [tag]})
    assert r.status_code == 201
    assert (r.json["name"], r.json["phone"], [t["id"] for t in r.json["tags"]]) == ("Casa Ejemplo Taqueria", None, [tag])
    again = editor.post(f"/api/inbox/{cid}/accept", json={"ownership": "counts", "ownership_reason": "x"})
    assert again.status_code == 409
    assert con.execute("SELECT COUNT(*) FROM place").fetchone()[0] == 1


def test_accept_near_an_existing_place_asks_first(editor, con):
    existing = mk_place(con, "Casa Ejemplo", lat=37.9781, lng=-122.0312)
    cid = osm_candidate(con)
    r = editor.post(f"/api/inbox/{cid}/accept", json={"ownership": "counts", "ownership_reason": "x"})
    assert r.status_code == 409 and r.json["duplicate_of"] == existing
    assert candidate(con, cid)["status"] == "new"
    r = editor.post(f"/api/inbox/{cid}/accept", json={"ownership": "counts", "ownership_reason": "x", "add_anyway": True})
    assert r.status_code == 201


def test_google_value_needs_explicit_from_google_and_prefill_on(editor, con, monkeypatch, fake_http):
    cid = google_only(con)
    verdict = {"ownership": "counts", "ownership_reason": "family run"}
    # no OSM name, and the Google name is never used on its own
    r = editor.post(f"/api/inbox/{cid}/accept", json=verdict)
    assert r.status_code == 400 and r.json["error"] == "name is required."
    # the pin never comes from Google, even when asked
    r = editor.post(f"/api/inbox/{cid}/accept", json={**verdict, "name": "Pho Example", "lat": {"from_google": True},
                                                      "lng": {"from_google": True}})
    assert r.status_code == 400
    monkeypatch.setattr(settings, "GOOGLE_PREFILL", False)
    r = editor.post(f"/api/inbox/{cid}/accept", json={**verdict, "name": {"from_google": True}})
    assert r.status_code == 400 and "GOOGLE_PREFILL" in r.json["error"]
    assert candidate(con, cid)["status"] == "new" and fake_http.calls == []
    monkeypatch.setattr(settings, "GOOGLE_PREFILL", True)
    fake_http.on("nominatim", "osm/nominatim_address.json")
    r = editor.post(f"/api/inbox/{cid}/accept", json={**verdict, "name": {"from_google": True},
                                                      "address": {"from_google": True}})
    assert r.status_code == 201, r.json
    p = r.json
    assert (p["name"], p["address"]) == ("Pho Example", "200 Example Street, Concord, CA 94520")
    # the pin is the geocoded confirmed address (Nominatim), not Google's cached location
    assert (p["lat"], p["lng"]) == (37.979, -122.03)
    (call,) = fake_http.calls
    assert "nominatim" in call.url and "200+Example+Street" in call.url


def test_from_google_with_no_cache_fetches_live_details(editor, con, key, fake_http):
    cid = google_only(con, g_name=None, g_address=None, g_lat=None, g_lng=None, g_status=None, g_type=None,
                      g_fetched_at=None)
    fake_http.on("places/g-pho", g_details("g-pho", "Pho Example", 37.979, -122.03))
    fake_http.on("nominatim", "osm/nominatim_empty.json")
    fake_http.on("photon", "osm/photon_empty.json")
    r = editor.post(f"/api/inbox/{cid}/accept", json={"ownership": "counts", "ownership_reason": "x",
                                                      "name": {"from_google": True}, "address": "200 Example Street"})
    assert r.status_code == 201, r.json
    assert r.json["name"] == "Pho Example" and r.json["lat"] is None    # no geocode answer: saved without a pin
    assert [c.url.split("?")[0] for c in fake_http.calls][0].endswith("/places/g-pho")


def test_doesnt_count_creates_a_past_place_and_scan_wont_offer_it(editor, con):
    cid = osm_candidate(con, osm_name="Chain Burger Example", chain_hint=1, status="chain_hidden")
    r = editor.post(f"/api/inbox/{cid}/accept", json={"ownership": "no", "ownership_reason": "part of a group"})
    assert r.status_code == 201
    pid = r.json["id"]
    assert [p["id"] for p in editor.get("/api/past").json["places"]] == [pid]
    # the next scan sees the Google id on a place (and the accepted candidate) and offers nothing
    assert con.execute("SELECT google_place_id FROM place WHERE id = ?", (pid,)).fetchone()[0] == "g-casa"
    assert candidate(con, cid)["status"] == "accepted"
    for tab in ("new", "backlog", "chain_hidden"):
        assert editor.get(f"/api/inbox?tab={tab}").json["rows"] == []


def test_where_to_eat_candidate_uses_its_own_fields(editor, con, fake_http):
    wte = {"name": "Taqueria Muestra", "address": "300 Sample Avenue, Martinez, CA 94553", "cuisine": "Tacos",
           "phone": "+1 555 0102", "website": "https://taqueria.example.com", "hours": "Tu-Su 10:00-20:00",
           "lat": 38.0190, "lng": -122.1340}
    cid = mk_candidate(con, origin="where_to_eat", wte_id=42, wte_json=json.dumps(wte))
    r = editor.post(f"/api/inbox/{cid}/accept", json={"ownership": "counts", "ownership_reason": "from Where to Eat"})
    assert r.status_code == 201, r.json
    p = r.json
    for k in ("name", "address", "cuisine", "phone", "website", "hours", "lat", "lng"):
        assert p[k] == wte[k], k
    assert (p["origin"], p["wte_id"], p["town"], p["osm_ref"]) == ("where_to_eat", 42, "Martinez", None)
    assert fake_http.calls == []  # it had a pin: no geocoding


# ---------------------------------------------------------------- listing
def test_tabs_counts_and_row_shape(editor, con, clock):
    clock.set("2026-11-14T12:00")
    lid = mk_list(con, "Example Taco Trail")
    newer = osm_candidate(con, first_seen_at=common.now())
    older = mk_candidate(con, origin="where_to_eat", wte_id=7, first_seen_at=common.now() - DAY,
                         wte_json=json.dumps({"name": "Taqueria Muestra", "address": "300 Sample Avenue, Martinez, CA"}))
    con.execute("INSERT INTO list_entry (list_id, line, candidate_id, status, created_at) VALUES (?, ?, ?, 'matched', ?)",
                (lid, "Taqueria Muestra", older, common.now()))
    mk_candidate(con, status="backlog", osm_name="Pho Example", osm_ref="node/5", google_place_id="g-b")
    mk_candidate(con, status="chain_hidden", osm_name="Chain Burger Example", chain_hint=1, osm_ref="node/6",
                 google_place_id="g-c")
    mk_place(con, "Casa Cerrada", closure_flag="CLOSED_PERMANENTLY")
    r = editor.get("/api/inbox?tab=new").json
    assert r["tab"] == "new" and r["total"] == 2
    assert r["counts"] == {"new": 2, "backlog": 1, "flags": 1, "chain_hidden": 1}
    assert [row["id"] for row in r["rows"]] == [newer, older]
    first, second = r["rows"]
    assert (first["name"], first["name_from"], first["osm_match"], first["town"]) == ("Casa Ejemplo", "osm", True, "Concord")
    assert first["google"]["name"] == "Casa Ejemplo Google Name" and first["google"]["type"] == "mexican_restaurant"
    assert first["osm"]["cuisine"] == "mexican" and first["osm"]["hours"] == "Mo-Su 11:00-21:00"
    assert (second["name"], second["name_from"], second["osm_match"]) == ("Taqueria Muestra", "where_to_eat", False)
    assert second["lists"] == [{"id": lid, "name": "Example Taco Trail"}] and second["town"] == "Martinez"
    flags = editor.get("/api/inbox?tab=flags").json
    assert [(f["name"], f["closure_flag"]) for f in flags["rows"]] == [("Casa Cerrada", "CLOSED_PERMANENTLY")]
    assert editor.get("/api/inbox?tab=chain_hidden").json["rows"][0]["chain_hint"] is True
    assert editor.get("/api/inbox?tab=pending").status_code == 400
    assert editor.get("/api/inbox?tab=new&offset=1").json["rows"][0]["id"] == older
    assert editor.get("/api/inbox?tab=new&limit=0").json["rows"] == []


def test_pending_dismissed_accepted_never_listed(editor, con):
    for status in ("pending", "dismissed", "accepted"):
        mk_candidate(con, status=status, osm_name=f"Hidden {status}")
    r = editor.get("/api/inbox?tab=new").json
    assert r["rows"] == [] and r["counts"] == {"new": 0, "backlog": 0, "flags": 0, "chain_hidden": 0}
    for tab in ("new", "backlog", "chain_hidden", "flags"):
        assert editor.get(f"/api/inbox?tab={tab}").json["total"] == 0


def test_refetch_only_unmatched_expired_rows_on_screen(editor, con, fake_http, clock, key):
    clock.set("2026-11-14T12:00")
    t = common.now()
    old = t - 31 * DAY
    matched = osm_candidate(con, google_place_id="g-matched", g_fetched_at=old, first_seen_at=t)
    fresh = google_only(con, google_place_id="g-fresh", g_fetched_at=t - DAY, first_seen_at=t - 1)
    expired = google_only(con, google_place_id="g-expired", g_fetched_at=old, first_seen_at=t - 2)
    empty = google_only(con, google_place_id="g-empty", g_name=None, g_address=None, g_lat=None, g_lng=None,
                        g_status=None, g_type=None, g_fetched_at=None, first_seen_at=t - 3)
    google_only(con, google_place_id="g-offscreen", g_fetched_at=None, g_name=None, first_seen_at=t - 4)
    fake_http.on("places/g-expired", g_details("g-expired", "Pho Example Renamed", 37.979, -122.03))
    fake_http.on("places/g-empty", g_details("g-empty", "Banh Mi Example", 37.979, -122.03))
    rows = editor.get("/api/inbox?tab=new&limit=4").json["rows"]
    assert [r["id"] for r in rows] == [matched, fresh, expired, empty]
    assert sorted(c.url.rsplit("/", 1)[1] for c in fake_http.calls) == ["g-empty", "g-expired"]
    by_id = {r["id"]: r for r in rows}
    assert by_id[expired]["name"] == "Pho Example Renamed" and by_id[expired]["name_from"] == "google"
    assert by_id[empty]["google"]["name"] == "Banh Mi Example"
    assert candidate(con, empty)["g_fetched_at"] == t       # cached again, for at most 30 days (google.tidy)
    assert candidate(con, matched)["g_fetched_at"] == old   # matched rows show OSM values and are not re-fetched
    fake_http.calls.clear()
    editor.get("/api/inbox?tab=new&limit=4")
    assert fake_http.calls == []


def test_cap_reached_row_says_so(editor, con, fake_http, monkeypatch, key):
    monkeypatch.setattr(settings, "GOOGLE_MAX_PRO_PER_MONTH", 0)
    cid = google_only(con, g_name=None, g_address=None, g_lat=None, g_lng=None, g_status=None, g_type=None,
                      g_fetched_at=None)
    (row,) = editor.get("/api/inbox?tab=new").json["rows"]
    assert row["id"] == cid and row["google_place_id"] == "g-pho"
    assert row["note"] == "Google details wait for next month."
    assert fake_http.calls == []


def test_no_key_shows_rows_without_fetching(editor, con, fake_http):
    google_only(con, g_name=None, g_fetched_at=None)
    (row,) = editor.get("/api/inbox?tab=new").json["rows"]
    assert row["name"] is None and fake_http.calls == []


# ---------------------------------------------------------------- dismiss, unhide
def test_dismiss_and_unhide(editor, con):
    a = osm_candidate(con)
    b = mk_candidate(con, status="chain_hidden", osm_name="Chain Burger Example", chain_hint=1)
    assert editor.post(f"/api/inbox/{a}/dismiss", json={}).json["status"] == "dismissed"
    assert candidate(con, a)["decided_at"] is not None
    assert editor.post(f"/api/inbox/{a}/unhide", json={}).status_code == 409
    assert editor.post(f"/api/inbox/{b}/unhide", json={}).json["status"] == "new"
    assert [r["id"] for r in editor.get("/api/inbox?tab=new").json["rows"]] == [b]
    assert editor.post("/api/inbox/999/dismiss", json={}).status_code == 404
    assert editor.post(f"/api/inbox/{b}/dismiss").status_code == 400      # a mutation needs a JSON body


def test_read_key_cannot_sort(keyed, con):
    cid = osm_candidate(con)
    assert keyed("read").get("/api/inbox").status_code == 200
    assert keyed("read").post(f"/api/inbox/{cid}/dismiss", json={}).status_code == 403


# ---------------------------------------------------------------- closure flags
def test_confirm_flag_sets_standing_with_history(editor, con, clock):
    clock.set("2026-11-14T12:00")
    shut = mk_place(con, "Casa Ejemplo", closure_flag="CLOSED_PERMANENTLY")
    paused = mk_place(con, "Pho Example", closure_flag="CLOSED_TEMPORARILY")
    r = editor.post(f"/api/places/{shut}/flag/confirm", json={})
    assert r.status_code == 200
    p = r.json
    assert (p["standing"], p["standing_since"], p["closure_flag"]) == ("closed", "2026-11-14", None)
    assert p["history"][0] == {"kind": "standing", "old": "open", "new": "closed", "reason": "Google lists it as closed",
                               "on": "2026-11-14", "by": "alex@example.com"}
    assert editor.post(f"/api/places/{paused}/flag/confirm", json={}).json["standing"] == "temp_closed"
    assert editor.post(f"/api/places/{shut}/flag/confirm", json={}).status_code == 409   # no flag left
    assert editor.get("/api/inbox?tab=flags").json["rows"] == []


def test_dismiss_flag_remembers_the_status(editor, con):
    pid = mk_place(con, "Casa Ejemplo", closure_flag="CLOSED_TEMPORARILY")
    p = editor.post(f"/api/places/{pid}/flag/dismiss", json={}).json
    assert (p["closure_flag"], p["closure_dismissed"], p["standing"], p["history"]) == (None, "CLOSED_TEMPORARILY", "open", [])
    assert editor.post(f"/api/places/{pid}/flag/dismiss", json={}).status_code == 409
    assert editor.post("/api/places/999/flag/dismiss", json={}).status_code == 404


# ---------------------------------------------------------------- add by hand
def test_lookup_marks_existing_places(editor, con, fake_http):
    pid = mk_place(con, "Casa Ejemplo", osm_ref="node/1001", lat=37.9781, lng=-122.0312)
    cid = mk_candidate(con, osm_ref="way/2002", osm_name="Chain Burger Example", status="chain_hidden")
    fake_http.on("nominatim", "osm/nominatim_search.json")
    r = editor.get("/api/lookup?name=casa&town=concord")
    assert r.status_code == 200, r.json
    casa, chain = r.json["results"]
    assert (casa["source"], casa["name"], casa["osm_ref"], casa["in_list"], casa["in_inbox"]) == \
        ("osm", "Casa Ejemplo", "node/1001", pid, None)
    assert (casa["cuisine"], casa["hours"], casa["town"]) == ("mexican", "Mo-Su 11:00-21:00", "Concord")
    assert (chain["in_list"], chain["in_inbox"], chain["is_chain"]) == (None, cid, True)
    (call,) = fake_http.calls          # one Nominatim call, bounded to Concord's box
    assert "bounded=1" in call.url and "q=casa" in call.url
    assert r.json["google"] is False
    assert editor.get("/api/lookup?name=casa&town=Nowhere").status_code == 400
    assert editor.get("/api/lookup?name=").status_code == 400


def test_lookup_with_a_key_adds_google_top_three(editor, con, fake_http, key):
    fake_http.on("nominatim", "osm/nominatim_search.json")
    fake_http.on("searchText", g_ids_page(["g1", "g2", "g3", "g4"]))
    fake_http.on("places/g1", g_details("g1", "Casa Ejemplo", 37.9781, -122.0312))      # the same place OSM found
    fake_http.on("places/g2", g_details("g2", "Pho Example", 37.9790, -122.0300))
    fake_http.on("places/g3", common.UpstreamError("gone", status=404))
    mk_place(con, "Pho Example", google_place_id="g2")
    r = editor.get("/api/lookup?name=example").json
    assert sorted(c.url.rsplit("/", 1)[1] for c in fake_http.calls if "/places/g" in c.url) == ["g1", "g2", "g3"]
    rows = r["results"]
    casa = next(x for x in rows if x["name"] == "Casa Ejemplo")
    assert (casa["source"], casa["google_place_id"], casa["osm_ref"]) == ("osm", "g1", "node/1001")
    pho = next(x for x in rows if x["name"] == "Pho Example")
    assert (pho["source"], pho["google_place_id"], pho["osm_ref"], pho["in_list"] is not None) == ("google", "g2", None, True)
    assert pho["google"]["name"] == "Pho Example"
    assert len(rows) == 3 and r["google"] is True
    search = next(c for c in fake_http.calls if "searchText" in c.url)
    assert "locationRestriction" in search.data


def test_lookup_at_the_cap_says_so_and_keeps_osm(editor, con, fake_http, key, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_MAX_PRO_PER_MONTH", 0)
    fake_http.on("nominatim", "osm/nominatim_search.json")
    fake_http.on("searchText", g_ids_page(["g1"]))
    r = editor.get("/api/lookup?name=casa").json
    assert len(r["results"]) == 2 and "Google details wait for next month." in r["notes"]


def test_lookup_survives_osm_failing(editor, con, fake_http):
    fake_http.on("nominatim", common.UpstreamError("down", status=503))
    r = editor.get("/api/lookup?name=casa")
    assert r.status_code == 200 and r.json["results"] == [] and r.json["notes"]


def test_geocode_for_the_add_form(editor, fake_http):
    fake_http.on("nominatim", "osm/nominatim_address.json")
    assert editor.get("/api/geocode?address=200 Example Street&town=Concord").json == {"lat": 37.979, "lng": -122.03}
    fake_http.on("nominatim", "osm/nominatim_empty.json")
    fake_http.on("photon", "osm/photon_empty.json")
    assert editor.get("/api/geocode?address=nowhere&town=Concord").json == {"lat": None, "lng": None}
    assert editor.get("/api/geocode?address=").status_code == 400


def test_module_functions_directly(con):
    """The route-free API Task 13's tools reach through the routes."""
    cid = osm_candidate(con)
    pid = inbox.accept(con, cid, {"ownership": "counts", "ownership_reason": "x"}, "mcp")
    assert con.execute("SELECT added_by FROM place WHERE id = ?", (pid,)).fetchone()[0] == "mcp"
    assert inbox.rows(con, "new")["total"] == 0
