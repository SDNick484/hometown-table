"""Tags and the occasion flag (spec §6.4)."""
import pytest

import tags
from common import ApiError
from conftest import mk_place, mk_tag


def test_seed_runs_once_with_three_occasion_tags(con):
    tags.seed(con); tags.seed(con)
    assert [t["label"] for t in tags.list(con)] == [label for label, _ in tags.SEED]
    assert [t["label"] for t in tags.list(con) if t["occasion"]] == ["good for groups", "private room", "special occasion"]
    assert len(tags.list(con)) == 6


def test_seed_is_registered_as_an_init_hook_and_app_start_seeded(con):
    import app
    assert tags.seed in app.INIT_HOOKS
    assert len(tags.list(con)) == 6          # the test setup ran init_db()


def test_seed_leaves_existing_tags_alone(con):
    con.execute("DELETE FROM tag")
    mk_tag(con, "mine")
    tags.seed(con)
    assert [t["label"] for t in tags.list(con)] == ["mine"]


def test_create_rename_and_occasion(editor):
    r = editor.post("/api/tags", json={"label": "patio", "occasion": True})
    assert r.status_code == 201 and r.get_json()["occasion"] is True
    tid = r.get_json()["id"]
    r = editor.patch(f"/api/tags/{tid}", json={"label": "outdoor seating", "occasion": False})
    assert r.get_json()["label"] == "outdoor seating" and r.get_json()["occasion"] is False
    assert editor.patch("/api/tags/9999", json={"label": "x"}).status_code == 404


def test_labels_are_unique_folded_and_at_most_40(editor):
    assert editor.post("/api/tags", json={"label": "Kid-Friendly"}).status_code == 400   # seeded as kid-friendly
    assert editor.post("/api/tags", json={"label": "y" * 41}).status_code == 400
    assert editor.post("/api/tags", json={"label": "  "}).status_code == 400
    assert editor.post("/api/tags", json={"label": "y" * 40}).status_code == 201
    tid = editor.get("/api/tags").get_json()["tags"][0]["id"]
    assert editor.patch(f"/api/tags/{tid}", json={"label": "PRIVATE ROOM"}).status_code == 400
    assert editor.patch(f"/api/tags/{tid}", json={"label": "Good For Groups"}).status_code == 200   # its own label


def test_occasion_must_be_a_boolean(editor):
    assert editor.post("/api/tags", json={"label": "x", "occasion": "yes"}).status_code == 400


def test_reorder_needs_every_id_once(editor):
    ids = [t["id"] for t in editor.get("/api/tags").get_json()["tags"]]
    r = editor.put("/api/tags/order", json={"ids": ids[::-1]})
    assert r.status_code == 200 and [t["id"] for t in r.get_json()["tags"]] == ids[::-1]
    assert [t["id"] for t in editor.get("/api/tags").get_json()["tags"]] == ids[::-1]
    for bad in (ids[:-1], ids + [ids[0]], ids[:-1] + [9999], "nope"):
        assert editor.put("/api/tags/order", json={"ids": bad}).status_code == 400


def test_delete_tag_reports_place_count(editor, con):
    tid = mk_tag(con, "patio")
    for _ in range(2):
        con.execute("INSERT INTO place_tag (place_id, tag_id) VALUES (?, ?)", (mk_place(con), tid))
    listed = {t["id"]: t for t in editor.get("/api/tags").get_json()["tags"]}
    assert listed[tid]["places"] == 2
    r = editor.delete(f"/api/tags/{tid}", json={})
    assert r.status_code == 200 and r.get_json() == {"removed_from": 2}
    assert con.execute("SELECT COUNT(*) FROM place_tag").fetchone()[0] == 0
    assert editor.delete(f"/api/tags/{tid}", json={}).status_code == 404


def test_tags_module_matches_the_plan(con):
    tid = tags.create(con, "patio")
    tags.rename(con, tid, "terrace")
    assert tags.delete(con, tid) == 0
    with pytest.raises(ApiError):
        tags.rename(con, tid, "again")
