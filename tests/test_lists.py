"""Curated lists and paste import (plan Task 11; spec §8.5)."""
import pytest

import common
import lists
import settings
from conftest import mk_candidate, mk_list, mk_place
from plugins.google import g_ids_page


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_PLACES_KEY", "test-key-not-real")


def _import(editor, lid, *lines):
    r = editor.post(f"/api/lists/{lid}/import", json={"lines": list(lines)})
    assert r.status_code == 200, r.json
    return r.json


def _search_calls(fake_http):
    return [c for c in fake_http.calls if "searchText" in c.url]


def test_messy_paste_is_cleaned_and_deduplicated(editor, con, fake_http):   # Review Focus 3
    lid = mk_list(con, "Example Taco Trail")
    fake_http.on("searchText", g_ids_page(["g1"]))
    r = editor.post(f"/api/lists/{lid}/import", json={"lines": [
        "1. Casa Ejemplo – great al pastor", "", "• casa ejemplo", "  - Taqueria Ejemplo: open late"]}).json
    assert r["new_candidates"] + r["linked_candidates"] + r["matched_places"] + r["unmatched"] == 2
    assert len(_search_calls(fake_http)) == 2          # the repeat was never searched


def test_dismissed_candidate_reopens_not_duplicates(editor, con, fake_http):   # Review Focus 3
    lid = mk_list(con, "Example Taco Trail")
    cid = mk_candidate(con, google_place_id="g1", status="dismissed", origin="scan", decided_at=5)
    fake_http.on("searchText", g_ids_page(["g1"]))
    r = _import(editor, lid, "Casa Ejemplo")
    assert (r["linked_candidates"], r["new_candidates"]) == (1, 0)
    assert con.execute("SELECT COUNT(*) FROM candidate").fetchone()[0] == 1
    assert con.execute("SELECT status FROM candidate WHERE id = ?", (cid,)).fetchone()[0] == "new"
    entry = con.execute("SELECT * FROM list_entry").fetchone()
    assert (entry["candidate_id"], entry["status"], entry["list_id"]) == (cid, "matched", lid)


def test_backlog_goes_back_to_new_and_a_new_id_makes_a_list_candidate(editor, con, fake_http):
    lid = mk_list(con, "Example Taco Trail")
    cid = mk_candidate(con, google_place_id="g1", status="backlog")
    fake_http.on("searchText", [g_ids_page(["g1"]), g_ids_page(["g2"])])
    r = _import(editor, lid, "Casa Ejemplo", "Pho Example")
    assert (r["linked_candidates"], r["new_candidates"]) == (1, 1)
    assert con.execute("SELECT status FROM candidate WHERE id = ?", (cid,)).fetchone()[0] == "new"
    new = con.execute("SELECT origin, status FROM candidate WHERE google_place_id = 'g2'").fetchone()
    assert tuple(new) == ("list", "new")


def test_chain_hidden_stays_hidden_but_gains_list(editor, con, fake_http):
    lid = mk_list(con, "Example Taco Trail")
    cid = mk_candidate(con, google_place_id="g1", status="chain_hidden")
    fake_http.on("searchText", g_ids_page(["g1"]))
    r = _import(editor, lid, "Chain Burger Example")
    assert r["linked_candidates"] == 1
    assert con.execute("SELECT status FROM candidate WHERE id = ?", (cid,)).fetchone()[0] == "chain_hidden"
    assert con.execute("SELECT candidate_id FROM list_entry").fetchone()[0] == cid


def test_accepted_candidate_links_to_its_place(editor, con, fake_http):
    lid = mk_list(con, "Example Taco Trail")
    pid = mk_place(con, "Casa Ejemplo")
    mk_candidate(con, google_place_id="g1", status="accepted", place_id=pid)
    fake_http.on("searchText", g_ids_page(["g1"]))
    r = _import(editor, lid, "Some other spelling")
    assert r["matched_places"] == 1
    assert con.execute("SELECT place_id FROM list_entry").fetchone()[0] == pid


def test_existing_place_by_name_links_without_google(editor, con, fake_http):
    lid = mk_list(con, "Example Taco Trail")
    pid = mk_place(con, "Casa Ejemplo")
    r = _import(editor, lid, "3) casa ejemplo — best tacos")
    assert r["matched_places"] == 1 and r["unmatched"] == 0
    assert fake_http.calls == []
    assert con.execute("SELECT place_id, status FROM list_entry").fetchone()[:] == (pid, "matched")
    assert [p["name"] for p in editor.get(f"/api/lists/{lid}").json["places"]] == ["Casa Ejemplo"]


