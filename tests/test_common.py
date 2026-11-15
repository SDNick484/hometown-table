import io
import json
import urllib.error

import pytest

import common
import settings
from common import (ApiError, UpstreamError, fold, int_arg, km_between, month_key, names_match, text, today,
                    town_for)


def test_names_match():
    assert names_match("Casa Ejemplo", "casa ejemplo restaurant")
    assert names_match("Phở Example", "pho example") and not names_match("Casa Ejemplo", "Casa Otra")


def test_names_match_details():
    assert names_match("The Example Café & Bar", "example bar")            # the, café, & are ignored
    assert names_match("Pho Example", "Pho Example Concord")                # one a subset of the other, either way
    assert names_match("Joe's Example", "Joes Example")                     # apostrophes vanish
    assert not names_match("", "") and not names_match("The Restaurant", "the restaurant")  # nothing left to compare
    assert not names_match("Casa Ejemplo", None)


def test_fold():
    assert fold("  Phở  JALAPEÑO ") == "pho  jalapeno"
    assert fold(None) == "" and fold("Straße") == "strasse"


def test_town_for_prefers_address_city_then_nearest():
    assert town_for("100 Main St, Walnut Creek, CA", 37.97, -122.03) == "Walnut Creek"
    assert town_for(None, 37.978, -122.031) == "Concord" and town_for(None, 37.33, -121.89) is None


def test_town_for_details():
    assert town_for("1 Example Ave, Martinez, CA 94553", None, None) == "Martinez"
    assert town_for("1 Concord Blvd, Pleasant Hill CA 94523, USA", None, None) == "Pleasant Hill"  # a street name isn't a city
    assert town_for("1 Example Ave, San Francisco, CA", 37.978, -122.031) == "Concord"   # not a town: the pin decides
    assert town_for("", None, None) is None and town_for(None, None, None) is None
    assert town_for("Pacheco Blvd, Martinez", None, None) == "Martinez"    # the city part, not the street


def test_km_between():
    assert km_between(37.978, -122.0311, 37.978, -122.0311) == 0
    assert 2.9 < km_between(37.9780, -122.0311, 37.9480, -122.0608) < 4.5   # Concord to Pleasant Hill


def test_today_is_los_angeles(clock):
    clock.set_utc("2026-11-15T07:30"); assert today() == "2026-11-14"


def test_month_key_is_los_angeles(clock):
    clock.set_utc("2026-12-01T07:30")
    assert month_key() == "2026-11"
    clock.advance(3600)
    assert month_key() == "2026-12" and today() == "2026-12-01"


def test_clock_set_is_local_time(clock):
    clock.set("2026-11-14T23:30")
    assert today() == "2026-11-14" and common.now() == clock.t
    clock.advance(31 * 60)
    assert today() == "2026-11-15"


def test_text_over_limit_is_400():
    with pytest.raises(ApiError) as e:
        text({"note": "x" * 201}, "note", 200)
    assert e.value.status == 400 and "200" in e.value.message
    assert text({"note": "  " + "x" * 200 + "  "}, "note", 200) == "x" * 200   # stripped, never truncated


def test_text_shapes():
    assert text({}, "note", 10) is None and text({"note": "   "}, "note", 10) is None
    for bad in ({"note": 5}, {"note": ["a"]}):
        with pytest.raises(ApiError):
            text(bad, "note", 10)
    with pytest.raises(ApiError) as e:
        text({}, "name", 10, required=True)
    assert e.value.status == 400


def test_api_error_carries_extra_fields():
    e = ApiError(409, "Already there.", duplicate_of=7)
    assert (e.status, e.message, e.extra) == (409, "Already there.", {"duplicate_of": 7})


def test_int_arg():
    assert int_arg(None, 1, 50, 10) == 10 and int_arg("", 1, 50, 10) == 10
    assert int_arg("7", 1, 50, 10) == 7 and int_arg(7, 1, 50, 10) == 7
    for bad in ("0", "51", "x", "1.5", True, 2.0, "-1"):
        with pytest.raises(ApiError) as e:
            int_arg(bad, 1, 50, 10)
        assert e.value.status == 400


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_http_json_posts_json_and_parses_the_answer(monkeypatch):
    monkeypatch.undo()   # the autouse guard replaced common.http_json; test the real one with urlopen faked
    seen = {}

    def fake_urlopen(req, timeout):
        seen.update(url=req.full_url, method=req.get_method(), body=req.data, headers=dict(req.header_items()),
                    timeout=timeout)
        return _Resp(b'{"places": []}')

    monkeypatch.setattr(common.urllib.request, "urlopen", fake_urlopen)
    out = common.http_json("https://places.googleapis.com/v1/places:searchText", data={"textQuery": "restaurant"},
                           headers={"X-Goog-FieldMask": "places.id"}, timeout=3)
    assert out == {"places": []}
    assert seen["method"] == "POST" and json.loads(seen["body"]) == {"textQuery": "restaurant"} and seen["timeout"] == 3
    assert seen["headers"]["Content-type"] == "application/json" and seen["headers"]["X-goog-fieldmask"] == "places.id"
    assert seen["headers"]["User-agent"].startswith("HometownTable/1")


