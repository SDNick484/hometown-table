"""Configuration, read once from the environment at import. Complete in Task 1: no later task adds a setting.
Nothing here has a household value as its default. An empty value means the default; a malformed one stops the app
at start with a ValueError naming the key. On the host the environment comes from /etc/conf.d/hometown.

  DATA_DIR                  where hometown.db lives (default ./data)
  HOMETOWN_PORT             port for `python3 app.py` and the installer's gunicorn bind (default 8782)
  APP_NAME                  page title and home-screen name (default Hometown Table)
  TOWNS                     the area: `name|lat|lng` or `name|lat|lng|radius_km`, separated by ";" (default: the eight
                            towns of spec §4)
  TOWN_RADIUS_KM            a town's scan box is its centre +- this many km each way, unless the town gives its own (4)
  REST_DAYS                 "not lately" means no visit by the audience for more than this many days (45)
  CF_TEAM_DOMAIN, CF_AUD    the Cloudflare Access team domain (`<team>.cloudflareaccess.com`) and application audience
                            tag; both empty means no Access sign-in works
  ADMIN_EMAILS              comma-separated editor emails; empty refuses everyone
  LAN_EDIT                  1: requests from RFC 1918 and loopback addresses without Cloudflare headers are editor
                            `lan` (development and recovery only; default 0)
  HOMETOWN_READ_KEY         bearer key(s) for the MCP service, comma-separated for rotation: GET only
  HOMETOWN_API_KEY          bearer key(s) for the MCP service: read and write. A value in both lists is a write key.
  GOOGLE_PLACES_KEY         Google Places API (New) key; empty turns scanning and list matching off
  GOOGLE_MAX_PRO_PER_MONTH  hard cap on Place Details (Pro) calls per calendar month in America/Los_Angeles (3000)
  GOOGLE_PREFILL            1: the accept form offers "Use this" for a live Google value OSM lacks (default 1)
  SCAN_TILE_KM              scan tile size (1.5); SCAN_MIN_TILE_KM: smallest split of a full tile (0.25)
  SCAN_TYPES                comma-separated Google place types the scan searches (restaurant)
  CLOSURE_CHECK_DAYS        re-check a place's Google business status after this many days (28)

TZ is not a setting: the app's calendar is always America/Los_Angeles (the process's own TZ is left alone)."""
import os
import re
import unicodedata
from typing import NamedTuple

BASE = os.path.dirname(os.path.abspath(__file__))
TZ = "America/Los_Angeles"

_READ: list[str] = []   # every environment key read below, in order: ENV_KEYS


def _raw(name: str) -> str:
    _READ.append(name)
    return os.environ.get(name, "").strip()


def _str(name: str, default: str = "") -> str:
    return _raw(name) or default


def _int(name: str, default: int, minimum: int, maximum: int | None = None) -> int:
    raw = _raw(name)
    if not raw:
        return default
    if not re.fullmatch(r"[0-9]+", raw) or int(raw) < minimum or (maximum is not None and int(raw) > maximum):
        top = f" and at most {maximum}" if maximum is not None else ""
        raise ValueError(f"{name} must be a whole number of at least {minimum}{top}")
    return int(raw)


def _float(name: str, default: float) -> float:
    raw = _raw(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if not 0 < value < 1000:
        raise ValueError(f"{name} must be a number of kilometres above 0")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = _raw(name).lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be 1 or 0")


def _list(name: str) -> list[str]:
    return [v.strip() for v in _raw(name).split(",") if v.strip()]


class Town(NamedTuple):
    name: str
    lat: float
    lng: float
    radius_km: float


DEFAULT_TOWNS = ("Concord|37.9780|-122.0311;Clayton|37.9410|-121.9358;Pleasant Hill|37.9480|-122.0608;"
                 "Martinez|38.0194|-122.1341;Walnut Creek|37.9101|-122.0652;Pacheco|37.9835|-122.0753;"
                 "Pittsburg|38.0280|-121.8847;Lafayette|37.8858|-122.1180")


def _key(name: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c)).casefold()


