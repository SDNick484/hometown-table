"""Task 15: visits synced from Dinner Log and Where to Eat (visit_sync.py, its Settings API, the command and the
cron job). The feeds are served by tests/plugins/visit_sync.py through fake_http; every restaurant and person is
invented."""
import json
import os
import re
import types

import pytest

import common
import health
import manage
import settings
import visit_sync
import visits
from conftest import mk_person, mk_place, mk_visit
from plugins.visit_sync import DL_TOKEN, DL_URL, WTE_TOKEN, dl_visit, wte_visit

CONCORD = (37.9780, -122.0311)
WALNUT_CREEK = (37.9101, -122.0652)
M = 1 / 111_000   # about a metre of latitude, in degrees


def near(metres, centre=CONCORD):
    return centre[0] + metres * M, centre[1]


@pytest.fixture
def today(clock):
    clock.set("2026-11-14T18:00")
    return clock


@pytest.fixture
def group(con):
    """Alex and Sam, chosen as the synced-visit people; Riley is not in the group."""
    alex, sam = mk_person(con, "Alex", email="alex@example.com"), mk_person(con, "Sam")
    mk_person(con, "Riley", grp="kid")
    visit_sync.set_people(con, [alex, sam])
    return types.SimpleNamespace(alex=alex, sam=sam)


def ledger(con, source=None):
    sql = "SELECT * FROM sync_seen" + (" WHERE source = ?" if source else "") + " ORDER BY source, CAST(ref AS INTEGER)"
    return [dict(r) for r in con.execute(sql, (source,) if source else ())]


def visit_rows(con):
    return [dict(r) for r in con.execute("SELECT * FROM visit ORDER BY id")]


def meta(con, key):
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------- paging and the cursor
def test_dinner_log_reads_every_page_and_keeps_the_cursor(con, feeds, group, today):
    pid = mk_place(con, "Casa Ejemplo")
    feeds.dl.entries = [dl_visit(n, f"2026-11-0{n}", "Casa Ejemplo", "Concord") for n in range(1, 6)]
    feeds.dl.page_max = 2
    out = visit_sync.run(con)
    assert feeds.dl.afters == [0, 2, 4, 5]                  # pages of two, then an empty page ends it
    assert out["dinner_log"] == {"status": "ok", "new": 5, "logged": 5, "duplicate": 0, "unmatched": 0}
    assert meta(con, "sync_cursor_dinner_log") == "5"
    assert [v["visited_on"] for v in visit_rows(con)] == [f"2026-11-0{n}" for n in range(1, 6)]
    assert {v["place_id"] for v in visit_rows(con)} == {pid}
    feeds.dl.entries.append(dl_visit(6, "2026-11-06", "Casa Ejemplo", "Concord"))
    visit_sync.run(con)
    assert feeds.dl.afters[4:] == [5, 6] and len(visit_rows(con)) == 6


def test_where_to_eat_reads_every_page_and_keeps_the_cursor(con, feeds, group, today):
    mk_place(con, "Pho Example", wte_id=45)
    feeds.wte.entries = [wte_visit(n, "2026-11-10", "Pho Example", place_id=45) for n in (3, 8, 20)]
    feeds.wte.page_max = 1
    out = visit_sync.run(con)
    assert feeds.wte.afters == [0, 3, 8, 20]
    assert out["where_to_eat"]["new"] == 3 and meta(con, "sync_cursor_where_to_eat") == "20"
    assert [r["status"] for r in ledger(con, "where_to_eat")] == ["logged", "duplicate", "duplicate"]  # one day, one visit
    visit_sync.run(con)
    assert feeds.wte.afters[4:] == [20]


def test_requests_carry_the_token_and_the_contract_query(con, feeds, group, today):
    visit_sync.run(con)
    urls = sorted(c.url for c in feeds.http.calls)
    assert urls == [f"{DL_URL}/api/restaurant-visits?after=0&limit=200", "https://wte.example.com/feed/visits?after=0&limit=200"]
    assert {c.headers["Authorization"] for c in feeds.http.calls} == {f"Bearer {DL_TOKEN}", f"Bearer {WTE_TOKEN}"}


