"""Task 2: places, ownership and standing with dated history, Past places, search, duplicates, concurrency."""
import pytest

import places
from common import ApiError
from conftest import mk_person, mk_place, mk_tag, mk_visit


def test_sold_to_group_keeps_visits_and_moves_to_past(editor, con, clock):   # Review Focus 1
    clock.set("2026-11-14T12:00")
    pid = mk_place(con)
    a = mk_person(con, "Alex")
    mk_visit(con, pid, "2026-10-01", {a: 5})
    r = editor.post(f"/api/places/{pid}/ownership", json={"value": "no", "reason": "sold to a group"})
    assert r.status_code == 200
    place = editor.get(f"/api/places/{pid}").json
    assert place["ownership"] == "no" and place["visit_count"] == 1
    assert place["history"][0] == {"kind": "ownership", "old": "counts", "new": "no",
                                   "reason": "sold to a group", "on": "2026-11-14", "by": "alex@example.com"}
    assert [p["id"] for p in editor.get("/api/past").json["places"]] == [pid]


def test_past_lists_closed_and_not_counting_newest_first_with_visit_counts(editor, con, clock):
    clock.set("2026-11-14T12:00")
    a = mk_person(con, "Alex")
    sold, shut, fine = mk_place(con, "Casa Ejemplo"), mk_place(con, "Pho Example"), mk_place(con, "Taqueria Muestra")
    mk_visit(con, sold, "2026-01-02", {a: 4})
    mk_visit(con, sold, "2026-02-02", {a: 4})
    editor.post(f"/api/places/{sold}/ownership", json={"value": "no", "reason": "sold", "on": "2026-06-01"})
    editor.post(f"/api/places/{shut}/standing", json={"value": "closed", "reason": "retired", "on": "2026-08-15"})
    rows = editor.get("/api/past").json["places"]
    assert [r["id"] for r in rows] == [shut, sold] and fine not in [r["id"] for r in rows]
    assert rows[0]["kind"] == "closed" and rows[0]["since"] == "2026-08-15" and rows[0]["visit_count"] == 0
    assert rows[1]["kind"] == "ownership" and rows[1]["reason"] == "sold" and rows[1]["visit_count"] == 2
    # a past place can be changed back
    assert editor.post(f"/api/places/{sold}/ownership", json={"value": "counts", "reason": "bought back"}).status_code == 200
    assert [r["id"] for r in editor.get("/api/past").json["places"]] == [shut]


@pytest.mark.parametrize("kind", ["ownership", "standing"])
def test_change_needs_a_reason(editor, con, kind):
    pid = mk_place(con)
    value = "no" if kind == "ownership" else "closed"
    for body in ({"value": value}, {"value": value, "reason": "  "}):
        r = editor.post(f"/api/places/{pid}/{kind}", json=body)
        assert r.status_code == 400 and r.json["error"] == "Give a reason."
    assert editor.post(f"/api/places/{pid}/{kind}", json={"value": "maybe", "reason": "x"}).status_code == 400
    assert editor.post(f"/api/places/{pid}/{kind}", json={"value": value, "reason": "x" * 201}).status_code == 400
    assert editor.post(f"/api/places/{pid}/{kind}", json={"value": value, "reason": "x", "on": "2999-01-01"}).status_code == 400
    assert editor.get(f"/api/places/{pid}").json["history"] == []


def test_delete_with_visits_is_409_and_without_is_204(editor, con):
    pid, empty = mk_place(con), mk_place(con, "Pho Example")
    a = mk_person(con, "Alex")
    mk_visit(con, pid, "2026-10-01", {a: 5})
    r = editor.delete(f"/api/places/{pid}", json={})
    assert r.status_code == 409
    assert r.json["error"] == "This place has visits. Mark it closed or doesn't count instead."
    assert editor.delete(f"/api/places/{empty}", json={}).status_code == 204
    assert editor.get(f"/api/places/{empty}").status_code == 404
    assert editor.get(f"/api/places/{pid}").status_code == 200


