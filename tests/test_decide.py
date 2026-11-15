"""The decider: modes, filters, audiences and shuffle (plan Task 6; spec §7)."""
import pytest

import decide
from common import ApiError
from conftest import mk_list, mk_person, mk_place, mk_tag, mk_visit


def names(con, **params):
    return [p["name"] for p in decide.decide(con, params)["places"]]


def _set_baseline(con, at):
    con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('baseline_at', ?)", (str(at),))


def _tag(con, place_id, tag_id):
    con.execute("INSERT INTO place_tag (place_id, tag_id) VALUES (?, ?)", (place_id, tag_id))


def _on_list(con, list_id, place_id):
    con.execute("INSERT INTO list_entry (list_id, line, place_id, status, created_at) VALUES (?, 'x', ?, 'matched', 0)",
                (list_id, place_id))


# ---------------------------------------------------------------- the plan's tests
def test_not_lately_uses_rest_days_and_audience(con, clock):
    clock.set("2026-11-14T18:00")
    a, k = mk_person(con, "Alex"), mk_person(con, "Riley", grp="kid")
    old = mk_place(con, name="Old Favorite")
    recent = mk_place(con, name="Recent")
    mk_visit(con, old, "2026-09-01", {a: 5, k: 5})
    mk_visit(con, recent, "2026-11-01", {a: 5, k: 5})
    assert [p["name"] for p in decide.decide(con, {"mode": "not_lately"})["places"]] == ["Old Favorite"]
    # REST_DAYS is 45: exactly 45 days ago is not "more than", 46 is
    edge = mk_place(con, name="Edge")
    mk_visit(con, edge, "2026-09-30", {a: 5})           # 45 days before Nov 14
    past_edge = mk_place(con, name="Past Edge")
    mk_visit(con, past_edge, "2026-09-29", {a: 4})      # 46 days
    assert names(con, mode="not_lately") == ["Old Favorite", "Past Edge"]
    # the audience decides whose last visit counts: Alex went to Old Favorite recently without Riley
    mk_visit(con, old, "2026-11-10", {a: None})
    assert "Old Favorite" not in names(con, mode="not_lately")
    assert names(con, mode="not_lately", audience="kids") == ["Old Favorite"]


def test_not_lately_needs_a_score_of_four_and_orders_longest_first(con, clock):
    clock.set("2026-11-14T18:00")
    a, s = mk_person(con, "Alex"), mk_person(con, "Sam")
    meh = mk_place(con, name="Meh Example")
    mk_visit(con, meh, "2026-01-01", {a: 4, s: 3})       # mean 3.5
    unrated = mk_place(con, name="Unrated Example")
    mk_visit(con, unrated, "2026-01-01", {a: None})
    older = mk_place(con, name="Older Example")
    mk_visit(con, older, "2026-03-01", {a: 4})
    tie_low = mk_place(con, name="Tie Low")
    mk_visit(con, tie_low, "2026-05-01", {a: 4})
    tie_high = mk_place(con, name="Tie High")
    mk_visit(con, tie_high, "2026-05-01", {a: 5})
    assert names(con, mode="not_lately") == ["Older Example", "Tie High", "Tie Low"]
    # Sam alone: Meh Example is a 3 for Sam, and Sam has no rating of the rest
    assert names(con, mode="not_lately", audience="Sam") == []


def test_kids_audience_ignores_adult_only_visits(con, clock):
    clock.set("2026-11-14T18:00")
    a, k = mk_person(con, "Alex"), mk_person(con, "Riley", grp="kid")
    adults_only = mk_place(con, name="Adults Only Example")
    mk_visit(con, adults_only, "2026-11-01", {a: 5})
    together = mk_place(con, name="Together Example")
    mk_visit(con, together, "2026-11-01", {a: 2, k: 5})
    assert names(con, mode="never_been", audience="kids") == ["Adults Only Example"]
    assert names(con, mode="never_been", audience="adults") == []
    assert names(con, mode="never_been") == []
    card = decide.decide(con, {"mode": "any", "audience": "kids"})["places"]
    by_name = {c["name"]: c for c in card}
    assert by_name["Adults Only Example"]["score"] is None and by_name["Adults Only Example"]["last_visit"] is None
    assert by_name["Together Example"]["score"] == 5
    assert decide.decide(con, {"mode": "any", "audience": "Alex"})["places"][1]["score"] == 2