def test_hand_written_feed_pages_in_the_contract_shape(con, fake_http, monkeypatch, group, today):
    monkeypatch.setattr(settings, "DINNER_LOG_URL", DL_URL + "/")      # a trailing slash on the base is fine
    monkeypatch.setattr(settings, "DINNER_LOG_TOKEN", DL_TOKEN)
    monkeypatch.setattr(settings, "WTE_URL", "https://wte.example.com")
    monkeypatch.setattr(settings, "WTE_TOKEN", WTE_TOKEN)
    empty = {"version": 1, "visits": [], "next": 0}
    fake_http.on("restaurant-visits?after=0&", "dinner_log/restaurant-visits.json")
    fake_http.on("restaurant-visits?after=102&", {**empty, "next": 102})
    fake_http.on("feed/visits?after=0&", "where_to_eat/feed-visits.json")
    fake_http.on("feed/visits?after=46&", {**empty, "next": 46})
    casa = mk_place(con, "Casa Ejemplo")
    pho = mk_place(con, "Pho Example", town="Pleasant Hill")
    taq = mk_place(con, "Taqueria Ejemplo", town="Walnut Creek", lat=WALNUT_CREEK[0], lng=WALNUT_CREEK[1])
    out = visit_sync.run(con)
    assert out["dinner_log"]["logged"] == 2 and out["where_to_eat"]["logged"] == 1
    assert out["where_to_eat"]["unmatched"] == 1                        # Noodle Example: no such place here
    assert {(v["place_id"], v["visited_on"]) for v in visit_rows(con)} == {
        (casa, "2026-11-07"), (pho, "2026-11-10"), (taq, "2026-11-08")}
    row = next(r for r in ledger(con) if r["ref"] == "45")
    assert json.loads(row["extra"]) == {"place_id": 7, "address": "100 Example Street, Walnut Creek, CA 94596",
                                        "lat": 37.9101, "lng": -122.0652}
    assert json.loads(next(r for r in ledger(con) if r["ref"] == "101")["extra"]) == {"dish": "mole enchiladas"}
    assert (meta(con, "sync_cursor_dinner_log"), meta(con, "sync_cursor_where_to_eat")) == ("102", "46")


def test_cursor_advances_only_past_pages_that_were_stored(con, feeds, group, today):
    mk_place(con, "Casa Ejemplo")
    feeds.dl.entries = [dl_visit(n, f"2026-11-0{n}", "Casa Ejemplo") for n in range(1, 5)]
    feeds.dl.page_max = 2
    answers = iter([None, common.UpstreamError("dinner.example.com answered 500", status=500)])

    def flaky(call):
        problem = next(answers, None)
        if problem:
            raise problem
        return feeds.dl(call)
    feeds.http.on(DL_URL, flaky)
    with pytest.raises(visit_sync.SyncError):
        visit_sync.run(con)
    assert meta(con, "sync_cursor_dinner_log") == "2" and len(ledger(con, "dinner_log")) == 2
    feeds.http.on(DL_URL, feeds.dl)
    visit_sync.run(con)
    assert feeds.dl.afters == [0, 2, 4] and len(visit_rows(con)) == 4


def test_a_feed_answer_in_another_shape_is_an_error(con, feeds, group, today):
    feeds.http.on(DL_URL, {"version": 2, "visits": [], "next": 0})
    with pytest.raises(visit_sync.SyncError, match="Dinner Log"):
        visit_sync.run(con)
    assert meta(con, "sync_cursor_dinner_log") is None
    assert visit_sync.run_errors(con) == {"dinner_log": "Dinner Log sent an answer this version doesn't understand."}


def test_a_page_whose_cursor_does_not_advance_stops_the_run(con, feeds, group, today):
    feeds.http.on(DL_URL, {"version": 1, "visits": [dl_visit(1, "2026-11-01", "Casa Ejemplo")], "next": 0})
    with pytest.raises(visit_sync.SyncError, match="cursor"):
        visit_sync.run(con)