def test_stale_version_is_409_and_saves_nothing(editor, editor2, con):
    pid = mk_place(con)
    v = editor.get(f"/api/places/{pid}").json["version"]
    assert editor.patch(f"/api/places/{pid}", json={"version": v, "cuisine": "Mexican"}).status_code == 200
    r = editor2.patch(f"/api/places/{pid}", json={"version": v, "cuisine": "Thai", "notes": "x"})
    assert r.status_code == 409 and r.json["error"] == "Someone else just changed this place. Reload to see it."
    place = editor.get(f"/api/places/{pid}").json
    assert place["cuisine"] == "Mexican" and place["notes"] is None and place["version"] == v + 1
    assert editor.patch(f"/api/places/{pid}", json={"cuisine": "Thai"}).status_code == 400    # version required


def test_edit_cannot_change_ownership_or_standing_directly(editor, con):
    pid = mk_place(con)
    v = editor.get(f"/api/places/{pid}").json["version"]
    assert editor.patch(f"/api/places/{pid}", json={"version": v, "ownership": "no"}).status_code == 400
    assert editor.patch(f"/api/places/{pid}", json={"version": v, "standing": "closed"}).status_code == 400


def test_duplicate_within_150m_needs_add_anyway(editor, con):
    first = mk_place(con, "Casa Ejemplo", lat=37.9780, lng=-122.0311)
    body = {"name": "Casa Ejemplo Restaurant", "ownership": "unsure", "ownership_reason": "new", "lat": 37.9785, "lng": -122.0311}
    r = editor.post("/api/places", json=body)
    assert r.status_code == 409 and r.json["duplicate_of"] == first
    r = editor.post("/api/places", json={**body, "add_anyway": True})
    assert r.status_code == 201 and r.json["id"] != first
    far = editor.post("/api/places", json={**body, "name": "Casa Ejemplo", "lat": 37.9880})   # about 1.1 km away
    assert far.status_code == 201
    other = editor.post("/api/places", json={**body, "name": "Pho Example"})
    assert other.status_code == 201


def test_create_needs_name_verdict_and_reason_and_records_who_and_origin(editor, con):
    ok = {"name": "Casa Ejemplo", "ownership": "counts", "ownership_reason": "family runs it"}
    for bad in ({**ok, "name": ""}, {**ok, "ownership": "maybe"}, {k: v for k, v in ok.items() if k != "ownership_reason"},
                {**ok, "name": "x" * 201}):
        assert editor.post("/api/places", json=bad).status_code == 400
    r = editor.post("/api/places", json=ok)
    assert r.status_code == 201
    p = r.json
    assert (p["origin"], p["added_by"], p["standing"], p["version"], p["want_to_try"]) == ("hand", "alex@example.com", "open", 1, 0)


def test_create_with_tags_want_and_foreign_ids(con):
    t = mk_tag(con, "kid-friendly")
    pid = places.create(con, {"name": "Casa Ejemplo", "ownership": "counts", "ownership_reason": "x", "want_to_try": True,
                              "tag_ids": [t], "origin": "scan", "first_seen_at": 5, "google_place_id": "g1"}, "alex@example.com")
    p = places.get(con, pid)
    assert [x["label"] for x in p["tags"]] == ["kid-friendly"] and p["want_to_try"] == 1 and p["want_since"]
    assert p["origin"] == "scan" and p["first_seen_at"] == 5 and p["google_place_id"] == "g1"
    with pytest.raises(ApiError) as e:
        places.create(con, {"name": "Other", "ownership": "counts", "ownership_reason": "x", "tag_ids": [999]}, "x")
    assert e.value.status == 400
    with pytest.raises(ApiError) as e:
        places.create(con, {"name": "Dup Id", "ownership": "counts", "ownership_reason": "x", "google_place_id": "g1"}, "x")
    assert e.value.status == 409


def test_town_from_address_city(editor):
    r = editor.post("/api/places", json={"name": "Casa Ejemplo", "ownership": "counts", "ownership_reason": "x",
                                          "address": "100 Main St, Martinez, CA 94553"})
    assert r.json["town"] == "Martinez"
    r = editor.post("/api/places", json={"name": "Pho Example", "ownership": "counts", "ownership_reason": "x",
                                          "address": "1 Nowhere Rd", "lat": 37.9780, "lng": -122.0311})
    assert r.json["town"] == "Concord"
    r = editor.post("/api/places", json={"name": "Chain Burger Example", "ownership": "no", "ownership_reason": "x", "town": "Oakland"})
    assert r.json["town"] == "Oakland"