def parse_towns(raw: str, radius_km: float) -> tuple[Town, ...]:
    """`name|lat|lng[|radius_km]` entries separated by ";" (newlines allowed) -> Towns, in the order given."""
    towns, seen = [], set()
    for entry in re.split(r"[;\n]", raw or ""):
        if not entry.strip():
            continue
        bits = [b.strip() for b in entry.split("|")]
        try:
            if len(bits) not in (3, 4) or not bits[0]:
                raise ValueError
            lat, lng = float(bits[1]), float(bits[2])
            radius = float(bits[3]) if len(bits) == 4 else radius_km
        except ValueError:
            raise ValueError(f"TOWNS: {entry.strip()!r} is not name|lat|lng or name|lat|lng|radius_km") from None
        if not (-90 <= lat <= 90 and -180 <= lng <= 180 and 0 < radius < 1000):
            raise ValueError(f"TOWNS: {entry.strip()!r} has a latitude, longitude or radius out of range")
        if _key(bits[0]) in seen:
            raise ValueError(f"TOWNS: {bits[0]!r} is listed twice")
        seen.add(_key(bits[0]))
        towns.append(Town(bits[0], lat, lng, radius))
    if not towns:
        raise ValueError("TOWNS must name at least one town")
    return tuple(towns)


def read_keys() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(READ_KEYS, API_KEYS) from HOMETOWN_READ_KEY and HOMETOWN_API_KEY. A value in both is a write key only."""
    api = tuple(dict.fromkeys(_list("HOMETOWN_API_KEY")))
    read = tuple(k for k in dict.fromkeys(_list("HOMETOWN_READ_KEY")) if k not in api)
    return read, api


DATA_DIR = _str("DATA_DIR") or os.path.join(BASE, "data")
DB_PATH = os.path.join(DATA_DIR, "hometown.db")
PORT = _int("HOMETOWN_PORT", 8782, 1, 65535)
APP_NAME = _str("APP_NAME", "Hometown Table")
TOWN_RADIUS_KM = _float("TOWN_RADIUS_KM", 4.0)
TOWNS: tuple[Town, ...] = parse_towns(_str("TOWNS", DEFAULT_TOWNS), TOWN_RADIUS_KM)
REST_DAYS = _int("REST_DAYS", 45, 1)
CF_TEAM_DOMAIN = re.sub(r"^https?://", "", _str("CF_TEAM_DOMAIN").strip("'\"")).split("/")[0].lower()
CF_AUD = _str("CF_AUD").strip("'\"")
ADMIN_EMAILS: frozenset[str] = frozenset(e.lower() for e in _list("ADMIN_EMAILS"))
LAN_EDIT = _bool("LAN_EDIT", False)
READ_KEYS, API_KEYS = read_keys()
GOOGLE_PLACES_KEY = _str("GOOGLE_PLACES_KEY")
GOOGLE_MAX_PRO_PER_MONTH = _int("GOOGLE_MAX_PRO_PER_MONTH", 3000, 0)
GOOGLE_PREFILL = _bool("GOOGLE_PREFILL", True)
SCAN_TILE_KM = _float("SCAN_TILE_KM", 1.5)
SCAN_MIN_TILE_KM = _float("SCAN_MIN_TILE_KM", 0.25)
if SCAN_MIN_TILE_KM > SCAN_TILE_KM:
    raise ValueError("SCAN_MIN_TILE_KM must not be larger than SCAN_TILE_KM")
SCAN_TYPES: tuple[str, ...] = tuple(_list("SCAN_TYPES")) or ("restaurant",)
CLOSURE_CHECK_DAYS = _int("CLOSURE_CHECK_DAYS", 28, 1)

ENV_KEYS: tuple[str, ...] = tuple(dict.fromkeys(_READ))