def test_existing_candidate_by_name_links_without_google(editor, con, fake_http):
    lid = mk_list(con, "Example Taco Trail")
    cid = mk_candidate(con, google_place_id="g1", status="dismissed", g_name="Pho Example")
    r = _import(editor, lid, "Pho Example")
    assert r["linked_candidates"] == 1 and fake_http.calls == []
    assert con.execute("SELECT status FROM candidate WHERE id = ?", (cid,)).fetchone()[0] == "new"


def test_place_found_by_google_id_links_to_the_place(editor, con, fake_http):
    lid = mk_list(con, "Example Taco Trail")
    pid = mk_place(con, "Casa Ejemplo Norte", google_place_id="g9")
    fake_http.on("searchText", g_ids_page(["g9"]))
    assert _import(editor, lid, "Casa E.")["matched_places"] == 1
    assert con.execute("SELECT place_id FROM list_entry").fetchone()[0] == pid


def test_no_key_keeps_lines_unmatched(editor, con, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_PLACES_KEY", "")
    lid = mk_list(con, "Example Taco Trail")
    r = _import(editor, lid, "Casa Ejemplo", "Pho Example")
    assert r["unmatched"] == 2 and r["unmatched_lines"] == ["Casa Ejemplo", "Pho Example"]
    assert con.execute("SELECT COUNT(*) FROM candidate").fetchone()[0] == 0
    assert [u["line"] for u in editor.get(f"/api/lists/{lid}").json["unmatched"]] == ["Casa Ejemplo", "Pho Example"]


def test_no_results_is_unmatched(editor, con, fake_http):
    lid = mk_list(con, "Example Taco Trail")
    fake_http.on("searchText", g_ids_page([]))
    assert _import(editor, lid, "Nowhere Example")["unmatched"] == 1


def test_a_google_failure_keeps_the_lines_and_stops_asking(editor, con, fake_http):
    lid = mk_list(con, "Example Taco Trail")
    fake_http.on("searchText", common.UpstreamError("down", status=503))
    r = _import(editor, lid, "Casa Ejemplo", "Pho Example")
    assert r["unmatched"] == 2 and len(_search_calls(fake_http)) == 1


def test_search_is_restricted_to_the_towns(editor, con, fake_http):
    lid = mk_list(con, "Example Taco Trail")
    fake_http.on("searchText", g_ids_page([]))
    _import(editor, lid, "Casa Ejemplo")
    rect = _search_calls(fake_http)[0].data["locationRestriction"]["rectangle"]
    assert rect["low"]["latitude"] < 37.8858 and rect["high"]["latitude"] > 38.0194
    assert rect["low"]["longitude"] < -122.1341 and rect["high"]["longitude"] > -121.8847


def test_retry_matches_and_drop_removes(editor, con, fake_http):
    lid = mk_list(con, "Example Taco Trail")
    fake_http.on("searchText", g_ids_page([]))
    _import(editor, lid, "Casa Ejemlo", "Pho Exmple")
    eid, other = [r[0] for r in con.execute("SELECT id FROM list_entry ORDER BY id")]
    fake_http.on("searchText", g_ids_page(["g5"]))
    r = editor.post(f"/api/lists/entries/{eid}/retry", json={"text": "Casa Ejemplo"})
    assert r.status_code == 200 and r.json["new_candidates"] == 1 and r.json["unmatched"] == 0
    row = con.execute("SELECT line, status, candidate_id FROM list_entry WHERE id = ?", (eid,)).fetchone()
    assert row["line"] == "Casa Ejemplo" and row["status"] == "matched" and row["candidate_id"]
    assert editor.delete(f"/api/lists/entries/{other}", json={}).status_code == 204
    assert con.execute("SELECT COUNT(*) FROM list_entry").fetchone()[0] == 1
    assert editor.post(f"/api/lists/entries/{eid}/retry", json={"text": "x"}).status_code == 409   # already matched
    assert editor.post("/api/lists/entries/999/retry", json={"text": "x"}).status_code == 404


def test_retry_that_still_misses_updates_the_text(editor, con, fake_http):
    lid = mk_list(con, "Example Taco Trail")
    fake_http.on("searchText", g_ids_page([]))
    _import(editor, lid, "Casa Ejemlo")
    eid = con.execute("SELECT id FROM list_entry").fetchone()[0]
    r = editor.post(f"/api/lists/entries/{eid}/retry", json={"text": "  Casa Ejmplo "})
    assert r.json["unmatched"] == 1
    assert con.execute("SELECT line FROM list_entry").fetchone()[0] == "Casa Ejmplo"


def test_reimporting_does_not_duplicate_entries(editor, con, fake_http):
    lid = mk_list(con, "Example Taco Trail")
    fake_http.on("searchText", g_ids_page(["g1"]))
    _import(editor, lid, "Casa Ejemplo")
    _import(editor, lid, "Casa Ejemplo")
    assert con.execute("SELECT COUNT(*) FROM list_entry").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM candidate").fetchone()[0] == 1


def test_over_200_lines_is_400(editor, con, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_PLACES_KEY", "")
    lid = mk_list(con, "Example Taco Trail")
    r = editor.post(f"/api/lists/{lid}/import", json={"lines": [f"Place {i}" for i in range(201)]})
    assert r.status_code == 400
    assert editor.post(f"/api/lists/{lid}/import", json={"lines": [f"Place {i}" for i in range(200)]}).status_code == 200


def test_bad_import_bodies_are_400(editor, con):
    lid = mk_list(con, "Example Taco Trail")
    assert editor.post(f"/api/lists/{lid}/import", json={"lines": "Casa"}).status_code == 400
    assert editor.post(f"/api/lists/{lid}/import", json={"lines": [3]}).status_code == 400
    assert editor.post("/api/lists/999/import", json={"lines": []}).status_code == 404


def test_clean_line():
    assert lists.clean_line("2) Pho Example — get the #7") == "Pho Example"
    assert lists.clean_line("• Casa Ejemplo - al pastor") == "Casa Ejemplo"
    assert lists.clean_line("  *  Taqueria Ejemplo: open late ") == "Taqueria Ejemplo"
    assert lists.clean_line("1.  Pho Example – x – y") == "Pho Example"
    assert lists.clean_line("Pho-Example Noodles") == "Pho-Example Noodles"
    assert lists.clean_line("Joe's: Place") == "Joe's"
    assert lists.clean_line("   ") is None
    assert lists.clean_line("12.") is None
    assert lists.clean_line("- ") is None


def test_create_list_rules(editor, con):
    r = editor.post("/api/lists", json={"name": " Example Taco Trail ", "url": "https://example.com/t"})
    assert r.status_code == 201 and r.json["name"] == "Example Taco Trail"
    dup = editor.post("/api/lists", json={"name": "example TACO trail"})
    assert dup.status_code == 409 and dup.json["error"] == "There's already a list called example TACO trail."
    again = editor.post("/api/lists", json={"name": "example taco trail", "get_or_create": True})
    assert again.status_code == 200 and again.json["id"] == r.json["id"]
    assert editor.post("/api/lists", json={"name": ""}).status_code == 400
    assert editor.post("/api/lists", json={"name": "x" * 101}).status_code == 400
    assert editor.post("/api/lists", json={"name": "B", "url": "javascript:alert(1)"}).status_code == 400


def test_list_page_and_index(editor, con, fake_http):
    lid = mk_list(con, "Example Taco Trail")
    mk_place(con, "Casa Ejemplo")
    fake_http.on("searchText", [g_ids_page(["g1"]), g_ids_page([])])
    _import(editor, lid, "Casa Ejemplo", "Pho Example", "Nowhere Example")
    page = editor.get(f"/api/lists/{lid}").json
    assert [p["name"] for p in page["places"]] == ["Casa Ejemplo"]
    assert [c["name"] for c in page["candidates"]] == ["Pho Example"]       # the line stands in until details arrive
    assert [u["line"] for u in page["unmatched"]] == ["Nowhere Example"]
    assert page["list"]["name"] == "Example Taco Trail"
    row = editor.get("/api/lists").json["lists"][0]
    assert (row["places"], row["candidates"], row["unmatched"]) == (1, 1, 1)


def test_delete_list_keeps_places_and_candidates(editor, con, fake_http):
    lid = mk_list(con, "Example Taco Trail")
    pid = mk_place(con, "Casa Ejemplo")
    cid = mk_candidate(con, google_place_id="g1", g_name="Pho Example")
    _import(editor, lid, "Casa Ejemplo", "Pho Example")
    assert editor.delete(f"/api/lists/{lid}", json={}).status_code == 204
    assert con.execute("SELECT COUNT(*) FROM list_entry").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM place WHERE id = ?", (pid,)).fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM candidate WHERE id = ?", (cid,)).fetchone()[0] == 1
    assert editor.get(f"/api/lists/{lid}").status_code == 404


def test_get_or_create_and_the_key_is_not_a_writer(keyed, con):
    assert keyed("read").post("/api/lists", json={"name": "A"}, headers={"X-Hometown": "1"}).status_code == 403