def test_set_standing_clears_closure_flag(editor, con, clock):
    clock.set("2026-11-14T12:00")
    pid = mk_place(con, closure_flag="CLOSED_PERMANENTLY")
    r = editor.post(f"/api/places/{pid}/standing", json={"value": "closed", "reason": "confirmed shut"})
    assert r.status_code == 200
    p = editor.get(f"/api/places/{pid}").json
    assert p["closure_flag"] is None and p["standing"] == "closed" and p["standing_since"] == "2026-11-14"
    assert p["history"][0]["kind"] == "standing" and p["history"][0]["old"] == "open" and p["history"][0]["new"] == "closed"


def test_search_is_accent_insensitive_and_hides_past_by_default(editor, con):
    pho = mk_place(con, "Phở Example", cuisine="Vietnamese")
    mk_place(con, "Casa Ejemplo", cuisine="Mexican", town="Martinez", notes="best jalapeño poppers")
    gone = mk_place(con, "Pho Old Example", cuisine="Vietnamese", standing="closed")
    sold = mk_place(con, "Pho Sold", ownership="no")
    ids = lambda q, **kw: [p["id"] for p in editor.get("/api/places", query_string={"q": q, **kw}).json["places"]]  # noqa: E731
    assert ids("pho") == [pho]
    assert sorted(ids("pho", past="1")) == sorted([pho, gone, sold])
    assert len(ids("jalapeno")) == 1 and len(ids("mexican martinez")) == 1 and ids("zzz") == []
    assert len(ids("")) == 2
    assert editor.get("/api/places", query_string={"q": "100%"}).json["places"] == []      # % is not a wildcard


def test_tags_and_want_endpoints(editor, con):
    pid = mk_place(con)
    t1, t2 = mk_tag(con, "private room", True), mk_tag(con, "kid-friendly")
    r = editor.put(f"/api/places/{pid}/tags", json={"tag_ids": [t1, t2]})
    assert r.status_code == 200 and [t["label"] for t in r.json["tags"]] == ["private room", "kid-friendly"]
    assert [t["label"] for t in editor.put(f"/api/places/{pid}/tags", json={"tag_ids": [t2]}).json["tags"]] == ["kid-friendly"]
    assert editor.put(f"/api/places/{pid}/tags", json={"tag_ids": [999]}).status_code == 400
    assert editor.put(f"/api/places/{pid}/tags", json={"tag_ids": "x"}).status_code == 400
    r = editor.put(f"/api/places/{pid}/want", json={"on": True})
    assert r.json["want_to_try"] == 1 and r.json["want_since"]
    assert editor.put(f"/api/places/{pid}/want", json={"on": False}).json["want_since"] is None


def test_get_has_last_visit_lists_and_count(editor, con):
    from conftest import mk_list
    pid = mk_place(con)
    a, s = mk_person(con, "Alex"), mk_person(con, "Sam")
    mk_visit(con, pid, "2026-03-01", {a: 2})
    mk_visit(con, pid, "2026-10-01", {a: 5, s: None})
    lid = mk_list(con, "Taco Trail")
    con.execute("INSERT INTO list_entry (list_id, line, place_id, status, created_at) VALUES (?, 'Casa', ?, 'matched', 1)", (lid, pid))
    p = editor.get(f"/api/places/{pid}").json
    assert p["visit_count"] == 2
    assert p["last_visit"] == {"on": "2026-10-01", "people": [{"name": "Alex", "rating": 5}, {"name": "Sam", "rating": None}]}
    assert p["lists"] == [{"id": lid, "name": "Taco Trail"}]
    assert editor.get(f"/api/places/{mk_place(con, 'Pho Example')}").json["last_visit"] is None
    assert editor.get("/api/places/9999").status_code == 404


def test_places_routes_need_an_editor_and_a_write_key(client, keyed, con):
    pid = mk_place(con)
    assert client.get(f"/api/places/{pid}").status_code == 401
    assert client.get("/api/past").status_code == 401
    assert keyed("read").get(f"/api/places/{pid}").status_code == 200
    r = keyed("read").post(f"/api/places/{pid}/ownership", json={"value": "no", "reason": "x"}, headers={"X-Hometown": "1"})
    assert r.status_code == 403 and r.json == {"error": "The read key can only read."}
    r = keyed("write").post(f"/api/places/{pid}/ownership", json={"value": "no", "reason": "x"}, headers={"X-Hometown": "1"})
    assert r.status_code == 200 and r.json["history"][0]["by"] == "mcp"
