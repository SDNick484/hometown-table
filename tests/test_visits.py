"""Visits, ratings, "me" and the scores (plan Task 4; spec §6.2, §6.3)."""
import pytest

import visits
from conftest import EDITOR, EDITOR2, mk_person, mk_place, mk_visit
from common import ApiError


def _post(c, pid, **body):
    return c.post(f"/api/places/{pid}/visits", json=body)


def test_late_night_visit_is_dated_today_in_los_angeles(editor, con, clock):   # Review Focus 5
    clock.set_utc("2026-11-15T07:30")          # 23:30 on Nov 14 in Concord
    pid = mk_place(con)
    mk_person(con, "Alex", email="alex@example.com")
    r = editor.post(f"/api/places/{pid}/visits", json={"people": [{"person": "me", "rating": 5}]})
    assert r.status_code == 201
    assert editor.get(f"/api/visits?place={pid}").json["visits"][0]["visited_on"] == "2026-11-14"


def test_me_without_link_is_400_and_saves_nothing(keyed, con):                  # Review Focus 4
    pid = mk_place(con)
    r = keyed("write", as_email="sam@example.com").post(f"/api/places/{pid}/visits",
            json={"people": [{"person": "me", "rating": 4}]}, headers={"X-Hometown": "1"})
    assert r.status_code == 400 and r.json["error"] == "Link your email to a person in Settings."
    assert con.execute("SELECT COUNT(*) FROM visit").fetchone()[0] == 0


def test_forged_acting_as_without_a_key_is_not_me(editor2, con):
    pid = mk_place(con)
    mk_person(con, "Alex", email="alex@example.com")
    r = editor2.post(f"/api/places/{pid}/visits", json={"people": ["me"]}, headers={"X-Hometown-As": "alex@example.com"})
    assert r.status_code == 400 and con.execute("SELECT COUNT(*) FROM visit").fetchone()[0] == 0


def test_key_acting_as_a_linked_editor_logs_for_that_person(keyed, con):
    pid = mk_place(con)
    a = mk_person(con, "Alex", email="alex@example.com")
    r = keyed("write", as_email="alex@example.com").post(
        f"/api/places/{pid}/visits", json={"people": [{"person": "me", "rating": 3}]}, headers={"X-Hometown": "1"})
    assert r.status_code == 201
    v = r.json["visit"]
    assert v["added_by"] == "mcp" and [(p["person_id"], p["rating"]) for p in v["people"]] == [(a, 3)]


def test_logging_clears_want_to_try(editor, con):
    pid = mk_place(con, want_to_try=1, want_since=100)
    a = mk_person(con, "Alex")
    assert _post(editor, pid, people=[{"person": a}]).status_code == 201
    row = con.execute("SELECT want_to_try, want_since FROM place WHERE id = ?", (pid,)).fetchone()
    assert tuple(row) == (0, None)


def test_back_dated_visit_leaves_a_later_star(editor, con, clock):
    clock.set("2026-11-14T18:00")
    starred = clock.t - 3 * 86400                                       # starred on Nov 11, at 6 pm in Concord
    pid = mk_place(con, want_to_try=1, want_since=starred)
    a = mk_person(con, "Alex")
    assert _post(editor, pid, visited_on="2026-11-10", people=[a]).status_code == 201   # the day before: kept
    assert tuple(con.execute("SELECT want_to_try, want_since FROM place WHERE id = ?", (pid,)).fetchone()) == (1, starred)
    assert _post(editor, pid, visited_on="2026-11-11", people=[a]).status_code == 201   # the day it was starred
    assert tuple(con.execute("SELECT want_to_try, want_since FROM place WHERE id = ?", (pid,)).fetchone()) == (0, None)


def test_star_day_is_the_los_angeles_day(editor, con, clock):
    clock.set_utc("2026-11-15T07:30")                                   # 23:30 on Nov 14 in Concord
    pid = mk_place(con, want_to_try=1, want_since=clock.t)
    assert _post(editor, pid, visited_on="2026-11-14", people=[mk_person(con, "Alex")]).status_code == 201
    assert con.execute("SELECT want_to_try FROM place WHERE id = ?", (pid,)).fetchone()[0] == 0


def test_star_without_a_date_is_cleared_by_any_visit(editor, con):
    pid = mk_place(con, want_to_try=1, want_since=None)
    assert _post(editor, pid, visited_on="2020-01-01", people=[mk_person(con, "Alex")]).status_code == 201
    assert con.execute("SELECT want_to_try FROM place WHERE id = ?", (pid,)).fetchone()[0] == 0


def test_rejected_visit_leaves_want_to_try_alone(editor, con):
    pid = mk_place(con, want_to_try=1, want_since=100)
    mk_person(con, "Alex")
    assert _post(editor, pid, people=[{"person": "Nobody"}]).status_code == 400
    assert con.execute("SELECT want_to_try FROM place WHERE id = ?", (pid,)).fetchone()[0] == 1