def test_ineligible_places_never_appear(con, clock):   # Review Focus 1
    clock.set("2026-11-14T18:00")
    a = mk_person(con, "Alex")
    occasion = mk_tag(con, "birthday dinner", occasion=True)
    lst = mk_list(con, "Taco Trail")
    _set_baseline(con, clock.t - 86400 * 30)
    bad = {"Sold": {"ownership": "no"}, "Closed": {"standing": "closed"}, "Paused": {"standing": "temp_closed"},
           "Good": {}}
    for label, cols in bad.items():
        # one of each that every mode would offer if it were eligible: a rested favourite, and an unvisited,
        # starred, new-in-town occasion place
        rested = mk_place(con, name=f"{label} Rested", **cols)
        mk_visit(con, rested, "2026-01-01", {a: 5})
        fresh = mk_place(con, name=f"{label} Fresh", want_to_try=1, want_since=1, origin="scan",
                         first_seen_at=clock.t - 86400, **cols)
        for pid in (rested, fresh):
            _tag(con, pid, occasion)
            _on_list(con, lst, pid)
    for mode in decide.MODES:
        for params in ({}, {"list": str(lst)}, {"tag": "birthday dinner"}, {"audience": "Alex"}):
            got = names(con, mode=mode, limit=50, **params)
            assert got and all(n.startswith("Good ") for n in got), (mode, params, got)
    assert names(con, mode="not_lately") == ["Good Rested"]
    assert names(con, mode="want") == names(con, mode="never_been") == names(con, mode="new_in_town") == ["Good Fresh"]
    assert names(con, mode="occasion") == ["Good Rested", "Good Fresh"]      # rated first
    assert names(con, mode="any") == ["Good Fresh", "Good Rested"]


def test_unsure_places_appear_marked(con):
    mk_place(con, name="Sure Example")
    mk_place(con, name="Unsure Example", ownership="unsure")
    cards = {c["name"]: c for c in decide.decide(con, {"mode": "any"})["places"]}
    assert cards["Unsure Example"]["unsure"] is True and cards["Sure Example"]["unsure"] is False


def test_new_in_town_needs_first_seen_after_baseline(con, clock):
    clock.set("2026-11-14T18:00")
    base = clock.t - 86400 * 60
    a = mk_person(con, "Alex")
    mk_place(con, name="Backlog Example", origin="scan", first_seen_at=base - 100)
    mk_place(con, name="Fresh Example", origin="scan", first_seen_at=base + 86400)
    mk_place(con, name="Fresher Example", origin="scan", first_seen_at=base + 86400 * 2)
    visited = mk_place(con, name="Visited Example", origin="scan", first_seen_at=base + 86400 * 3)
    mk_visit(con, visited, "2026-11-01", {a: 4})
    mk_place(con, name="Hand Example", first_seen_at=base + 86400)
    assert names(con, mode="new_in_town") == []                     # no scan has finished: nothing is new yet
    _set_baseline(con, base)
    assert names(con, mode="new_in_town") == ["Fresher Example", "Fresh Example"]
    # "never visited by anyone", not just the audience
    mk_person(con, "Riley", grp="kid")
    assert names(con, mode="new_in_town", audience="kids") == ["Fresher Example", "Fresh Example"]


def test_want_is_oldest_starred_first_and_unvisited_by_the_audience(con):
    a = mk_person(con, "Alex")
    mk_person(con, "Riley", grp="kid")
    mk_place(con, name="Starred Late", want_to_try=1, want_since=300)
    mk_place(con, name="Starred Early", want_to_try=1, want_since=100)
    went = mk_place(con, name="Starred Visited", want_to_try=1, want_since=200)
    mk_visit(con, went, "2026-11-01", {a: 4})
    mk_place(con, name="Not Starred")
    assert names(con, mode="want") == ["Starred Early", "Starred Late"]
    assert names(con, mode="want", audience="kids") == ["Starred Early", "Starred Visited", "Starred Late"]


def test_never_been_is_newest_added_first(con, clock):
    clock.set("2026-11-01T12:00")
    mk_place(con, name="First Added")
    clock.advance(60)
    mk_place(con, name="Second Added", want_to_try=1, want_since=clock.t)
    assert names(con, mode="never_been") == ["Second Added", "First Added"]


