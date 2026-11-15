"""Exports and the status screen's data (spec §12, §6.4)."""
import csv
import io
import json

import export
from conftest import EDITOR, mk_person, mk_place, mk_tag, mk_visit

BASICS = ("place", "person", "tag", "visit", "visit_person")


def seeded(con):
    alex, riley = mk_person(con, "Alex"), mk_person(con, "Riley", "kid")
    casa = mk_place(con, "Casa Ejemplo", town="Concord")
    pho = mk_place(con, "Pho Example", town="Walnut Creek", ownership="unsure")
    tag = mk_tag(con, "private room", True)
    con.execute("INSERT INTO place_tag (place_id, tag_id) VALUES (?, ?)", (casa, tag))
    mk_visit(con, casa, "2026-11-01", {alex: 5, riley: None}, occasion="Riley's graduation")
    mk_visit(con, pho, "2026-11-08", {alex: 3})
    con.execute("UPDATE visit_person SET note = 'Great flan, ''quoted''' WHERE person_id = ? AND visit_id = 1", (alex,))
    return alex, riley


def test_csv_has_one_row_per_visit_person(editor, con):
    seeded(con)
    r = editor.get("/api/export.csv")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    assert "attachment" in r.headers["Content-Disposition"] and ".csv" in r.headers["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(r.get_data(as_text=True))))
    assert rows[0] == ["place", "town", "ownership", "visited_on", "occasion", "person", "rating", "note"]
    assert len(rows) == 4
    assert rows[1] == ["Casa Ejemplo", "Concord", "counts", "2026-11-01", "Riley's graduation", "Alex", "5", "Great flan, 'quoted'"]
    assert rows[2][5:] == ["Riley", "", ""]                      # no rating, no note: empty cells
    assert rows[3][:4] == ["Pho Example", "Walnut Creek", "unsure", "2026-11-08"]


def test_csv_does_not_let_a_cell_run_as_a_formula(con):
    alex = mk_person(con, "Alex")
    mk_visit(con, mk_place(con, "=SUM(A1)"), "2026-11-01", {alex: 4}, occasion="+1 dinner")
    rows = list(csv.reader(io.StringIO(export.to_csv(con))))
    assert rows[1][0] == "'=SUM(A1)" and rows[1][4] == "'+1 dinner"


def test_csv_with_no_visits_is_just_the_header(con):
    assert export.to_csv(con).strip().splitlines() == ["place,town,ownership,visited_on,occasion,person,rating,note"]


def test_json_holds_everything(editor, con):
    seeded(con)
    r = editor.get("/api/export.json")
    assert r.status_code == 200 and r.mimetype == "application/json"
    assert "attachment" in r.headers["Content-Disposition"]
    data = json.loads(r.get_data(as_text=True))
    assert {"exported_at", "schema_version"} <= set(data)
    for table in BASICS + ("place_event", "place_tag", "source_list", "list_entry", "candidate", "scan_run", "google_usage",
                                "sync_seen"):
        assert table in data["tables"], table
    assert [p["name"] for p in data["tables"]["place"]] == ["Casa Ejemplo", "Pho Example"]
    assert len(data["tables"]["visit_person"]) == 3
    assert data["tables"]["place_tag"] == [{"place_id": 1, "tag_id": data["tables"]["tag"][-1]["id"]}]
    assert len(data["tables"]["tag"]) == 7                       # six seeded + one


def test_json_never_carries_settings_or_keys(editor):
    body = editor.get("/api/export.json").get_data(as_text=True)
    for secret in ("test-read-key", "test-write-key", "test-audience-tag"):
        assert secret not in body
    assert "meta" not in json.loads(body)["tables"]


def test_exports_need_an_editor(client, keyed):
    assert client.get("/api/export.csv").status_code == 401
    assert client.get("/api/export.json").status_code == 401
    assert keyed("read").get("/api/export.csv").status_code == 200


def test_status_never_shows_keys(editor, con, monkeypatch):
    import settings
    monkeypatch.setattr(settings, "GOOGLE_PLACES_KEY", "AIza-secret-key-for-test")
    r = editor.get("/api/settings/status")
    body = r.get_data(as_text=True)
    assert "AIza-secret-key-for-test" not in body and "test-write-key" not in body
    data = r.get_json()
    assert data["google_key_set"] is True and data["google_prefill"] is True and data["rest_days"] == 45
    assert [t["name"] for t in data["towns"]][:2] == ["Concord", "Clayton"]
    assert data["last_scan"] is None and data["google_usage"] == {"month": data["google_usage"]["month"], "pro_calls": 0, "id_calls": 0,
                                                                  "max_pro_calls": 3000}
    monkeypatch.setattr(settings, "GOOGLE_PLACES_KEY", "")
    assert editor.get("/api/settings/status").get_json()["google_key_set"] is False


def test_status_shows_last_scan_and_this_months_usage(editor, con, clock):
    import common
    clock.set("2026-11-14T10:00")
    con.execute("INSERT INTO scan_run (started_at, finished_at, tiles, search_calls, detail_calls, new_candidates, error) "
                "VALUES (1, 2, 4, 5, 6, 7, NULL), (3, 4, 1, 1, 1, 0, 'upstream down')")
    con.execute("INSERT INTO google_usage (month, pro_calls, id_calls) VALUES (?, 12, 3), ('2020-01', 99, 99)", (common.month_key(),))
    data = editor.get("/api/settings/status").get_json()
    assert data["last_scan"]["error"] == "upstream down" and data["last_scan"]["started_at"] == 3
    assert data["google_usage"]["pro_calls"] == 12 and data["google_usage"]["id_calls"] == 3


def test_status_says_who_me_is(editor, con):
    assert editor.get("/api/settings/status").get_json()["me"] == {"email": EDITOR, "person_id": None}
    pid = mk_person(con, "Alex", email=EDITOR)
    assert editor.get("/api/settings/status").get_json()["me"] == {"email": EDITOR, "person_id": pid}