def test_future_date_and_unknown_name_are_400(editor, con, clock):
    clock.set("2026-11-14T12:00")
    pid = mk_place(con)
    mk_person(con, "Alex")
    r = _post(editor, pid, visited_on="2026-11-15", people=["Alex"])
    assert r.status_code == 400 and r.json["error"] == "That date is in the future."
    r = _post(editor, pid, people=[{"person": "Casey"}])
    assert r.status_code == 400 and r.json["error"] == "No one called Casey."
    assert _post(editor, pid, visited_on="2026-11-14", people=["Alex"]).status_code == 201
    assert _post(editor, pid, visited_on="2026-02-30", people=["Alex"]).status_code == 400
    assert con.execute("SELECT COUNT(*) FROM visit").fetchone()[0] == 1


def test_who_went_is_required(editor, con):
    pid = mk_place(con)
    for body in ({}, {"people": []}, {"people": [{"rating": 4}]}):
        r = _post(editor, pid, **body)
        assert r.status_code == 400 and r.json["error"] == "Who went?"


def test_names_match_active_people_ignoring_case_and_accents(editor, con):
    pid = mk_place(con)
    mk_person(con, "José")
    gone = mk_person(con, "Riley")
    con.execute("UPDATE person SET active = 0 WHERE id = ?", (gone,))
    assert _post(editor, pid, people=[{"person": "JOSE"}]).status_code == 201
    assert _post(editor, pid, people=[{"person": "riley"}]).json["error"] == "No one called riley."


def test_bad_ratings_and_long_text_are_400(editor, con):
    pid = mk_place(con)
    mk_person(con, "Alex")
    for rating in (0, 6, 3.5, "4", True):
        assert _post(editor, pid, people=[{"person": "Alex", "rating": rating}]).status_code == 400, rating
    assert _post(editor, pid, people=["Alex"], occasion="x" * 81).status_code == 400
    assert _post(editor, pid, people=["Alex"], note="x" * 2001).status_code == 400
    assert _post(editor, pid, people=[{"person": "Alex", "note": "x" * 501}]).status_code == 400
    assert _post(editor, pid, people=["Alex", {"person": "alex"}]).status_code == 400
    assert con.execute("SELECT COUNT(*) FROM visit").fetchone()[0] == 0


def test_unknown_place_is_404(editor, con):
    mk_person(con, "Alex")
    assert _post(editor, 999, people=["Alex"]).status_code == 404


def test_score_uses_each_persons_latest_rating(con):
    pid = mk_place(con)
    a, b = mk_person(con, "Alex"), mk_person(con, "Sam")
    mk_visit(con, pid, "2026-01-01", {a: 2, b: 4})
    mk_visit(con, pid, "2026-06-01", {a: 5})
    assert visits.score(con, pid, [a, b]) == 4.5 and visits.liked_by_all(con, pid, [a, b])
    assert visits.latest_ratings(con, pid, [a, b]) == {a: 5, b: 4}


def test_unrated_visits_do_not_replace_a_rating_and_empty_means_none(con):
    pid = mk_place(con)
    a, b = mk_person(con, "Alex"), mk_person(con, "Sam")
    mk_visit(con, pid, "2026-01-01", {a: 2})
    mk_visit(con, pid, "2026-06-01", {a: None, b: None})
    assert visits.latest_ratings(con, pid, [a, b]) == {a: 2}
    assert visits.score(con, pid, [b]) is None and not visits.liked_by_all(con, pid, [b])
    assert not visits.liked_by_all(con, pid, [a, b])      # Alex's 2 is not a like


def test_liked_by_all_ignores_people_who_never_rated(con):
    pid = mk_place(con)
    a, b = mk_person(con, "Alex"), mk_person(con, "Sam")
    mk_visit(con, pid, "2026-01-01", {a: 4})
    assert visits.liked_by_all(con, pid, [a, b])


def test_person_without_rating_is_on_the_visit(editor, con):
    pid = mk_place(con)
    mk_person(con, "Alex")
    mk_person(con, "Sam", grp="kid")
    _post(editor, pid, people=[{"person": "Alex", "rating": 5, "note": "Great salsa"}, {"person": "Sam"}])
    people = editor.get(f"/api/visits?place={pid}").json["visits"][0]["people"]
    assert [(p["name"], p["rating"], p["note"]) for p in people] == [("Alex", 5, "Great salsa"), ("Sam", None, None)]