def test_http_json_errors_are_upstream_errors(monkeypatch):
    monkeypatch.undo()

    def not_found(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b""))

    monkeypatch.setattr(common.urllib.request, "urlopen", not_found)
    with pytest.raises(UpstreamError) as e:
        common.http_json("https://places.googleapis.com/v1/places/x")
    assert e.value.status == 404 and "places.googleapis.com" in str(e.value)

    monkeypatch.setattr(common.urllib.request, "urlopen", lambda req, timeout: _Resp(b"<html>"))
    with pytest.raises(UpstreamError):
        common.http_json("https://overpass-api.de/api/interpreter", data="[out:json];")

    def down(req, timeout):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(common.urllib.request, "urlopen", down)
    with pytest.raises(UpstreamError) as e:
        common.http_json("https://nominatim.openstreetmap.org/search")
    assert e.value.status is None


def test_http_json_is_blocked_in_tests_without_fake_http():
    with pytest.raises(AssertionError, match="fake_http"):
        common.http_json("https://example.com/x")


def test_fake_http_answers_in_order_and_records_calls(fake_http):
    fake_http.on("example.com", [{"page": 1}, {"page": 2}])
    fake_http.on(lambda call: call.method == "POST", UpstreamError("down", status=503))
    assert common.http_json("https://example.com/a") == {"page": 1}
    assert common.http_json("https://example.com/b") == {"page": 2}
    with pytest.raises(UpstreamError):
        common.http_json("https://example.com/c", data={"x": 1})
    assert [c.url for c in fake_http.calls] == ["https://example.com/a", "https://example.com/b", "https://example.com/c"]


# ---------------------------------------------------------------- settings parsing
def test_default_towns_and_box_radius():
    names = [t.name for t in settings.TOWNS]
    assert names == ["Concord", "Clayton", "Pleasant Hill", "Martinez", "Walnut Creek", "Pacheco", "Pittsburg",
                     "Lafayette"]
    concord = settings.TOWNS[0]
    assert (concord.lat, concord.lng, concord.radius_km) == (37.9780, -122.0311, 4.0)


def test_parse_towns():
    towns = settings.parse_towns("Alpha|37.9|-122.0; Beta|38.0|-121.9|2.5\n", 4.0)
    assert towns == (settings.Town("Alpha", 37.9, -122.0, 4.0), settings.Town("Beta", 38.0, -121.9, 2.5))
    for bad in ("", "Alpha|37.9", "Alpha|x|-122", "Alpha|95|-122", "Alpha|37|-122|0", "Alpha|37|-122;alpha|38|-121"):
        with pytest.raises(ValueError):
            settings.parse_towns(bad, 4.0)


def test_keys_split_and_a_key_in_both_lists_is_write_only(monkeypatch):
    monkeypatch.setenv("HOMETOWN_READ_KEY", " r1 , shared ,")
    monkeypatch.setenv("HOMETOWN_API_KEY", "w1,shared")
    assert settings.read_keys() == (("r1",), ("w1", "shared"))


def test_settings_defaults():
    assert settings.PORT == 8782 and settings.APP_NAME == "Hometown Table" and settings.REST_DAYS == 45
    assert settings.LAN_EDIT is False and settings.GOOGLE_PREFILL is True and settings.GOOGLE_PLACES_KEY == ""
    assert settings.GOOGLE_MAX_PRO_PER_MONTH == 3000 and settings.SCAN_TYPES == ("restaurant",)
    assert (settings.SCAN_TILE_KM, settings.SCAN_MIN_TILE_KM, settings.CLOSURE_CHECK_DAYS) == (1.5, 0.25, 28)
    assert settings.TZ == "America/Los_Angeles"
    assert settings.ADMIN_EMAILS == frozenset({"alex@example.com", "sam@example.com"})


def test_env_example_names_every_setting_and_the_mcp_keys():
    with open(f"{settings.BASE}/.env.example", encoding="utf-8") as f:
        lines = f.read().splitlines()
    keys = [line.split("=")[0] for line in lines if "=" in line and not line.startswith("#")]
    mcp = ("HOMETOWN_MCP_WRITES", "MCP_HTTP_PORT", "MCP_HTTP_PATH", "MCP_PUBLIC_HOSTS", "CF_ACCESS_TEAM_DOMAIN",
           "CF_ACCESS_AUD", "MCP_ALLOWED_EMAILS", "HOMETOWN_URL")
    assert set(settings.ENV_KEYS) | set(settings.SYNC_KEYS) | set(mcp) <= set(keys)
    for i, line in enumerate(lines):
        if "=" in line and not line.startswith("#"):
            assert i > 0 and lines[i - 1].startswith("#"), f"{line}: needs a comment line above it"