# ---------------------------------------------------------------- exactly once
def test_exactly_once_across_runs_and_after_the_synced_visit_is_deleted(con, feeds, group, today):
    pid = mk_place(con, "Casa Ejemplo")
    feeds.dl.entries = [dl_visit(7, "2026-11-12", "Casa Ejemplo", "Concord", "tamales")]
    visit_sync.run(con)
    visit_sync.run(con)
    assert len(visit_rows(con)) == 1
    vid = visit_rows(con)[0]["id"]
    assert ledger(con)[0] | {"seen_at": None} == {
        "source": "dinner_log", "ref": "7", "day": "2026-11-12", "name": "Casa Ejemplo", "location": "Concord",
        "extra": json.dumps({"dish": "tamales"}), "status": "logged", "place_id": pid, "visit_id": vid, "seen_at": None}
    visits.delete(con, vid)
    con.execute("DELETE FROM meta WHERE key = 'sync_cursor_dinner_log'")   # the whole feed is read again
    out = visit_sync.run(con)
    assert out["dinner_log"]["new"] == 0 and visit_rows(con) == []
    assert (ledger(con)[0]["status"], ledger(con)[0]["visit_id"]) == ("logged", None)


def test_a_visit_already_logged_that_day_is_a_duplicate(con, feeds, group, today):
    pid = mk_place(con, "Casa Ejemplo")
    hand = mk_visit(con, pid, "2026-11-12", {group.alex: 5})
    feeds.dl.entries = [dl_visit(1, "2026-11-12", "Casa Ejemplo", "Concord"),
                        dl_visit(2, "2026-11-13", "Casa Ejemplo", "Concord")]
    feeds.wte.entries = [wte_visit(2, "2026-11-13", "Casa Ejemplo", place_id=3)]     # the same outing in both apps
    out = visit_sync.run(con)
    assert (out["dinner_log"]["logged"], out["dinner_log"]["duplicate"]) == (1, 1)
    assert (out["where_to_eat"]["logged"], out["where_to_eat"]["duplicate"]) == (0, 1)
    rows = {(r["source"], r["ref"]): r for r in ledger(con)}
    assert (rows["dinner_log", "1"]["status"], rows["dinner_log", "1"]["visit_id"]) == ("duplicate", hand)
    on_13 = [v["id"] for v in visit_rows(con) if v["visited_on"] == "2026-11-13"]
    assert len(on_13) == 1 and len(visit_rows(con)) == 2
    assert (rows["dinner_log", "2"]["status"], rows["dinner_log", "2"]["visit_id"]) == ("logged", on_13[0])
    assert (rows["where_to_eat", "2"]["status"], rows["where_to_eat", "2"]["visit_id"]) == ("duplicate", on_13[0])
    assert rows["where_to_eat", "2"]["place_id"] == pid


def test_synced_visits_have_the_group_no_ratings_and_the_dish(con, feeds, group, today):
    pid = mk_place(con, "Casa Ejemplo", lat=CONCORD[0], lng=CONCORD[1], want_to_try=1, want_since=1)
    mk_place(con, "Casa Ejemplo", town="Clayton", lat=near(5000)[0], lng=CONCORD[1])  # same name, 5 km away
    feeds.dl.entries = [dl_visit(1, "2026-11-12", "Casa Ejemplo", "Concord", "Mole enchiladas")]
    feeds.wte.entries = [wte_visit(1, "2026-11-13", "Casa Ejemplo", place_id=99, lat=CONCORD[0], lng=CONCORD[1])]
    visit_sync.run(con)
    got = visits.history(con, place_id=pid)
    assert [v["visited_on"] for v in got] == ["2026-11-13", "2026-11-12"]
    for v in got:
        assert [(p["person_id"], p["rating"], p["note"]) for p in v["people"]] == [(group.alex, None, None), (group.sam, None, None)]
    assert got[1]["note"] == "Dinner Log: Mole enchiladas" and got[0]["note"] is None
    assert (got[1]["added_by"], got[0]["added_by"]) == ("sync:dinner_log", "sync:where_to_eat")
    assert con.execute("SELECT COUNT(*) FROM visit_person WHERE rating IS NOT NULL").fetchone()[0] == 0
    assert con.execute("SELECT want_to_try FROM place WHERE id = ?", (pid,)).fetchone()[0] == 0