def test_history_filters_and_order(editor, con):
    p1, p2 = mk_place(con), mk_place(con, "Pho Example")
    a, b = mk_person(con, "Alex"), mk_person(con, "Sam")
    mk_visit(con, p1, "2026-01-01", {a: 3})
    mk_visit(con, p1, "2026-03-01", {a: 4, b: 4}, occasion="Riley's graduation")
    mk_visit(con, p2, "2026-02-01", {b: 5})
    ids = lambda **q: [v["visited_on"] for v in visits.history(con, **q)]   # noqa: E731
    assert ids() == ["2026-03-01", "2026-02-01", "2026-01-01"]
    assert ids(place_id=p1) == ["2026-03-01", "2026-01-01"]
    assert ids(person="sam") == ["2026-03-01", "2026-02-01"] and ids(person=a) == ["2026-03-01", "2026-01-01"]
    assert ids(occasion="GRADUATION") == ["2026-03-01"]
    assert ids(since="2026-02-01", until="2026-02-28") == ["2026-02-01"]
    assert ids(limit=1) == ["2026-03-01"]
    got = editor.get("/api/visits?person=Sam&since=2026-02-15").json
    assert [v["visited_on"] for v in got["visits"]] == ["2026-03-01"] and got["place"] is None
    assert editor.get(f"/api/visits?place={p2}").json["place"] == {"id": p2, "name": "Pho Example"}
    assert editor.get("/api/visits?person=Nobody").status_code == 400
    assert editor.get("/api/visits?since=soon").status_code == 400
    assert editor.get("/api/visits?place=999").status_code == 404


def test_update_replaces_people_and_needs_the_current_version(editor, con):
    pid = mk_place(con)
    a, b = mk_person(con, "Alex"), mk_person(con, "Sam")
    vid = _post(editor, pid, people=[{"person": a, "rating": 2}]).json["visit"]["id"]
    r = editor.patch(f"/api/visits/{vid}", json={"version": 1, "occasion": "Birthday",
                                                  "people": [{"person": a, "rating": 5}, {"person": b}]})
    assert r.status_code == 200
    v = r.json["visit"]
    assert v["version"] == 2 and v["occasion"] == "Birthday" and [p["rating"] for p in v["people"]] == [5, None]
    stale = editor.patch(f"/api/visits/{vid}", json={"version": 1, "note": "late"})
    assert stale.status_code == 409 and "Reload" in stale.json["error"]
    assert editor.patch(f"/api/visits/{vid}", json={"note": "no version"}).status_code == 400
    assert editor.patch("/api/visits/999", json={"version": 1}).status_code == 404
    r = editor.patch(f"/api/visits/{vid}", json={"version": 2, "occasion": None})
    assert r.json["visit"]["occasion"] is None and r.json["visit"]["note"] is None


def test_failed_update_changes_nothing(editor, con):
    pid = mk_place(con)
    a = mk_person(con, "Alex")
    vid = _post(editor, pid, people=[{"person": a, "rating": 2}], occasion="Lunch").json["visit"]["id"]
    r = editor.patch(f"/api/visits/{vid}", json={"version": 1, "occasion": "Dinner", "people": []})
    assert r.status_code == 400
    v = visits.get(con, vid)
    assert v["occasion"] == "Lunch" and v["version"] == 1 and v["people"][0]["rating"] == 2


def test_delete_removes_the_visit_and_its_people(editor, con):
    pid = mk_place(con)
    a = mk_person(con, "Alex")
    vid = mk_visit(con, pid, "2026-01-01", {a: 3})
    assert editor.delete(f"/api/visits/{vid}", json={}).status_code == 200
    assert con.execute("SELECT COUNT(*) FROM visit_person").fetchone()[0] == 0
    assert editor.delete(f"/api/visits/{vid}", json={}).status_code == 404


def test_read_key_can_list_but_not_log(keyed, con):
    pid = mk_place(con)
    mk_person(con, "Alex")
    reader = keyed("read")
    assert reader.get("/api/visits").status_code == 200
    r = reader.post(f"/api/places/{pid}/visits", json={"people": ["Alex"]}, headers={"X-Hometown": "1"})
    assert r.status_code == 403 and r.json["error"] == "The read key can only read."


def test_me_endpoint(editor, editor2, con, keyed):
    a = mk_person(con, "Alex", email=EDITOR)
    assert editor.get("/api/me").json == {"email": EDITOR, "person": {"id": a, "name": "Alex"}}
    assert editor2.get("/api/me").json == {"email": EDITOR2, "person": None}
    assert keyed("read").get("/api/me").json == {"email": None, "person": None}
    assert keyed("read", as_email=EDITOR).get("/api/me").json["person"]["id"] == a


def test_visit_people_lists_active_people(editor, con):
    mk_person(con, "Alex")
    gone = mk_person(con, "Riley")
    con.execute("UPDATE person SET active = 0 WHERE id = ?", (gone,))
    assert [p["name"] for p in editor.get("/api/visits/people").json["people"]] == ["Alex"]


def test_log_raises_api_error_directly(con):
    with pytest.raises(ApiError) as e:
        visits.log(con, mk_place(con), {"people": []}, "lan", None)
    assert e.value.status == 400
