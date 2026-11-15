"""Google Places API (New): IDs-only search, capped Pro details, and the 30-day tidy (spec 8.1).

Only google_place_id is stored for good; the g_* candidate columns are a cache that `tidy` clears after 30 days. Text
Search runs under an IDs-only field mask (free, counted but never capped). Place Details is the Pro tier: every call
is counted in google_usage, and one atomic check-and-increment before the request keeps the month's total at or
under GOOGLE_MAX_PRO_PER_MONTH. A call that fails or answers 404 stays counted: Google may have billed it. Settings
are read at call time, so a test (or the owner's .env) changes them without a reimport."""
import urllib.parse

import common
import db
import settings

BASE = "https://places.googleapis.com/v1"
IDS_MASK = "places.id,places.movedPlaceId,nextPageToken"
PRO_MASK = "id,displayName,formattedAddress,location,businessStatus,primaryType"
MAX_PAGES = 3      # Text Search returns at most 60 results, 20 a page
TIDY_DAYS = 30
G_COLUMNS = ("g_name", "g_address", "g_lat", "g_lng", "g_status", "g_type", "g_fetched_at")


class CapReached(Exception):
    """This month's Pro calls are at GOOGLE_MAX_PRO_PER_MONTH; the call was not made."""


def enabled() -> bool:
    return bool(settings.GOOGLE_PLACES_KEY)


def _headers(mask):
    return {"X-Goog-Api-Key": settings.GOOGLE_PLACES_KEY, "X-Goog-FieldMask": mask}


def _count(con, column, cap=None):
    """Add one to this month's `column` in a single transaction; with `cap`, only while it is below it. True if
    counted."""
    month = common.month_key()
    with db.transaction(con):
        con.execute("INSERT OR IGNORE INTO google_usage (month) VALUES (?)", (month,))
        sql = f"UPDATE google_usage SET {column} = {column} + 1 WHERE month = ?"
        if cap is not None:
            sql += f" AND {column} < ?"
        cur = con.execute(sql, (month,) if cap is None else (month, cap))
    return cur.rowcount == 1


def search_ids(con, *, text, rect=None, included_type=None):
    """Place ids for a text query, and {old: new} for places Google says have moved. rect is (south, west, north,
    east). Follows nextPageToken. Never capped. Without a key: ([], {}) and no call."""
    if not enabled():
        return [], {}
    body = {"textQuery": text}
    if included_type:
        body["includedType"] = included_type
        body["strictTypeFiltering"] = True
    if rect is not None:
        south, west, north, east = rect
        body["locationRestriction"] = {"rectangle": {"low": {"latitude": south, "longitude": west},
                                                     "high": {"latitude": north, "longitude": east}}}
    ids, moves = [], {}
    for _ in range(MAX_PAGES):
        _count(con, "id_calls")
        page = common.http_json(f"{BASE}/places:searchText", data=body, headers=_headers(IDS_MASK))
        for p in page.get("places") or []:
            if p.get("id"):
                ids.append(p["id"])
                if p.get("movedPlaceId"):
                    moves[p["id"]] = p["movedPlaceId"]
        token = page.get("nextPageToken")
        if not token:
            break
        body = {**body, "pageToken": token}
    return ids, moves


def details(con, place_id):
    """{name, address, lat, lng, status, type} for an id, or None for 404 (or no key, with no call). Raises CapReached
    at the monthly cap, before any request."""
    if not enabled():
        return None
    if not _count(con, "pro_calls", cap=settings.GOOGLE_MAX_PRO_PER_MONTH):
        raise CapReached(f"{settings.GOOGLE_MAX_PRO_PER_MONTH} Google lookups already used this month")
    try:
        p = common.http_json(f"{BASE}/places/{urllib.parse.quote(place_id, safe='')}", headers=_headers(PRO_MASK))
    except common.UpstreamError as e:
        if e.status == 404:
            return None
        raise
    loc = p.get("location") or {}
    return {"name": (p.get("displayName") or {}).get("text"), "address": p.get("formattedAddress"),
            "lat": loc.get("latitude"), "lng": loc.get("longitude"), "status": p.get("businessStatus"),
            "type": p.get("primaryType")}


def usage(con):
    row = con.execute("SELECT pro_calls, id_calls FROM google_usage WHERE month = ?", (common.month_key(),)).fetchone()
    return {"month": common.month_key(), "pro_calls": row["pro_calls"] if row else 0,
            "id_calls": row["id_calls"] if row else 0, "cap": settings.GOOGLE_MAX_PRO_PER_MONTH}


def tidy(con, now=None) -> int:
    """Clear the g_* columns of candidates fetched 30 days ago or more (Google's cache limit). OSM values stay.
    Returns how many rows were cleared."""
    cutoff = (common.now() if now is None else now) - TIDY_DAYS * 86400
    sets = ", ".join(f"{c} = NULL" for c in G_COLUMNS)
    with db.transaction(con):
        cur = con.execute(f"UPDATE candidate SET {sets} WHERE g_fetched_at IS NOT NULL AND g_fetched_at <= ?", (cutoff,))
    return cur.rowcount