def test_inactive_people_drop_out_of_the_group(con, feeds, group, today):
    mk_place(con, "Casa Ejemplo")
    con.execute("UPDATE person SET active = 0 WHERE id = ?", (group.sam,))
    assert visit_sync.people(con) == [group.alex]
    feeds.dl.entries = [dl_visit(1, "2026-11-12", "Casa Ejemplo")]
    visit_sync.run(con)
    assert [p["person_id"] for p in visits.history(con)[0]["people"]] == [group.alex]


# ---------------------------------------------------------------- matching
def test_where_to_eat_matches_by_wte_id_first(con, feeds, group, today):
    pid = mk_place(con, "Casa Ejemplo Renamed", wte_id=45)
    mk_place(con, "Pho Example")
    feeds.wte.entries = [wte_visit(1, "2026-11-12", "Pho Example", place_id=45)]
    visit_sync.run(con)
    assert ledger(con)[0]["status"] == "logged" and ledger(con)[0]["place_id"] == pid


def test_where_to_eat_matches_by_name_within_150_m_of_its_pin(con, feeds, group, today):
    here = mk_place(con, "Pho Example", lat=CONCORD[0], lng=CONCORD[1])
    mk_place(con, "Pho Example", lat=near(400)[0], lng=CONCORD[1])        # the same name, 400 m away
    feeds.wte.entries = [wte_visit(1, "2026-11-12", "Phở Example", place_id=8, lat=near(100)[0], lng=CONCORD[1]),
                         wte_visit(2, "2026-11-12", "Pho Example", place_id=9, lat=near(1500)[0], lng=CONCORD[1])]
    visit_sync.run(con)
    rows = ledger(con)
    assert (rows[0]["status"], rows[0]["place_id"]) == ("logged", here)
    assert rows[1]["status"] == "unmatched"                              # nothing named so within 150 m


def test_where_to_eat_without_a_pin_matches_by_name_and_town(con, feeds, group, today):
    mk_place(con, "Taqueria Ejemplo", town="Concord")
    wc = mk_place(con, "Taqueria Ejemplo", town="Walnut Creek")
    feeds.wte.entries = [
        wte_visit(1, "2026-11-12", "Taqueria Ejemplo", place_id=5, address="1 Example Street, Walnut Creek, CA 94596"),
        wte_visit(2, "2026-11-13", "Taqueria Ejemplo", location="Walnut Creek", place_id=5),
        wte_visit(3, "2026-11-11", "Taqueria Ejemplo", place_id=5)]                 # no town: two candidates
    visit_sync.run(con)
    assert [(r["status"], r["place_id"]) for r in ledger(con)] == [("logged", wc), ("logged", wc), ("unmatched", None)]


def test_dinner_log_matches_by_name_and_town_any_standing(con, feeds, group, today):
    closed = mk_place(con, "Casa Ejemplo", town="Martinez", standing="closed", ownership="no")
    mk_place(con, "Casa Ejemplo", town="Concord")
    feeds.dl.entries = [dl_visit(1, "2026-11-01", "casa ejemplo", "Martinez, CA")]
    visit_sync.run(con)
    assert (ledger(con)[0]["status"], ledger(con)[0]["place_id"]) == ("logged", closed)


def test_dinner_log_without_a_town_matches_one_place_by_name(con, feeds, group, today):
    pho = mk_place(con, "The Pho Example Restaurant", town="Pittsburg")
    feeds.dl.entries = [dl_visit(1, "2026-11-01", "Pho Example", "the place by the station")]
    visit_sync.run(con)
    assert ledger(con)[0]["place_id"] == pho


