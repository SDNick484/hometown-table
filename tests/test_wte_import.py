import json

import wte_import
from conftest import fixture_json, mk_candidate, mk_place
from commands import import_where_to_eat


def _setup(con):
    # Already present: Taqueria Muestra, added by hand a few metres from where Where to Eat has it.
    mk_place(con, "The Taqueria Muestra", town="Clayton", lat=37.9412, lng=-121.9360)


def _export():
    d = fixture_json("where_to_eat/export.json")
    # The fixture has five places; drop the last (pin-less, area-only) one for the four-place scenario.
    d["places"] = d["places"][:4]
    return d


def test_imports_only_open_places_in_the_area(con):
    _setup(con)
    r = wte_import.run(con, _export())
    assert r == {"created": 1, "skipped_closed": 1, "skipped_outside": 1, "skipped_existing": 1}
    rows = con.execute("SELECT * FROM candidate").fetchall()
    assert len(rows) == 1
    c = rows[0]
    assert (c["origin"], c["status"], c["wte_id"]) == ("where_to_eat", "new", 101)
    assert json.loads(c["wte_json"]) == {
        "name": "Casa Ejemplo", "address": "1 Example Way, Concord, CA 94520", "cuisine": "Mexican",
        "phone": "925-555-0101", "website": "https://example.com/casa", "hours": "Mon-Sun 11:00-21:00",
        "lat": 37.9781, "lng": -122.0312}


def test_second_run_is_a_noop(con):
    _setup(con)
    wte_import.run(con, _export())
    r = wte_import.run(con, _export())
    assert r == {"created": 0, "skipped_closed": 1, "skipped_outside": 1, "skipped_existing": 2}
    assert con.execute("SELECT COUNT(*) FROM candidate").fetchone()[0] == 1


def test_dismissed_candidate_is_not_recreated(con):
    mk_candidate(con, origin="where_to_eat", status="dismissed", wte_id=101)
    r = wte_import.run(con, _export())
    assert r["created"] == 1  # only Taqueria Muestra
    assert con.execute("SELECT COUNT(*) FROM candidate WHERE wte_id = 101").fetchone()[0] == 1


def test_place_with_same_wte_id_is_existing(con):
    mk_place(con, "Something Else Entirely", wte_id=101)
    assert wte_import.run(con, _export())["created"] == 1  # only Taqueria Muestra


def test_near_but_different_name_is_not_existing(con):
    mk_place(con, "Pho Example", lat=37.9781, lng=-122.0312)
    assert wte_import.run(con, _export())["created"] == 2  # Casa Ejemplo and Taqueria Muestra


def test_same_name_far_away_is_not_existing(con):
    mk_place(con, "Casa Ejemplo", lat=37.9880, lng=-122.0312)  # about 1.1 km north
    assert wte_import.run(con, _export())["created"] == 2


def test_area_name_counts_when_there_is_no_pin(con):
    d = fixture_json("where_to_eat/export.json")
    d["places"] = d["places"][4:]
    assert wte_import.run(con, d)["created"] == 1
    d["places"][0]["id"], d["places"][0]["area"] = 106, "Oakland"
    assert wte_import.run(con, d)["skipped_outside"] == 1


def test_dishes_photos_and_notes_are_not_imported(con):
    wte_import.run(con, fixture_json("where_to_eat/export.json"))
    for table in ("place", "visit"):
        assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    stored = " ".join(r["wte_json"] for r in con.execute("SELECT wte_json FROM candidate"))
    for text in ("green salsa", "birthday", "burrito", "casa.jpg", "Sam likes it", "Cash only"):
        assert text not in stored


def test_command_prints_counts(con, tmp_path, capsys):
    p = tmp_path / "export.json"
    p.write_text(json.dumps(fixture_json("where_to_eat/export.json")), encoding="utf-8")
    args = type("A", (), {"path": str(p)})()
    assert import_where_to_eat.NAME == "import-where-to-eat"
    assert import_where_to_eat.run(args) == 0
    out = capsys.readouterr().out
    assert "created: 3" in out and "skipped_closed: 1" in out and "skipped_outside: 1" in out
    assert import_where_to_eat.run(args) == 0
    assert "created: 0" in capsys.readouterr().out


def test_command_rejects_a_file_that_is_not_an_export(tmp_path, capsys):
    p = tmp_path / "bad.json"
    p.write_text("[1, 2]", encoding="utf-8")
    assert import_where_to_eat.run(type("A", (), {"path": str(p)})()) == 1
    assert "export" in capsys.readouterr().err