def test_occasion_uses_flag_not_label_and_ignores_rest_days(con, clock):
    clock.set("2026-11-14T18:00")
    a = mk_person(con, "Alex")
    flagged = mk_tag(con, "Fancy night", occasion=True)
    lookalike = mk_tag(con, "special occasion dinner", occasion=False)   # a label that sounds right, no flag
    high = mk_place(con, name="High Example")
    low = mk_place(con, name="Low Example")
    unrated = mk_place(con, name="Unrated Example")
    label_only = mk_place(con, name="Label Only Example")
    for pid in (high, low, unrated):
        _tag(con, pid, flagged)
    _tag(con, label_only, lookalike)
    mk_visit(con, high, "2026-11-13", {a: 5})          # yesterday: rest days don't matter here
    mk_visit(con, low, "2026-11-13", {a: 3})
    assert names(con, mode="occasion") == ["High Example", "Low Example", "Unrated Example"]
    con.execute("UPDATE tag SET label = 'Renamed' WHERE id = ?", (flagged,))
    assert names(con, mode="occasion") == ["High Example", "Low Example", "Unrated Example"]


def test_open_now_keeps_unknown_unless_strict(con, clock):
    clock.set("2026-11-14T18:00")    # a Saturday
    mk_place(con, name="Open Example", hours="Mo-Su 11:00-21:00")
    mk_place(con, name="Shut Example", hours="Mo-Fr 11:00-21:00")
    mk_place(con, name="Unknown Example", hours="Mo-Fr 11:00-21:00; PH off")
    mk_place(con, name="No Hours Example")
    assert names(con, mode="any") == ["No Hours Example", "Open Example", "Shut Example", "Unknown Example"]
    assert names(con, mode="any", open_now="true") == ["No Hours Example", "Open Example", "Unknown Example"]
    assert names(con, mode="any", open_now="strict") == ["Open Example"]
    assert len(names(con, mode="any", open_now="false")) == 4
    cards = {c["name"]: c for c in decide.decide(con, {"mode": "any"})["places"]}
    assert cards["Open Example"]["open"] is True and cards["Open Example"]["closes_at"] == "9 pm"
    assert cards["Shut Example"]["open"] is False and cards["Shut Example"]["closes_at"] is None
    assert cards["Unknown Example"]["open"] is None and cards["No Hours Example"]["open"] is None


def test_now_argument_overrides_the_clock(con, clock):
    clock.set("2026-11-14T18:00")
    mk_place(con, name="Lunch Example", hours="Mo-Su 11:00-14:00")
    assert names(con, mode="any", open_now="strict") == []
    from datetime import datetime
    got = decide.decide(con, {"mode": "any", "open_now": "strict"}, now=datetime(2026, 11, 14, 12, 0))
    assert [p["name"] for p in got["places"]] == ["Lunch Example"]


def test_seed_is_reproducible_and_changes_order(con):
    for i in range(12):
        mk_place(con, name=f"Place {i:02d}")
    plain = names(con, mode="any", limit=50)
    assert plain == sorted(plain)
    one = names(con, mode="any", seed="7", limit=50)
    assert one == names(con, mode="any", seed="7", limit=50) == names(con, mode="any", seed=7, limit=50)
    assert sorted(one) == plain and one != plain
    assert names(con, mode="any", seed="8", limit=50) != one
    # shuffled after filtering and before paging: the pages of one seed don't overlap and cover the list
    pages = [names(con, mode="any", seed="7", limit=5, offset=o) for o in (0, 5, 10)]
    assert sum(pages, []) == one
    result = decide.decide(con, {"mode": "any", "seed": "7", "limit": "5"})
    assert result["total"] == 12 and len(result["places"]) == 5 and result["mode"] == "any"