def test_ambiguous_and_unknown_are_unmatched_then_matched_on_retry(con, feeds, group, today):
    mk_place(con, "Casa Ejemplo", town="Concord")
    mk_place(con, "Casa Ejemplo", town="Clayton")
    feeds.dl.entries = [dl_visit(1, "2026-11-01", "Casa Ejemplo"),                   # two places by that name
                        dl_visit(2, "2026-11-02", "Noodle Example", "Lafayette")]   # none
    out = visit_sync.run(con)
    assert out["dinner_log"] == {"status": "ok", "new": 2, "logged": 0, "duplicate": 0, "unmatched": 2}
    assert [r["status"] for r in ledger(con)] == ["unmatched", "unmatched"] and visit_rows(con) == []
    noodle = mk_place(con, "Noodle Example", town="Lafayette")
    out = visit_sync.run(con)
    assert feeds.dl.afters == [0, 2, 2]                                  # the retry fetched nothing old again
    assert out["dinner_log"] == {"status": "ok", "new": 0, "logged": 1, "duplicate": 0, "unmatched": 0}
    rows = ledger(con)
    assert [r["status"] for r in rows] == ["unmatched", "logged"] and rows[1]["place_id"] == noodle
    assert visit_rows(con)[0]["visited_on"] == "2026-11-02"


def test_a_day_in_the_future_waits_as_unmatched_until_it_comes(con, feeds, group, today):
    mk_place(con, "Casa Ejemplo")
    feeds.dl.entries = [dl_visit(1, "2026-11-15", "Casa Ejemplo"), dl_visit(2, "not a day", "Casa Ejemplo")]
    visit_sync.run(con)
    assert [r["status"] for r in ledger(con)] == ["unmatched", "unmatched"] and visit_rows(con) == []
    today.set("2026-11-15T20:00")
    visit_sync.run(con)
    assert [r["status"] for r in ledger(con)] == ["logged", "unmatched"]


def test_an_entry_without_a_ref_is_skipped(con, feeds, group, today):
    feeds.http.on(DL_URL, [{"version": 1, "visits": [{"ref": None, "day": "2026-11-01", "name": "Casa Ejemplo"},
                                                      {"ref": "", "day": "2026-11-01", "name": "Casa Ejemplo"}],
                            "next": 4},
                           {"version": 1, "visits": [], "next": 4}])
    out = visit_sync.run(con)
    assert out["dinner_log"]["new"] == 0 and ledger(con) == [] and meta(con, "sync_cursor_dinner_log") == "4"


# ---------------------------------------------------------------- off, no group, errors, health
def test_no_source_configured_does_nothing(con, fake_http, group):
    assert visit_sync.run(con) == {"dinner_log": {"status": "off"}, "where_to_eat": {"status": "off"}}
    assert fake_http.calls == []


def test_a_source_needs_both_url_and_token(con, fake_http, monkeypatch, group):
    monkeypatch.setattr(settings, "DINNER_LOG_URL", DL_URL)
    assert not visit_sync.configured("dinner_log")
    assert visit_sync.run(con)["dinner_log"] == {"status": "off"} and fake_http.calls == []


def test_no_default_group_fetches_nothing_and_says_so(con, feeds, editor, today):
    mk_person(con, "Alex")
    out = visit_sync.run(con)
    assert out == {"dinner_log": {"status": "no_people"}, "where_to_eat": {"status": "no_people"}}
    assert feeds.http.calls == []
    sources = editor.get("/api/sync").get_json()["sources"]
    assert [s["last"]["status"] for s in sources] == ["no_people", "no_people"]
    assert health.problems(con) == []


def test_401_is_an_error_recorded_for_health(con, feeds, group, today, client):
    feeds.dl.fail = common.UpstreamError("dinner.example.com answered 401", status=401)
    mk_place(con, "Pho Example", wte_id=4)
    feeds.wte.entries = [wte_visit(1, "2026-11-12", "Pho Example", place_id=4)]
    with pytest.raises(visit_sync.SyncError) as e:
        visit_sync.run(con)
    assert "401" in str(e.value) and DL_TOKEN not in str(e.value)
    assert len(visit_rows(con)) == 1                                    # Where to Eat still synced
    errors = visit_sync.run_errors(con)
    assert list(errors) == ["dinner_log"] and "DINNER_LOG_TOKEN" in errors["dinner_log"]
    assert json.loads(meta(con, "sync_error")) == errors
    assert health.problems(con) == ["visit sync failed"]
    r = client.get("/healthz")
    assert r.status_code == 503 and r.get_json()["problems"] == ["visit sync failed"]
    feeds.dl.fail = None
    visit_sync.run(con)
    assert health.problems(con) == [] and meta(con, "sync_error") is None


