"""Task 8 test helpers: build an Overpass element in the real response shape."""


def overpass_el(ref, name, lat, lng, **tags):
    """An Overpass `out center tags` element. ref is "node/123" or "way/456". `name` None leaves the name tag off;
    other tags are keyword arguments (use dict unpacking for keys with a colon: **{"addr:street": "Example Street"}).
    A way or relation carries its pin as `center`, a node as lat/lon."""
    kind, _, ident = ref.partition("/")
    el = {"type": kind, "id": int(ident), "tags": ({"name": name} if name else {}) | {"amenity": "restaurant"} | tags}
    if kind == "node":
        el.update(lat=lat, lon=lng)
    else:
        el["center"] = {"lat": lat, "lon": lng}
    return el