def test_list_filter_and_liked_by_all(con, clock):
    clock.set("2026-11-14T18:00")
    a, s = mk_person(con, "Alex"), mk_person(con, "Sam")
    trail, other = mk_list(con, "Taco Trail"), mk_list(con, "Other List")
    loved = mk_place(con, name="Loved Example")
    split = mk_place(con, name="Split Example")
    untried = mk_place(con, name="Untried Example")
    off_list = mk_place(con, name="Off List Example")
    for pid in (loved, split, untried):
        _on_list(con, trail, pid)
    _on_list(con, other, loved)
    mk_visit(con, loved, "2026-01-01", {a: 5, s: 4})
    mk_visit(con, split, "2026-01-01", {a: 5, s: 2})
    mk_visit(con, off_list, "2026-01-01", {a: 5, s: 5})
    assert names(con, mode="any", list=str(trail)) == ["Loved Example", "Split Example", "Untried Example"]
    assert names(con, mode="never_been", list=str(trail)) == ["Untried Example"]     # "Taco Trail places we haven't tried"
    assert names(con, mode="any", list=[str(trail), str(other)]) == ["Loved Example"]
    assert names(con, mode="any", list="Taco Trail") == ["Loved Example", "Split Example", "Untried Example"]
    assert names(con, mode="any", liked_by_all="true") == ["Loved Example", "Off List Example"]
    assert names(con, mode="any", liked_by_all="true", audience="Alex") == ["Loved Example", "Off List Example",
                                                                            "Split Example"]
    assert names(con, mode="any", liked_by_all="true", list=str(trail)) == ["Loved Example"]
    with pytest.raises(ApiError) as e:
        names(con, mode="any", list="999")
    assert e.value.status == 400


def test_me_audience_without_link_is_400(editor, con):
    mk_person(con, "Sam", email="sam@example.com")
    r = editor.get("/api/decide?audience=me")
    assert r.status_code == 400 and r.json["error"] == "Link your email to a person in Settings."
    mk_person(con, "Alex", email="alex@example.com")
    assert editor.get("/api/decide?audience=me").status_code == 200


def test_bad_mode_is_400(editor):
    r = editor.get("/api/decide?mode=whatever")
    assert r.status_code == 400 and "whatever" in r.json["error"]
    for bad in ("limit=0", "limit=51", "limit=x", "limit=--5", "limit=%C2%B2", "offset=-1", "open_now=maybe", "near=1",
                "near=91,0", "near=a,b", "liked_by_all=perhaps", "town=Atlantis", "audience=Nobody", "tag=nonesuch",
                "tag=%C2%B2", "list=--1"):
        r = editor.get(f"/api/decide?{bad}")
        assert r.status_code == 400, bad
        assert r.json["error"], bad
    assert editor.get("/api/decide?audience=Nobody").json["error"] == "No one called Nobody."
    assert editor.get("/api/decide?seed=--5&seed=%C2%B2").status_code == 200      # any short text shuffles


# ---------------------------------------------------------------- the rest of §7
def test_filters_cuisine_town_and_tags(con):
    groups, room = mk_tag(con, "big table"), mk_tag(con, "quiet room")
    taqueria = mk_place(con, name="Casa Ejemplo", cuisine="Mexican, Tacos", town="Concord")
    pho = mk_place(con, name="Pho Example", cuisine="Vietnamese Phở", town="Martinez")
    mk_place(con, name="Other Example", cuisine="Thai", town="Martinez")
    _tag(con, taqueria, groups)
    _tag(con, taqueria, room)
    _tag(con, pho, groups)
    assert names(con, mode="any", cuisine="taco") == ["Casa Ejemplo"]
    assert names(con, mode="any", cuisine="PHO") == ["Pho Example"]
    assert names(con, mode="any", town="martinez") == ["Other Example", "Pho Example"]
    assert names(con, mode="any", tag=[str(groups)]) == ["Casa Ejemplo", "Pho Example"]
    assert names(con, mode="any", tag=[str(groups), "Quiet Room"]) == ["Casa Ejemplo"]
    card = decide.decide(con, {"mode": "any", "cuisine": "taco"})["places"][0]
    assert [t["label"] for t in card["tags"]] == ["big table", "quiet room"]


def test_near_adds_distance_and_sorts_any_and_never_been(con):
    mk_place(con, name="Far Example", lat=38.0194, lng=-122.1341)       # Martinez
    mk_place(con, name="Close Example", lat=37.9790, lng=-122.0311)     # by Concord's centre
    mk_place(con, name="Pinless Example")
    near = "37.9780,-122.0311"
    assert names(con, mode="any", near=near) == ["Close Example", "Far Example", "Pinless Example"]
    assert names(con, mode="never_been", near=near) == ["Close Example", "Far Example", "Pinless Example"]
    cards = decide.decide(con, {"mode": "any", "near": near})["places"]
    assert 0.1 < cards[0]["distance_km"] < 0.2 and cards[2]["distance_km"] is None
    assert "distance_km" not in decide.decide(con, {"mode": "any"})["places"][0]