def test_a_failed_source_that_is_switched_off_is_not_a_problem(con, feeds, group, today, monkeypatch):
    feeds.dl.fail = common.UpstreamError("dinner.example.com answered 404", status=404)
    with pytest.raises(visit_sync.SyncError, match="404"):
        visit_sync.run(con)
    assert "feed" in visit_sync.run_errors(con)["dinner_log"]
    assert health.problems(con) == ["visit sync failed"]
    monkeypatch.setattr(settings, "DINNER_LOG_TOKEN", "")
    assert health.problems(con) == []


def test_sync_health_is_reported_without_a_google_key(con, feeds, group, today, monkeypatch):
    assert not settings.GOOGLE_PLACES_KEY
    feeds.wte.fail = common.UpstreamError("wte.example.com could not be reached (timed out)")
    with pytest.raises(visit_sync.SyncError):
        visit_sync.run(con)
    assert health.problems(con) == ["visit sync failed"]
    monkeypatch.setattr(settings, "GOOGLE_PLACES_KEY", "test-google-key")
    con.execute("INSERT INTO scan_run (started_at, finished_at, error) VALUES (1, 2, 'x')")
    assert health.problems(con)[0] == "last scan failed" and "visit sync failed" in health.problems(con)


def test_one_run_at_a_time(con, feeds, group, today):
    con.execute("INSERT INTO meta (key, value) VALUES ('sync_running', ?)", (str(common.now() - 60),))
    with pytest.raises(visit_sync.SyncError, match="already running"):
        visit_sync.run(con)
    assert feeds.http.calls == []
    con.execute("UPDATE meta SET value = ? WHERE key = 'sync_running'", (str(common.now() - 2 * 3600),))   # stale
    visit_sync.run(con)
    assert meta(con, "sync_running") is None


# ---------------------------------------------------------------- the Settings API
def test_people_get_and_put_take_active_people_only(con, editor):
    alex, sam = mk_person(con, "Alex"), mk_person(con, "Sam")
    gone = mk_person(con, "Casey")
    con.execute("UPDATE person SET active = 0 WHERE id = ?", (gone,))
    assert editor.get("/api/sync/people").get_json() == {"people": []}
    r = editor.put("/api/sync/people", json={"people": [sam, alex]})
    assert r.status_code == 200 and r.get_json() == {"people": [sam, alex]}
    assert json.loads(meta(con, "sync_people")) == [sam, alex]
    for bad, message in (([gone], f"No one with id {gone}."), ([999], "No one with id 999."),
                         ([alex, alex], "Each person can be in the group once."), ("Alex", "people must be a list of person ids."),
                         ([True], "people must be a list of person ids.")):
        r = editor.put("/api/sync/people", json={"people": bad})
        assert r.status_code == 400 and r.get_json()["error"] == message, bad
    assert editor.put("/api/sync/people", json={"people": []}).get_json() == {"people": []}


def test_sync_status_lists_sources_and_unmatched(con, feeds, group, editor, today):
    feeds.dl.entries = [dl_visit(1, "2026-11-01", "Noodle Example", "Lafayette"),
                        dl_visit(2, "2026-11-03", "<b>Pho</b>", None)]
    visit_sync.run(con)
    body = editor.get("/api/sync").get_json()
    assert body["people"] == [group.alex, group.sam]
    dl, wte = body["sources"]
    assert (dl["source"], dl["label"], dl["configured"], dl["error"]) == ("dinner_log", "Dinner Log", True, None)
    assert dl["last"] == {"at": today.t, "status": "ok", "new": 2, "logged": 0, "duplicate": 0, "unmatched": 2}
    assert wte["label"] == "Where to Eat" and wte["last"]["new"] == 0
    assert body["unmatched"] == [
        {"source": "dinner_log", "label": "Dinner Log", "ref": "2", "day": "2026-11-03", "name": "<b>Pho</b>", "location": None},
        {"source": "dinner_log", "label": "Dinner Log", "ref": "1", "day": "2026-11-01", "name": "Noodle Example",
         "location": "Lafayette"}]
    assert DL_TOKEN not in json.dumps(body)


