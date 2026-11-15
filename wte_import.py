"""One-time import of Where to Eat's export.json into the inbox (spec §9).

Each open place in the area becomes a `where_to_eat` candidate (status `new`) holding its name, address, cuisine, phone,
website, hours, lat and lng in `wte_json` (the owner's own data, not Google's) and its Where to Eat id in `wte_id`.
Dishes, photos and notes are not imported. Idempotent: a place is skipped when its `wte_id` is already a place or
candidate, or when a place or candidate within 150 m has a matching folded name."""
import json

import common
import db
import settings

NEAR_KM = 0.15
FIELDS = ("name", "address", "cuisine", "phone", "website", "hours", "lat", "lng")


def _coord(v):
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _in_area(p: dict, lat, lng) -> bool:
    if lat is not None and lng is not None:
        if any(common.km_between(lat, lng, t.lat, t.lng) <= t.radius_km for t in settings.TOWNS):
            return True
    area = common.fold(p.get("area"))
    return bool(area) and any(area == common.fold(t.name) for t in settings.TOWNS)


def _known(con) -> tuple[set[int], list[tuple[str, float, float]]]:
    """The Where to Eat ids already stored, and (name, lat, lng) of every place and candidate that has a pin."""
    ids, pins = set(), []
    for r in con.execute("SELECT wte_id FROM place WHERE wte_id IS NOT NULL UNION "
                         "SELECT wte_id FROM candidate WHERE wte_id IS NOT NULL"):
        ids.add(r[0])
    for r in con.execute("SELECT name, lat, lng FROM place WHERE lat IS NOT NULL AND lng IS NOT NULL"):
        pins.append((r["name"], r["lat"], r["lng"]))
    for r in con.execute("SELECT g_name, g_lat, g_lng, osm_name, osm_lat, osm_lng, wte_json FROM candidate"):
        if r["g_lat"] is not None and r["g_name"]:
            pins.append((r["g_name"], r["g_lat"], r["g_lng"]))
        if r["osm_lat"] is not None and r["osm_name"]:
            pins.append((r["osm_name"], r["osm_lat"], r["osm_lng"]))
        if r["wte_json"]:
            w = json.loads(r["wte_json"])
            if _coord(w.get("lat")) is not None and _coord(w.get("lng")) is not None and w.get("name"):
                pins.append((w["name"], float(w["lat"]), float(w["lng"])))
    return ids, pins


def run(con, export: dict) -> dict:
    """Create candidates from the export's places. Returns the counts."""
    counts = {"created": 0, "skipped_closed": 0, "skipped_outside": 0, "skipped_existing": 0}
    places = export.get("places") if isinstance(export, dict) else None
    if not isinstance(places, list):
        raise ValueError("not a Where to Eat export: it has no \"places\" list")
    with db.transaction(con):
        ids, pins = _known(con)
        for p in places:
            if not isinstance(p, dict) or not str(p.get("name") or "").strip():
                continue
            if p.get("closed"):
                counts["skipped_closed"] += 1
                continue
            lat, lng = _coord(p.get("lat")), _coord(p.get("lng"))
            if not _in_area(p, lat, lng):
                counts["skipped_outside"] += 1
                continue
            wte_id = p.get("id") if isinstance(p.get("id"), int) else None
            near = lat is not None and lng is not None and any(
                common.names_match(p["name"], n) and common.km_between(lat, lng, la, ln) <= NEAR_KM
                for n, la, ln in pins)
            if (wte_id is not None and wte_id in ids) or near:
                counts["skipped_existing"] += 1
                continue
            stored = {k: p.get(k) for k in FIELDS}
            stored["lat"], stored["lng"] = lat, lng
            con.execute("INSERT INTO candidate (origin, status, wte_id, wte_json, first_seen_at) "
                        "VALUES ('where_to_eat', 'new', ?, ?, ?)",
                        (wte_id, json.dumps(stored, ensure_ascii=False), common.now()))
            if wte_id is not None:
                ids.add(wte_id)
            if lat is not None and lng is not None:
                pins.append((p["name"], lat, lng))
            counts["created"] += 1
    return counts
