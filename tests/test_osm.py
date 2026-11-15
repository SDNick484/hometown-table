"""Task 8: the OpenStreetMap client. Every call goes through common.http_json, replaced by fake_http."""
import urllib.parse

import pytest

import common
import osm
from conftest import fixture_json
from plugins.osm import overpass_el

BOX = (37.9, -122.1, 38.0, -122.0)  # south, west, north, east
STREET = {"addr:street": "Example Street"}


def test_brand_tag_marks_chain(fake_http):
    fake_http.on("overpass", {"elements": [overpass_el("node/1", "Chain Burger Example", 37.97, -122.03, brand="Chain Burger")]})
    assert osm.food_places(BOX)[0]["is_chain"] is True


def test_brand_wikidata_alone_marks_chain_and_plain_is_not(fake_http):
    fake_http.on("overpass", {"elements": [
        overpass_el("node/1", "A", 37.97, -122.03, **{"brand:wikidata": "Q1"}),
        overpass_el("node/2", "B", 37.97, -122.03)]})
    assert [e["is_chain"] for e in osm.food_places(BOX)] == [True, False]


def test_food_places_shape_and_query(fake_http):
    fake_http.on("overpass", "osm/overpass_food.json")
    places = osm.food_places(BOX)
    assert [p["ref"] for p in places] == ["node/1001", "way/2002", "node/3003"]  # the nameless element is dropped
    casa, chain, bakery = places
    assert casa == {"ref": "node/1001", "name": "Casa Ejemplo", "address": "100 Example Street, Concord, CA 94520",
                    "lat": 37.9781, "lng": -122.0312, "cuisine": "mexican;tex-mex", "phone": "+1 555 0100",
                    "website": "https://casa.example.com", "opening_hours": "Mo-Su 11:00-21:00", "is_chain": False}
    assert (chain["lat"], chain["lng"]) == (37.9482, -122.0610)  # a way's pin is its centre
    assert chain["address"] == "Sample Avenue"
    assert (chain["phone"], chain["website"], chain["is_chain"]) == ("+1 555 0101", "https://chain.example.com", True)
    assert bakery["address"] is None and bakery["cuisine"] is None and bakery["phone"] is None
    (call,) = fake_http.calls
    assert call.url == "https://overpass-api.de/api/interpreter"
    assert call.headers["User-Agent"] == "HometownTable/1 (+https://github.com/SDNick484/hometown-table)"
    body = urllib.parse.parse_qs(call.data)["data"][0]
    assert "restaurant|cafe|fast_food|food_court|ice_cream" in body and "bakery" in body
    assert "(37.9,-122.1,38.0,-122.0)" in body and "out center tags" in body


def test_match_needs_name_and_100m():
    def at(m_north):  # an element this many metres north of the query point
        return overpass_el("node/1", "Casa Ejemplo", 37.9780 + m_north / 111_195, -122.0311)
    els = [at(90)]
    assert osm.match("Casa Ejemplo", 37.9780, -122.0311, els)["ref"] == "node/1"
    assert osm.match("Casa Ejemplo", 37.9780, -122.0311, [at(120)]) is None
    other = overpass_el("node/2", "Pho Example", 37.9780 + 10 / 111_195, -122.0311)
    assert osm.match("Casa Ejemplo", 37.9780, -122.0311, [other]) is None


def test_match_takes_the_nearest_of_several_and_folds_names():
    near = overpass_el("node/1", "The Casa Ejemplo Restaurant", 37.9780 + 20 / 111_195, -122.0311)
    far = overpass_el("node/2", "Casa Ejemplo", 37.9780 + 80 / 111_195, -122.0311)
    assert osm.match("casa ejemplo", 37.9780, -122.0311, [far, near])["ref"] == "node/1"
    assert osm.match("Casa Ejemplo", 37.9780, -122.0311, []) is None


def test_match_accepts_converted_elements(fake_http):
    fake_http.on("overpass", "osm/overpass_food.json")
    found = osm.match("Casa Ejemplo", 37.9781, -122.0312, osm.food_places(BOX))
    assert found["ref"] == "node/1001" and found["address"].startswith("100 Example")


