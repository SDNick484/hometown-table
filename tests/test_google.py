import pytest
from conftest import fixture_json, mk_candidate
from plugins.google import g_details, g_ids_page

import common
import google
import settings

SEARCH = "https://places.googleapis.com/v1/places:searchText"
IDS_MASK = "places.id,places.movedPlaceId,nextPageToken"
PRO_MASK = "id,displayName,formattedAddress,location,businessStatus,primaryType"


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_PLACES_KEY", "test-key-not-real")


def test_cap_stops_pro_calls_and_counts_every_one(con, fake_http, monkeypatch):   # Review Focus 2
    monkeypatch.setattr(settings, "GOOGLE_MAX_PRO_PER_MONTH", 2)
    fake_http.on("places/", g_details("p1", "Casa Ejemplo", 37.97, -122.03))
    google.details(con, "p1")
    google.details(con, "p2")
    with pytest.raises(google.CapReached):
        google.details(con, "p3")
    assert len(fake_http.calls) == 2 and google.usage(con)["pro_calls"] == 2


def test_cap_counts_a_call_that_fails_and_a_404(con, fake_http, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_MAX_PRO_PER_MONTH", 2)
    fake_http.on("places/", [common.UpstreamError("gone", status=404), common.UpstreamError("down", status=503)])
    assert google.details(con, "p1") is None
    with pytest.raises(common.UpstreamError):
        google.details(con, "p2")
    assert google.usage(con)["pro_calls"] == 2
    with pytest.raises(google.CapReached):
        google.details(con, "p3")


def test_cap_of_zero_allows_nothing(con, fake_http, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_MAX_PRO_PER_MONTH", 0)
    with pytest.raises(google.CapReached):
        google.details(con, "p1")
    assert fake_http.calls == []


def test_cap_resets_next_month(con, fake_http, clock, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_MAX_PRO_PER_MONTH", 1)
    fake_http.on("places/", g_details("p1", "Casa Ejemplo", 37.97, -122.03))
    clock.set("2026-11-30T23:00")
    google.details(con, "p1")
    with pytest.raises(google.CapReached):
        google.details(con, "p2")
    assert google.usage(con) == {"month": "2026-11", "pro_calls": 1, "id_calls": 0, "cap": 1}
    clock.set("2026-12-01T00:30")
    assert google.details(con, "p2")["name"] == "Casa Ejemplo"
    assert google.usage(con)["month"] == "2026-12" and google.usage(con)["pro_calls"] == 1


def test_details_sends_the_pro_mask_and_returns_the_fields(con, fake_http):
    fake_http.on("places/", "google/details_casa_ejemplo.json")
    out = google.details(con, "ChIJexample0000000001")
    assert out == {"name": "Casa Ejemplo", "address": "100 Example Way, Concord, CA 94520, USA", "lat": 37.97,
                   "lng": -122.03, "status": "OPERATIONAL", "type": "mexican_restaurant"}
    call = fake_http.calls[0]
    assert call.method == "GET" and call.url == "https://places.googleapis.com/v1/places/ChIJexample0000000001"
    assert call.headers["X-Goog-FieldMask"] == PRO_MASK
    assert call.headers["X-Goog-Api-Key"] == "test-key-not-real"
    assert "test-key-not-real" not in call.url


def test_details_404_is_none_and_other_errors_raise(con, fake_http):
    fake_http.on("places/", [common.UpstreamError("gone", status=404), common.UpstreamError("busy", status=429)])
    assert google.details(con, "p1") is None
    with pytest.raises(common.UpstreamError):
        google.details(con, "p2")


def test_search_ids_uses_ids_only_mask_and_follows_pages(con, fake_http):
    fake_http.on(SEARCH, ["google/search_page1.json", "google/search_page2.json"])
    ids, moves = google.search_ids(con, text="restaurant", rect=(37.9, -122.1, 38.0, -122.0), included_type="restaurant")
    assert ids == ["ChIJexample0000000001", "ChIJexample0000000002", "ChIJexample0000000003"]
    assert moves == {"ChIJexample0000000002": "ChIJexample0000000009"}
    first, second = fake_http.calls
    for call in (first, second):
        assert call.method == "POST" and call.url == SEARCH
        assert call.headers["X-Goog-FieldMask"] == IDS_MASK
        assert call.headers["X-Goog-Api-Key"] == "test-key-not-real"
    assert first.data == {"textQuery": "restaurant", "includedType": "restaurant", "strictTypeFiltering": True,
                          "locationRestriction": {"rectangle": {"low": {"latitude": 37.9, "longitude": -122.1},
                                                                "high": {"latitude": 38.0, "longitude": -122.0}}}}
    assert second.data == {**first.data, "pageToken": "example-page-2-token"}
    assert google.usage(con)["id_calls"] == 2 and google.usage(con)["pro_calls"] == 0


def test_search_ids_without_type_or_rect_sends_only_the_text(con, fake_http):
    fake_http.on(SEARCH, g_ids_page(["a"]))
    assert google.search_ids(con, text="tacos Concord") == (["a"], {})
    assert fake_http.calls[0].data == {"textQuery": "tacos Concord"}


def test_search_ids_is_never_capped(con, fake_http, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_MAX_PRO_PER_MONTH", 0)
    fake_http.on(SEARCH, g_ids_page(["a"]))
    assert google.search_ids(con, text="x")[0] == ["a"]


def test_search_ids_stops_after_the_three_pages_google_returns(con, fake_http):
    fake_http.on(SEARCH, lambda call: g_ids_page([f"id{len(fake_http.calls)}"], next_token="more"))
    ids, _ = google.search_ids(con, text="x")
    assert ids == ["id1", "id2", "id3"] and len(fake_http.calls) == 3


def test_moved_ids_are_reported(con, fake_http):
    fake_http.on(SEARCH, g_ids_page(["a", ("old", "new")]))
    assert google.search_ids(con, text="x") == (["a", "old"], {"old": "new"})


def test_tidy_clears_google_columns_after_30_days_and_keeps_osm(con, clock):
    t = clock.t
    cols = dict(g_name="Casa Ejemplo", g_address="1 Example Way", g_lat=37.9, g_lng=-122.0, g_status="OPERATIONAL",
                g_type="restaurant", osm_ref="node/1", osm_name="Casa Ejemplo", osm_lat=37.9, osm_lng=-122.0)
    old = mk_candidate(con, google_place_id="old", g_fetched_at=t - 30 * 86400, **cols)
    fresh = mk_candidate(con, google_place_id="fresh", g_fetched_at=t - 30 * 86400 + 1, **cols)
    never = mk_candidate(con, google_place_id="never", osm_ref="node/2")
    assert google.tidy(con) == 1
    row = con.execute("SELECT * FROM candidate WHERE id = ?", (old,)).fetchone()
    assert all(row[c] is None for c in ("g_name", "g_address", "g_lat", "g_lng", "g_status", "g_type", "g_fetched_at"))
    assert (row["osm_ref"], row["osm_name"], row["osm_lat"], row["google_place_id"]) == ("node/1", "Casa Ejemplo", 37.9, "old")
    assert con.execute("SELECT g_name FROM candidate WHERE id = ?", (fresh,)).fetchone()[0] == "Casa Ejemplo"
    assert con.execute("SELECT osm_ref FROM candidate WHERE id = ?", (never,)).fetchone()[0] == "node/2"
    assert google.tidy(con, now=t + 60 * 86400) == 1   # the fresh one ages out too
    assert google.tidy(con) == 0


def test_tidy_command_runs_it(con, clock, capsys, monkeypatch):
    import db
    from commands import tidy
    monkeypatch.setattr(settings, "DB_PATH", con.execute("PRAGMA database_list").fetchone()["file"])
    mk_candidate(con, google_place_id="x", g_name="Casa Ejemplo", g_fetched_at=clock.t - 31 * 86400)
    assert tidy.NAME == "tidy" and tidy.run(None) == 0
    assert "1" in capsys.readouterr().out
    assert con.execute("SELECT g_name FROM candidate").fetchone()[0] is None
    assert db  # the command opened the same file through db.connect


def test_no_key_means_disabled_and_no_calls(con, fake_http, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_PLACES_KEY", "")
    assert google.enabled() is False
    assert google.search_ids(con, text="x") == ([], {})
    assert google.details(con, "p1") is None
    assert fake_http.calls == [] and google.usage(con)["pro_calls"] == 0


def test_enabled_with_a_key():
    assert google.enabled() is True


def test_fixtures_are_the_real_shapes():
    assert fixture_json("google/details_casa_ejemplo.json")["displayName"]["text"] == "Casa Ejemplo"