def test_dismiss_an_unmatched_visit(con, feeds, group, editor, today):
    feeds.dl.entries = [dl_visit(1, "2026-11-01", "Noodle Example", "Lafayette")]
    visit_sync.run(con)
    r = editor.post("/api/sync/dinner_log/1/dismiss", json={})
    assert r.status_code == 200 and r.get_json() == {"status": "dismissed"}
    assert ledger(con)[0]["status"] == "dismissed" and editor.get("/api/sync").get_json()["unmatched"] == []
    assert editor.post("/api/sync/dinner_log/1/dismiss", json={}).status_code == 409
    assert editor.post("/api/sync/dinner_log/9/dismiss", json={}).status_code == 404
    assert editor.post("/api/sync/elsewhere/1/dismiss", json={}).status_code == 404
    mk_place(con, "Noodle Example", town="Lafayette")
    visit_sync.run(con)
    assert visit_rows(con) == []                                         # a dismissed one is never retried


def test_sync_api_needs_an_editor_and_the_mutation_headers(client, editor, con):
    assert client.get("/api/sync").status_code == 401
    assert client.get("/api/sync/people").status_code == 401
    bare = editor.application.test_client()
    bare.environ_base.update(editor.environ_base)
    bare.environ_base.pop("HTTP_X_HOMETOWN")
    assert bare.put("/api/sync/people", json={"people": []}).status_code == 400


# ---------------------------------------------------------------- the command and the cron job
def test_command_runs_the_sync_and_exits_1_on_error(con, feeds, group, today, capsys):
    mk_place(con, "Casa Ejemplo")
    feeds.dl.entries = [dl_visit(1, "2026-11-01", "Casa Ejemplo"), dl_visit(2, "2026-11-01", "Noodle Example")]
    assert manage.main(["sync-visits"]) == 0
    out = capsys.readouterr().out
    assert "Dinner Log: 2 new, 1 logged, 0 already logged, 1 unmatched." in out and "Where to Eat: 0 new" in out
    feeds.wte.fail = common.UpstreamError("wte.example.com answered 401", status=401)
    assert manage.main(["sync-visits"]) == 1
    assert "WTE_TOKEN" in capsys.readouterr().err


def test_command_when_off_or_without_people(con, fake_http, capsys, monkeypatch):
    assert manage.main(["sync-visits"]) == 0
    assert "off" in capsys.readouterr().out and fake_http.calls == []
    monkeypatch.setattr(settings, "WTE_URL", "https://wte.example.com")
    monkeypatch.setattr(settings, "WTE_TOKEN", WTE_TOKEN)
    assert manage.main(["sync-visits"]) == 0
    assert "Settings" in capsys.readouterr().out and fake_http.calls == []


def test_cron_job_runs_the_sync_daily_as_hometown_with_the_config():
    with open(os.path.join(settings.BASE, "ops", "hometown-sync"), encoding="utf-8") as f:
        job = f.read()
    assert job.startswith("#!/bin/sh\n")
    loads = re.search(r'^\[ -f "\$CONF" \] && \. "\$CONF"$', job, re.M)
    assert "CONF=${CONF:-/etc/conf.d/hometown}" in job and "set -a" in job and loads
    assert loads.start() < job.index("su -s /bin/sh hometown -c") and "manage.py sync-visits" in job
    assert "su -l" not in job


def test_settings_keys_are_documented_and_installed():
    assert settings.SYNC_KEYS == ("DINNER_LOG_URL", "DINNER_LOG_TOKEN", "WTE_URL", "WTE_TOKEN")
    with open(os.path.join(settings.BASE, ".env.example"), encoding="utf-8") as f:
        example = f.read()
    for key in settings.SYNC_KEYS:
        assert re.search(rf"^{key}=$", example, re.M), key


def test_a_feed_url_must_be_http(monkeypatch):
    monkeypatch.setenv("DINNER_LOG_URL", "dinner.example.com")
    with pytest.raises(ValueError, match="DINNER_LOG_URL"):
        settings._url("DINNER_LOG_URL")
    monkeypatch.setenv("DINNER_LOG_URL", " https://dinner.example.com/ ")
    assert settings._url("DINNER_LOG_URL") == "https://dinner.example.com"