def test_search_nominatim_bounded_to_the_box(fake_http):
    fake_http.on("nominatim", "osm/nominatim_search.json")
    results = osm.search("Casa Ejemplo", BOX)
    assert results[0] == {"ref": "node/1001", "name": "Casa Ejemplo", "address": "100 Example Street, Concord, CA 94520",
                          "lat": 37.9781, "lng": -122.0312, "cuisine": "mexican", "phone": "+1 555 0100",
                          "website": "https://casa.example.com", "opening_hours": "Mo-Su 11:00-21:00", "is_chain": False}
    assert results[1]["ref"] == "way/2002" and results[1]["is_chain"] is True
    (call,) = fake_http.calls
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(call.url).query)
    assert call.url.startswith("https://nominatim.openstreetmap.org/search?")
    assert q["q"] == ["Casa Ejemplo"] and q["bounded"] == ["1"] and q["format"] == ["jsonv2"]
    assert q["viewbox"] == ["-122.1,38.0,-122.0,37.9"]  # left, top, right, bottom
    assert call.headers["User-Agent"].startswith("HometownTable/1")


def test_search_empty_and_upstream_error(fake_http):
    fake_http.on("nominatim", "osm/nominatim_empty.json")
    assert osm.search("Nothing Here", BOX) == []
    fake_http.on("nominatim", common.UpstreamError("nominatim.openstreetmap.org answered 429", status=429))
    with pytest.raises(common.UpstreamError):
        osm.search("x", BOX)


def test_geocode_uses_nominatim_first(fake_http):
    fake_http.on("nominatim", "osm/nominatim_address.json")
    assert osm.geocode("200 Example Street", "Concord") == (37.979, -122.03)
    assert len(fake_http.calls) == 1
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(fake_http.calls[0].url).query)
    assert q["q"] == ["200 Example Street, Concord, CA"]


def test_geocode_falls_back_to_photon(fake_http):
    fake_http.on("nominatim", "osm/nominatim_empty.json")
    fake_http.on("photon", "osm/photon_search.json")
    assert osm.geocode("200 Example Street", "Concord") == (37.9791, -122.0301)  # Photon is [lng, lat]
    assert "nominatim" in fake_http.calls[0].url and "photon.komoot.io" in fake_http.calls[1].url


def test_geocode_falls_back_when_nominatim_is_down(fake_http):
    fake_http.on("nominatim", common.UpstreamError("down"))
    fake_http.on("photon", "osm/photon_search.json")
    assert osm.geocode("200 Example Street", "Concord") == (37.9791, -122.0301)


def test_geocode_none_when_neither_knows(fake_http):
    fake_http.on("nominatim", "osm/nominatim_empty.json")
    fake_http.on("photon", "osm/photon_empty.json")
    assert osm.geocode("1 Nowhere Lane", "Concord") is None
    assert osm.geocode("", "Concord") is None


def test_geocode_raises_when_every_service_is_down(fake_http):
    fake_http.on("nominatim", common.UpstreamError("down"))
    fake_http.on("photon", common.UpstreamError("also down"))
    with pytest.raises(common.UpstreamError):
        osm.geocode("200 Example Street", "Concord")


def test_upstream_error_is_raised_not_swallowed(fake_http):
    fake_http.on("overpass", common.UpstreamError("overpass-api.de answered 504", status=504))
    with pytest.raises(common.UpstreamError):
        osm.food_places(BOX)


def test_overpass_answer_without_elements_is_an_error(fake_http):
    fake_http.on("overpass", {"remark": "runtime error: Query timed out"})
    with pytest.raises(common.UpstreamError):
        osm.food_places(BOX)


def test_fixture_helper_matches_real_shape():
    el = overpass_el("way/9", "Pho Example", 37.9, -122.0, **STREET)
    assert el["center"] == {"lat": 37.9, "lon": -122.0} and el["tags"]["name"] == "Pho Example"
    assert fixture_json("osm/overpass_food.json")["elements"][0]["type"] == "node"
