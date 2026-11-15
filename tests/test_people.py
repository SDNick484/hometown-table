"""People (spec §6.1): name rules, the optional editor email that makes "me" work, deactivate-not-delete."""
import pytest

import access
import people
from common import ApiError
from conftest import EDITOR, EDITOR2, mk_person, mk_place, mk_visit


def post(editor, **body):
    return editor.post("/api/people", json={"grp": "adult", **body})


def test_reserved_and_bad_names_are_400(editor):
    for n in ("everyone", "Kids", "a,b", "123"):
        assert editor.post("/api/people", json={"name": n, "grp": "adult"}).status_code == 400
    r = post(editor, name="ADULTS")
    assert r.status_code == 400 and r.get_json() == {"error": "That name is reserved."}
    assert post(editor, name="").status_code == 400
    assert post(editor, name="x" * 41).status_code == 400
    assert editor.post("/api/people", json={"name": "Jordan", "grp": "toddler"}).status_code == 400


def test_names_are_unique_folded_including_inactive(editor, con):
    assert post(editor, name="Riley").status_code == 201
    assert post(editor, name="riley").status_code == 400
    old = mk_person(con, "Casey")
    con.execute("UPDATE person SET active = 0 WHERE id = ?", (old,))
    assert post(editor, name="CASEY").status_code == 400


def test_create_lists_in_order_and_inactive_hidden_by_default(editor, con):
    a = post(editor, name="Alex").get_json()
    b = post(editor, name="Riley", grp="kid").get_json()
    assert [p["name"] for p in editor.get("/api/people").get_json()["people"]] == ["Alex", "Riley"]
    editor.patch(f"/api/people/{b['id']}", json={"active": False})
    assert [p["id"] for p in editor.get("/api/people").get_json()["people"]] == [a["id"]]
    assert len(editor.get("/api/people?inactive=1").get_json()["people"]) == 2


def test_email_must_be_an_editor(editor):
    r = post(editor, name="Alex", email="stranger@example.com")
    assert r.status_code == 400 and r.get_json() == {"error": "That email isn't an editor."}
    r = post(editor, name="Alex", email=EDITOR.upper())
    assert r.status_code == 201 and r.get_json()["email"] == EDITOR      # stored lowercased


def test_email_is_unique_and_can_be_cleared(editor):
    first = post(editor, name="Alex", email=EDITOR).get_json()
    assert post(editor, name="Sam", email=EDITOR).status_code == 409
    sam = post(editor, name="Sam", email=EDITOR2).get_json()
    assert editor.patch(f"/api/people/{sam['id']}", json={"email": EDITOR}).status_code == 409
    r = editor.patch(f"/api/people/{first['id']}", json={"email": None})
    assert r.status_code == 200 and r.get_json()["email"] is None
    assert editor.patch(f"/api/people/{sam['id']}", json={"email": EDITOR}).status_code == 200


def test_email_link_makes_me_work(editor, client, con):
    person = post(editor, name="Alex", email=EDITOR).get_json()["id"]
    with editor.application.test_request_context(headers={"Cf-Access-Jwt-Assertion": editor.environ_base["HTTP_CF_ACCESS_JWT_ASSERTION"]}):
        assert access.me_person_id(con) == person


def test_update_edits_fields_and_rejects_bad_ones(editor):
    pid = post(editor, name="Jordan").get_json()["id"]
    r = editor.patch(f"/api/people/{pid}", json={"name": "Jordan P", "grp": "kid", "sort_order": 5})
    assert r.get_json() | {} == {"id": pid, "name": "Jordan P", "grp": "kid", "email": None, "sort_order": 5, "active": True}
    assert editor.patch(f"/api/people/{pid}", json={"name": "everyone"}).status_code == 400
    assert editor.patch(f"/api/people/{pid}", json={"grp": "x"}).status_code == 400
    assert editor.patch(f"/api/people/{pid}", json={"active": "yes"}).status_code == 400
    assert editor.patch("/api/people/9999", json={"name": "Nobody"}).status_code == 404
    assert editor.patch(f"/api/people/{pid}", json={"name": "Jordan"}).status_code == 200   # own name is not a clash


def test_person_without_visits_is_deleted(editor, con):
    pid = mk_person(con, "Casey")
    r = editor.delete(f"/api/people/{pid}", json={})
    assert r.status_code == 200 and r.get_json() == {"result": "deleted"}
    assert con.execute("SELECT COUNT(*) FROM person").fetchone()[0] == 0
    assert editor.delete("/api/people/9999", json={}).status_code == 404


def test_person_with_visits_is_deactivated_not_deleted(editor, con):
    pid = mk_person(con, "Sam")
    mk_visit(con, mk_place(con), "2026-11-01", {pid: 4})
    r = editor.delete(f"/api/people/{pid}", json={})
    assert r.get_json() == {"result": "deactivated"}
    assert con.execute("SELECT active FROM person WHERE id = ?", (pid,)).fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM visit_person").fetchone()[0] == 1


def test_people_module_matches_the_plan(con):
    pid = people.create(con, " Riley ", "kid")
    assert people.list(con)[0]["name"] == "Riley"
    people.update(con, pid, name="Riley R")
    assert people.deactivate(con, pid) == "deleted"
    with pytest.raises(ApiError):
        people.update(con, pid, name="Gone")


def test_read_key_can_list_but_not_write(keyed):
    assert keyed("read").get("/api/people").status_code == 200
    assert keyed("read").post("/api/people", json={"name": "A", "grp": "adult"}, headers={"X-Hometown": "1"}).status_code == 403
