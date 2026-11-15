"""Shared helpers with no routes: the error type, request-body readers, folding and name matching, distance, the
clock and calendar (America/Los_Angeles), the town of an address, and the only outbound HTTP function.

Call these through the module (`common.now()`, `common.http_json(...)`), never `from common import now`: the tests
replace common.now (the `clock` fixture) and common.http_json (the `fake_http` fixture) on the module."""
import json
import math
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import request

import settings


class ApiError(Exception):
    """Raised anywhere while handling a request; app.py renders it as {"error": message, **extra} with this status.
    ApiError(409, "...", duplicate_of=7) -> 409 {"error": "...", "duplicate_of": 7}."""

    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


class UpstreamError(Exception):
    """An outbound call failed: no answer, an HTTP error (`status` holds its code), or an answer that isn't JSON."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- request bodies
def json_body() -> dict:
    """The request's JSON object. Anything else (no body, bad JSON, a list, a string) is 400."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "Expected a JSON object.")
    return data


def text(data: dict, key: str, max_len: int, required: bool = False) -> str | None:
    """data[key] stripped. Missing, null or blank gives None, or 400 when required. Not a string, or longer than
    max_len after stripping, is 400: text is never cut."""
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise ApiError(400, f"{key} must be text.")
    value = (value or "").strip()
    if not value:
        if required:
            raise ApiError(400, f"{key} is required.")
        return None
    if len(value) > max_len:
        raise ApiError(400, f"{key} must be at most {max_len} characters.")
    return value


def int_arg(value, lo: int, hi: int, default: int) -> int:
    """A whole number from a query string (or JSON) between lo and hi inclusive. None or "" gives default; anything
    else (text that isn't digits, a float, a bool, out of range) is 400."""
    if value is None or value == "":
        return default
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        number = None
    elif isinstance(value, int):
        number = value
    else:
        number = int(value) if re.fullmatch(r"-?[0-9]{1,18}", value.strip()) else None
    if number is None or not lo <= number <= hi:
        raise ApiError(400, f"Expected a whole number from {lo} to {hi}.")
    return number


# ---------------------------------------------------------------- folding and names
def fold(s) -> str:
    """Lowercase without accents, for search and comparison: "Phở" -> "pho", "JALAPEÑO" -> "jalapeno". None -> ""."""
    decomposed = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold().strip()


NAME_NOISE = frozenset({"the", "restaurant", "cafe", "&", "and"})


def name_words(s) -> frozenset[str]:
    """The folded words of a name, apostrophes dropped ("Joe's" -> "joes"), without the words NAME_NOISE ignores."""
    words = re.findall(r"[^\W_]+|&", re.sub(r"['’`]", "", fold(s)))
    return frozenset(w for w in words if w not in NAME_NOISE)


def names_match(a, b) -> bool:
    """Same place by name: the folded word sets, ignoring "the", "restaurant", "cafe", "&" and "and", are equal or one
    is a subset of the other. Names with no words left never match."""
    wa, wb = name_words(a), name_words(b)
    return bool(wa) and bool(wb) and (wa <= wb or wb <= wa)


# ---------------------------------------------------------------- distance and towns
def km_between(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(h)))


NEAREST_TOWN_KM = 10.0
_STATE_ZIP = re.compile(r"(?:\s+(?:ca|california))?(?:\s+\d{5}(?:-\d{4})?)?$")


def town_for(address, lat, lng) -> str | None:
    """The town of `settings.TOWNS` a place belongs to (spec §4): the city in its address when that names a town
    (case- and accent-insensitive), else the nearest town centre within 10 km of its pin, else None (the editor picks).
    The city is looked for in the address's comma-separated parts, last first, with a trailing state and ZIP dropped,
    so "1 Concord Blvd, Pleasant Hill CA 94523, USA" is Pleasant Hill."""
    by_key = {fold(t.name): t.name for t in settings.TOWNS}
    for part in reversed((address or "").split(",")):
        city = _STATE_ZIP.sub("", fold(part)).strip()
        if city in by_key:
            return by_key[city]
    if lat is None or lng is None:
        return None
    best = min(settings.TOWNS, key=lambda t: km_between(lat, lng, t.lat, t.lng))
    return best.name if km_between(lat, lng, best.lat, best.lng) <= NEAREST_TOWN_KM else None


# ---------------------------------------------------------------- the clock and the calendar
def now() -> int:
    """Seconds since the epoch. Every module asks here, so tests can freeze time (the `clock` fixture)."""
    return int(time.time())


def _local() -> datetime:
    return datetime.fromtimestamp(now(), ZoneInfo(settings.TZ))


def today() -> str:
    """Today as YYYY-MM-DD in America/Los_Angeles: a visit at 11:30 pm is that day in Concord, not tomorrow in UTC."""
    return _local().date().isoformat()


def month_key() -> str:
    """This calendar month as YYYY-MM in America/Los_Angeles (the Google usage counter's month)."""
    return _local().strftime("%Y-%m")


# ---------------------------------------------------------------- outbound HTTP
USER_AGENT = "HometownTable/1 (+https://github.com/SDNick484/hometown-table)"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def http_json(url: str, *, data=None, headers=None, timeout: float = 8) -> dict | list:
    """The only function that calls out (Google, OSM). GET without `data`; POST with it: a dict or list is sent as
    JSON, a str or bytes as it is (set Content-Type in `headers`). Returns the parsed JSON (an object, or a list for
    services that answer with one). Raises UpstreamError for no answer, an HTTP error (its `status` set), more than
    8 MB, or an answer that isn't JSON. Error messages name the host only, never the URL (it may carry a key)."""
    host = urllib.parse.urlsplit(url).hostname or "the server"
    sent = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
    body = None
    if data is not None:
        if isinstance(data, (dict, list)):
            body = json.dumps(data).encode()
            sent.setdefault("Content-Type", "application/json")
        else:
            body = data.encode() if isinstance(data, str) else bytes(data)
    req = urllib.request.Request(url, data=body, headers=sent, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise UpstreamError(f"{host} answered {e.code}", status=e.code) from None
    except (urllib.error.URLError, OSError) as e:
        raise UpstreamError(f"{host} could not be reached ({getattr(e, 'reason', e)})") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise UpstreamError(f"{host} sent more than {MAX_RESPONSE_BYTES} bytes")
    try:
        out = json.loads(raw)
    except ValueError:
        raise UpstreamError(f"{host} did not answer with JSON") from None
    if not isinstance(out, (dict, list)):
        raise UpstreamError(f"{host} did not answer with a JSON object")
    return out
