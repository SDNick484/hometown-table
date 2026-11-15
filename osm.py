"""OpenStreetMap client (spec §8.1, §8.2 step 3, §8.4): Overpass food places, name matching, Nominatim search and
geocoding with the Photon fallback. No key. Credit "© OpenStreetMap contributors" wherever OSM data is shown.

Everything goes through `common.http_json` (called through the module so tests can replace it). A failed call raises
`common.UpstreamError`; nothing here swallows it, except that `geocode` tries Photon when Nominatim fails.

A bounding box is `(south, west, north, east)`, Overpass's own order. Every place is a dict
{ref, name, address, lat, lng, cuisine, phone, website, opening_hours, is_chain}; `ref` is "node/123"."""
import urllib.parse

import common
import settings

USER_AGENT = "HometownTable/1 (+https://github.com/SDNick484/hometown-table)"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
PHOTON_URL = "https://photon.komoot.io/api/"
MATCH_KM = 0.1  # a candidate and an OSM element are the same place within 100 m
AMENITIES = "restaurant|cafe|fast_food|food_court|ice_cream"
_HEADERS = {"User-Agent": USER_AGENT}


def _address(tags: dict) -> str | None:
    """"100 Example Street, Concord, CA 94520" from the addr:* tags; whatever parts exist, None when none do."""
    street = " ".join(p for p in (tags.get("addr:housenumber"), tags.get("addr:street")) if p)
    state_zip = " ".join(p for p in (tags.get("addr:state"), tags.get("addr:postcode")) if p)
    parts = [p for p in (street, tags.get("addr:city"), state_zip) if p]
    return ", ".join(parts) or None


def _place(ref: str, name, lat, lng, tags: dict, address=None) -> dict:
    tags = tags or {}
    return {
        "ref": ref, "name": name, "address": address if address is not None else _address(tags),
        "lat": float(lat), "lng": float(lng), "cuisine": tags.get("cuisine"),
        "phone": tags.get("phone") or tags.get("contact:phone"),
        "website": tags.get("website") or tags.get("contact:website"),
        "opening_hours": tags.get("opening_hours"),
        "is_chain": bool(tags.get("brand") or tags.get("brand:wikidata")),
    }


def _element(el: dict) -> dict | None:
    tags = el.get("tags") or {}
    pin = el if "lat" in el else el.get("center") or {}
    if not tags.get("name") or "lat" not in pin or "lon" not in pin:
        return None
    return _place(f"{el['type']}/{el['id']}", tags["name"], pin["lat"], pin["lon"], tags)


def food_places(bbox) -> list[dict]:
    """Every food place OSM has in the box, in one Overpass query: amenity restaurant, cafe, fast_food, food_court or
    ice_cream, and shop=bakery. Elements with no name or no pin are left out (they can't be matched)."""
    s, w, n, e = bbox
    box = f"({s},{w},{n},{e})"
    query = (f'[out:json][timeout:60];(nwr["amenity"~"^({AMENITIES})$"]{box};nwr["shop"="bakery"]{box};);'
             "out center tags;")
    answer = common.http_json(OVERPASS_URL, data="data=" + urllib.parse.quote(query),
                              headers={**_HEADERS, "Content-Type": "application/x-www-form-urlencoded"}, timeout=90)
    if not isinstance(answer, dict) or not isinstance(answer.get("elements"), list):
        raise common.UpstreamError("overpass-api.de did not answer with elements")
    return [p for p in map(_element, answer["elements"]) if p]


def match(name, lat, lng, elements) -> dict | None:
    """The nearest of `elements` within 100 m whose name `common.names_match`es. They are `food_places`' dicts; a raw
    Overpass element (it has "tags") is converted first."""
    best, best_km = None, MATCH_KM
    for el in elements:
        if "tags" in el:
            el = _element(el)
        if not el or not common.names_match(name, el.get("name")):
            continue
        km = common.km_between(lat, lng, el["lat"], el["lng"])
        if km <= best_km:
            best, best_km = el, km
    return best


def _nominatim(query: str, **params) -> list:
    url = NOMINATIM_URL + "?" + urllib.parse.urlencode({"q": query, "format": "jsonv2", "limit": 10, **params})
    answer = common.http_json(url, headers=_HEADERS)
    if not isinstance(answer, list):
        raise common.UpstreamError("nominatim.openstreetmap.org did not answer with a list")
    return answer


def _nominatim_address(a: dict) -> str | None:
    street = " ".join(p for p in (a.get("house_number"), a.get("road")) if p)
    city = a.get("city") or a.get("town") or a.get("village")
    state = {"California": "CA"}.get(a.get("state"), a.get("state"))
    parts = [p for p in (street, city, " ".join(p for p in (state, a.get("postcode")) if p)) if p]
    return ", ".join(parts) or None


def search(name, bbox) -> list[dict]:
    """Nominatim search for `name`, limited to the box (add-by-hand's "look it up"), in `food_places`' shape."""
    s, w, n, e = bbox
    out = []
    for r in _nominatim(name, bounded=1, viewbox=f"{w},{n},{e},{s}", addressdetails=1, extratags=1):
        if not r.get("osm_type") or "osm_id" not in r:
            continue
        label = r.get("name") or (r.get("display_name") or "").split(",")[0].strip()
        out.append(_place(f"{r['osm_type']}/{r['osm_id']}", label, r["lat"], r["lon"], r.get("extratags"),
                          address=_nominatim_address(r.get("address") or {})))
    return out


def _photon(query: str, town: str) -> tuple[float, float] | None:
    params = {"q": query, "limit": 1}
    centre = next((t for t in settings.TOWNS if common.fold(t.name) == common.fold(town)), None)
    if centre:
        params.update(lat=centre.lat, lon=centre.lng)  # bias toward the town
    answer = common.http_json(PHOTON_URL + "?" + urllib.parse.urlencode(params), headers=_HEADERS)
    features = answer.get("features") if isinstance(answer, dict) else None
    if not isinstance(features, list):
        raise common.UpstreamError("photon.komoot.io did not answer with features")
    for f in features:
        lng, lat = (f.get("geometry") or {}).get("coordinates", [None, None])[:2]
        if lat is not None and lng is not None:
            return float(lat), float(lng)
    return None


def geocode(address, town) -> tuple[float, float] | None:
    """(lat, lng) of an address in a town: Nominatim, then Photon (where-to-eat's order). None when neither knows it.
    Raises UpstreamError only when a service failed and no service found the address."""
    if not (address or "").strip():
        return None
    query = f"{address.strip()}, {town}, CA" if town else address.strip()
    failure = None
    try:
        for r in _nominatim(query, limit=1, countrycodes="us"):
            return float(r["lat"]), float(r["lon"])
    except common.UpstreamError as e:
        failure = e
    try:
        found = _photon(query, town)
    except common.UpstreamError as e:
        raise failure or e from None
    if found is None and failure:
        raise failure
    return found