def test_card_shape_and_last_visit(con, clock):
    clock.set("2026-11-14T18:00")
    a, s, k = mk_person(con, "Alex"), mk_person(con, "Sam"), mk_person(con, "Riley", grp="kid")
    pid = mk_place(con, name="Casa Ejemplo", cuisine="Mexican", want_to_try=0)
    mk_visit(con, pid, "2026-06-01", {a: 3})
    mk_visit(con, pid, "2026-08-01", {s: 5, k: 4})
    card = decide.decide(con, {"mode": "any"})["places"][0]
    assert set(card) == {"id", "name", "town", "cuisine", "unsure", "want_to_try", "tags", "last_visit", "score",
                         "open", "closes_at"}
    assert card["last_visit"] == {"on": "2026-08-01", "people": [{"name": "Sam", "rating": 5},
                                                                {"name": "Riley", "rating": 4}]}
    assert card["score"] == 4 and card["want_to_try"] is False
    assert decide.decide(con, {"mode": "any", "audience": "Alex"})["places"][0]["last_visit"]["on"] == "2026-06-01"


def test_parse_params_reads_query_strings_and_mcp_kwargs():
    p = decide.parse_params({})
    assert p["mode"] == "not_lately" and p["audience"] == "everyone" and p["limit"] == 10 and p["offset"] == 0
    assert p["open_now"] is False and p["tag"] == [] and p["list"] == [] and p["near"] is None
    from werkzeug.datastructures import MultiDict
    q = decide.parse_params(MultiDict([("mode", "any"), ("tag", "a"), ("tag", "b"), ("near", "37.9, -122.0"),
                                       ("open_now", "strict"), ("liked_by_all", "1"), ("limit", "50")]))
    assert q["tag"] == ["a", "b"] and q["near"] == (37.9, -122.0) and q["open_now"] == "strict"
    assert q["liked_by_all"] is True and q["limit"] == 50
    kw = decide.parse_params({"mode": "occasion", "tag": "a", "open_now": True, "liked_by_all": False, "limit": 3})
    assert kw["tag"] == ["a"] and kw["open_now"] is True and kw["liked_by_all"] is False and kw["limit"] == 3
    assert decide.parse_params(q) == q           # parsing twice changes nothing
    with pytest.raises(decide.ParamError):
        decide.parse_params({"mode": "any", "limit": True})


def test_route_shape_and_editor_only(editor, client, con):
    mk_place(con, name="Casa Ejemplo")
    assert client.get("/api/decide").status_code == 401
    r = editor.get("/api/decide?mode=any&tag=")
    assert r.status_code == 200 and set(r.json) == {"mode", "total", "places"}
    assert r.json["total"] == 1 and r.json["places"][0]["name"] == "Casa Ejemplo"
    assert r.headers["Cache-Control"] == "no-store"


def test_read_key_can_decide_for_an_acting_editor(keyed, con):
    a = mk_person(con, "Alex", email="alex@example.com")
    pid = mk_place(con, name="Casa Ejemplo")
    mk_visit(con, pid, "2026-01-01", {a: 5})
    mk_place(con, name="Pho Example")
    r = keyed("read", as_email="alex@example.com").get("/api/decide?mode=never_been&audience=me")
    assert r.status_code == 200 and [p["name"] for p in r.json["places"]] == ["Pho Example"]


def test_choices_for_the_filter_row(editor, con):
    mk_place(con, name="Casa Ejemplo", cuisine="Mexican")
    mk_place(con, name="Casa Dos", cuisine="mexican")
    mk_place(con, name="Pho Example", cuisine="Vietnamese")
    mk_place(con, name="Sold Example", cuisine="Burgers", ownership="no")
    mk_list(con, "Taco Trail")
    mk_person(con, "Alex")
    r = editor.get("/api/decide/choices")
    assert r.status_code == 200
    assert r.json["cuisines"] == ["Mexican", "Vietnamese"]
    assert [t["name"] for t in r.json["towns"]][0] == "Concord"
    assert [x["name"] for x in r.json["lists"]] == ["Taco Trail"]
    assert [x["name"] for x in r.json["people"]] == ["Alex"]
    assert {"id", "label", "occasion"} <= set(r.json["tags"][0])
    assert r.json["modes"] == list(decide.MODES)
