"""Task 7 helpers: Google Places (New) responses in the real shapes, hand-written (not recorded)."""


def g_ids_page(ids, next_token=None):
    """A Text Search page under the IDs-only mask. An item is an id, or (id, moved_place_id) for a place that moved."""
    places = []
    for item in ids:
        if isinstance(item, tuple):
            places.append({"id": item[0], "movedPlaceId": item[1]})
        else:
            places.append({"id": item})
    page = {"places": places}
    if next_token:
        page["nextPageToken"] = next_token
    return page


def g_details(id, name, lat, lng, status="OPERATIONAL"):
    """A Place Details answer under the Pro mask."""
    return {"id": id, "displayName": {"text": name, "languageCode": "en"},
            "formattedAddress": "100 Example Way, Concord, CA 94520, USA",
            "location": {"latitude": lat, "longitude": lng}, "businessStatus": status,
            "primaryType": "mexican_restaurant"}
