"""Task 15 test helpers: the Dinner Log and Where to Eat visit feeds served through fake_http in their contract's
shape (`GET <base><path>?after=<int>&limit=200`, bearer token, `{"version": 1, "visits": [...], "next": <int>}`), and
the four sync settings blanked for every test.

The settings are read by settings.py at import, and tests/conftest.py (never edited after Task 1) neither sets nor
unsets them, so a developer's own environment could switch a feed on: the autouse fixture below turns both off, and a
test turns one on with the `feeds` fixture (or monkeypatch)."""
import types
import urllib.parse

import pytest

import settings

DL_URL, DL_TOKEN = "https://dinner.example.com", "test-dinner-log-token"
WTE_URL, WTE_TOKEN = "https://wte.example.com", "test-where-to-eat-token"
SYNC_SETTINGS = ("DINNER_LOG_URL", "DINNER_LOG_TOKEN", "WTE_URL", "WTE_TOKEN")


@pytest.fixture(autouse=True)
def _visit_sync_off(monkeypatch):
    for name in SYNC_SETTINGS:
        monkeypatch.setattr(settings, name, "")


def dl_visit(ref, day, name, location=None, dish=None) -> dict:
    """One Dinner Log feed entry (path /api/restaurant-visits)."""
    return {"ref": str(ref), "day": day, "name": name, "location": location, "dish": dish}


def wte_visit(ref, day, name, location=None, place_id=None, address=None, lat=None, lng=None) -> dict:
    """One Where to Eat feed entry (path /feed/visits)."""
    return {"ref": str(ref), "day": day, "name": name, "location": location, "place_id": place_id,
            "address": address, "lat": lat, "lng": lng}


class Feed:
    """A feed answering from `entries` (refs are digit strings, the cursor is the last ref sent). `page_max` makes
    pages smaller than the limit asked for; `fail` (an exception) is raised instead of answering; `afters` records the
    cursor of every request."""

    def __init__(self, base, path, token):
        self.base, self.path, self.token = base, path, token
        self.entries, self.afters, self.page_max, self.fail = [], [], 200, None

    def __call__(self, call):
        url = urllib.parse.urlsplit(call.url)
        query = urllib.parse.parse_qs(url.query)
        assert f"{url.scheme}://{url.netloc}" == self.base and url.path == self.path, call.url
        assert call.method == "GET" and query["limit"] == ["200"], call.url
        assert call.headers.get("Authorization") == f"Bearer {self.token}"
        after = int(query["after"][0])
        self.afters.append(after)
        if self.fail is not None:
            raise self.fail
        page = [e for e in self.entries if int(e["ref"]) > after][:min(int(query["limit"][0]), self.page_max)]
        return {"version": 1, "visits": page, "next": int(page[-1]["ref"]) if page else after}


@pytest.fixture
def feeds(fake_http, monkeypatch):
    """Both feeds configured and served: feeds.dl and feeds.wte are Feeds; feeds.http is fake_http."""
    monkeypatch.setattr(settings, "DINNER_LOG_URL", DL_URL)
    monkeypatch.setattr(settings, "DINNER_LOG_TOKEN", DL_TOKEN)
    monkeypatch.setattr(settings, "WTE_URL", WTE_URL)
    monkeypatch.setattr(settings, "WTE_TOKEN", WTE_TOKEN)
    dl = Feed(DL_URL, "/api/restaurant-visits", DL_TOKEN)
    wte = Feed(WTE_URL, "/feed/visits", WTE_TOKEN)
    fake_http.on(DL_URL, dl)
    fake_http.on(WTE_URL, wte)
    return types.SimpleNamespace(dl=dl, wte=wte, http=fake_http)
