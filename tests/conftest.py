"""Shared test setup, fixtures and factories. Never edited after Task 1: a task's own fixtures and helpers go in
tests/plugins/<task>.py, which this file loads as a pytest plugin.

settings.py reads the environment once at import, so the environment is set here before anything imports the app.
Every test starts from a fresh, migrated database. No test reaches the network: common.http_json fails unless the test
asks for `fake_http`, and a socket connect to anything but loopback raises.

Fixtures: con, client, editor, editor2, keyed, clock, jwt_for, fake_http.
Plain functions, imported with `from conftest import ...`: mk_place, mk_person, mk_tag, mk_visit, mk_candidate,
mk_list, fixture_json, and the constants below (EDITOR, EDITOR2, ORIGIN, READ_KEY, WRITE_KEY).

HOMETOWN_NO_SKIP=1 turns every skip into a failure (CI's ui and mcp jobs set it). A run that collects no tests at all
because the directories it was given hold no test files yet (tests/mcp before Task 13) exits 0 and says so; any other
"no tests collected" still fails."""
import copy
import glob
import json
import os
import socket
import sys
import tempfile
import time
import types
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
FIXTURES = os.path.join(TESTS, "fixtures")
DATA = tempfile.mkdtemp(prefix="hometown-test-")

TEAM = "example.cloudflareaccess.com"
AUD = "test-audience-tag"
CERTS_URL = f"https://{TEAM}/cdn-cgi/access/certs"
EDITOR, EDITOR2 = "alex@example.com", "sam@example.com"
READ_KEY = "test-read-key-0123456789"
WRITE_KEY = "test-write-key-0123456789"
ORIGIN = "http://localhost"  # the Flask test client's host

_SET = dict(DATA_DIR=DATA, CF_TEAM_DOMAIN=TEAM, CF_AUD=AUD, ADMIN_EMAILS=f"{EDITOR},{EDITOR2}",
            HOMETOWN_READ_KEY=READ_KEY, HOMETOWN_API_KEY=WRITE_KEY)
# Every other key settings.py reads is unset, so the tests see the defaults (checked against settings.ENV_KEYS below).
_UNSET = ("HOMETOWN_PORT", "APP_NAME", "TOWNS", "TOWN_RADIUS_KM", "REST_DAYS", "LAN_EDIT", "GOOGLE_PLACES_KEY",
          "GOOGLE_MAX_PRO_PER_MONTH", "GOOGLE_PREFILL", "SCAN_TILE_KM", "SCAN_MIN_TILE_KM", "SCAN_TYPES",
          "CLOSURE_CHECK_DAYS")
os.environ.update(_SET)
for _name in _UNSET:
    os.environ.pop(_name, None)
sys.path.insert(0, ROOT)

import jwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

import app as hometown  # noqa: E402
import common  # noqa: E402
import db  # noqa: E402
import settings  # noqa: E402

assert set(settings.ENV_KEYS) <= set(_SET) | set(_UNSET), \
    f"tests/conftest.py must set or unset {sorted(set(settings.ENV_KEYS) - set(_SET) - set(_UNSET))}"

# Each tests/plugins/<task>.py is a pytest plugin: its fixtures are available everywhere, and its helpers are
# imported with `from plugins.<task> import ...`.
pytest_plugins = sorted(f"plugins.{os.path.basename(p)[:-3]}" for p in glob.glob(os.path.join(TESTS, "plugins", "*.py"))
                        if not p.endswith("__init__.py"))

NO_SKIP = os.environ.get("HOMETOWN_NO_SKIP") == "1"


# ---------------------------------------------------------------- HOMETOWN_NO_SKIP and empty test directories
def _no_skip(report):
    if NO_SKIP and report.skipped and not hasattr(report, "wasxfail"):
        reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr)
        report.outcome = "failed"
        report.longrepr = f"skipped, and HOMETOWN_NO_SKIP=1 makes a skip a failure: {reason}"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    _no_skip(outcome.get_result())


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    outcome = yield
    _no_skip(outcome.get_result())


def _holds_test_files(path):
    if os.path.isfile(path):
        return True
    return any(f.startswith("test_") and f.endswith(".py") or f.endswith("_test.py")
               for _dir, _subdirs, files in os.walk(path) for f in files)


def pytest_sessionfinish(session, exitstatus):
    """`pytest tests/mcp` before any test file exists there: pytest says "no tests ran" and exits 5. That is not a
    failure of anything, so exit 0. Once the directory holds a test file the rule no longer applies, so a run that
    selects nothing (a typo in -k, a broken collection) still fails."""
    if exitstatus != pytest.ExitCode.NO_TESTS_COLLECTED:
        return
    args = [a.split("::")[0] for a in session.config.args]
    if args and not any(_holds_test_files(os.path.join(str(session.config.invocation_params.dir), a)) for a in args):
        session.exitstatus = pytest.ExitCode.OK
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_line(f"no test files in {', '.join(args)} yet: nothing to run")


# ---------------------------------------------------------------- no network
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
LOOPBACK = ("127.0.0.1", "::1", "localhost")


def _check_address(sock, address):
    if sock.family == getattr(socket, "AF_UNIX", None) or (isinstance(address, tuple) and address[0] in LOOPBACK):
        return
    raise AssertionError(f"tests never use the network (connect to {address!r}); use the fake_http fixture")


def _guarded_connect(self, address):
    _check_address(self, address)
    return _real_connect(self, address)


def _guarded_connect_ex(self, address):
    _check_address(self, address)
    return _real_connect_ex(self, address)


socket.socket.connect = _guarded_connect
socket.socket.connect_ex = _guarded_connect_ex


def _no_http(url, **_kwargs):
    raise AssertionError(f"tests never use the network (common.http_json({url!r})); use the fake_http fixture")


# ---------------------------------------------------------------- Cloudflare Access: a test key served as the team's certs
_SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_KID = "hometown-test-key"
_JWK = {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_SIGNING_KEY.public_key())), "kid": _KID, "alg": "RS256",
        "use": "sig"}


def _fake_fetch_data(client):
    """jwt.PyJWKClient.fetch_data: the team's certs URL gets the test key; anything else fails the test."""
    assert client.uri == f"https://{settings.CF_TEAM_DOMAIN}/cdn-cgi/access/certs", f"unexpected certs URL {client.uri}"
    return {"keys": [_JWK]}


def make_jwt(email, **claims_overrides):
    """An Access JWT for `email`, signed with the test key: audience CF_AUD, issuer https://<team>, valid for an hour
    of real time (PyJWT checks expiry against the real clock, not the `clock` fixture). An override of None drops that
    claim."""
    now = int(time.time())
    claims = {"aud": AUD, "iss": f"https://{TEAM}", "email": email, "sub": f"test-{email}", "type": "app",
              "iat": now, "nbf": now - 5, "exp": now + 3600, **claims_overrides}
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, _SIGNING_KEY, algorithm="RS256", headers={"kid": _KID})


# ---------------------------------------------------------------- per-test setup
@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    """A fresh migrated database, the test key behind the team's certs URL, and no HTTP."""
    for name in os.listdir(DATA):
        path = os.path.join(DATA, name)
        if os.path.isfile(path):
            os.remove(path)
    hometown.init_db()
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", _fake_fetch_data)
    monkeypatch.setattr(common, "http_json", _no_http)


@pytest.fixture
def con():
    c = db.connect(settings.DB_PATH)
    yield c
    c.close()


@pytest.fixture
def client():
    """Anonymous: no Access JWT, no key."""
    return hometown.app.test_client()


@pytest.fixture
def jwt_for():
    """jwt_for(email, **claims_overrides) -> a signed Access JWT (see make_jwt)."""
    return make_jwt


def _editor_client(email):
    c = hometown.app.test_client()
    c.environ_base.update(HTTP_CF_ACCESS_JWT_ASSERTION=make_jwt(email), HTTP_X_HOMETOWN="1", HTTP_ORIGIN=ORIGIN)
    return c


@pytest.fixture
def editor():
    """Signed in through Access as alex@example.com, with the mutation headers (X-Hometown: 1, same-host Origin) on
    every request."""
    return _editor_client(EDITOR)


@pytest.fixture
def editor2():
    """The second editor, sam@example.com, set up as `editor` is."""
    return _editor_client(EDITOR2)


@pytest.fixture
def keyed():
    """keyed(kind, as_email=None): a client calling from 127.0.0.1 with the bearer read ("read") or write ("write")
    key, as the MCP service does, and X-Hometown-As when as_email is given. It sends no X-Hometown header: pass
    headers={"X-Hometown": "1"} on writes."""
    def make(kind, as_email=None):
        key = {"read": READ_KEY, "write": WRITE_KEY}[kind]
        c = hometown.app.test_client()
        c.environ_base.update(REMOTE_ADDR="127.0.0.1", HTTP_AUTHORIZATION=f"Bearer {key}")
        if as_email is not None:
            c.environ_base["HTTP_X_HOMETOWN_AS"] = as_email
        return c
    return make


class Clock:
    """A frozen common.now(); common.today() and common.month_key() follow it. Starts at the real current second."""

    def __init__(self):
        self.t = int(time.time())

    def set(self, local_iso):
        """Wall-clock time in America/Los_Angeles: clock.set("2026-11-14T18:00")."""
        self.t = int(datetime.fromisoformat(local_iso).replace(tzinfo=ZoneInfo(settings.TZ)).timestamp())

    def set_utc(self, utc_iso):
        self.t = int(datetime.fromisoformat(utc_iso).replace(tzinfo=timezone.utc).timestamp())

    def advance(self, seconds):
        self.t += int(seconds)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(common, "now", lambda: c.t)
    return c


class FakeHttp:
    """Stands in for common.http_json. fake_http.on(match, response) adds a rule:

      match     a substring of the URL, or a predicate taking the call (call.url, .method, .data, .headers, .timeout)
      response  a dict (returned as the JSON), a "path/under/fixtures.json" string (loaded with fixture_json), an
                exception instance (raised, e.g. common.UpstreamError("gone", status=404)), a callable taking the call
                and returning any of these, or a list of any of these (one per call, in order; running out fails)

    The newest matching rule answers, so a test can override a plugin's default. Every call is in fake_http.calls. A
    call no rule matches raises AssertionError and fails the test even if the code under test catches it."""

    def __init__(self):
        self.rules = []
        self.calls = []
        self.unmatched = []

    def on(self, match, response):
        if isinstance(response, list):
            response = iter(copy.deepcopy(response))
        self.rules.append((match, response))
        return self

    def __call__(self, url, *, data=None, headers=None, timeout=8):
        call = types.SimpleNamespace(url=url, method="GET" if data is None else "POST", data=copy.deepcopy(data),
                                     headers=dict(headers or {}), timeout=timeout)
        self.calls.append(call)
        for match, response in reversed(self.rules):
            if (match in url) if isinstance(match, str) else match(call):
                return self._answer(response, call)
        self.unmatched.append(call)
        raise AssertionError(f"unexpected HTTP call: {call.method} {url}")

    def _answer(self, response, call):
        if isinstance(response, types.GeneratorType) or hasattr(response, "__next__"):
            try:
                response = next(response)
            except StopIteration:
                raise AssertionError(f"fake_http ran out of responses for {call.url}") from None
        if callable(response) and not isinstance(response, BaseException):
            response = response(call)
        if isinstance(response, BaseException):
            raise response
        if isinstance(response, str):
            return fixture_json(response)
        return copy.deepcopy(response)


@pytest.fixture
def fake_http(monkeypatch):
    fake = FakeHttp()
    monkeypatch.setattr(common, "http_json", fake)
    yield fake
    if fake.unmatched:
        pytest.fail("unexpected HTTP calls: " + ", ".join(f"{c.method} {c.url}" for c in fake.unmatched))


# ---------------------------------------------------------------- helpers and factories (import them from conftest)
def fixture_json(path):
    """The JSON file tests/fixtures/<path>, parsed."""
    with open(os.path.join(FIXTURES, path), encoding="utf-8") as f:
        return json.load(f)


def _insert(con, table, row):
    cols = ", ".join(row)
    marks = ", ".join("?" * len(row))
    return con.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values())).lastrowid


def mk_place(con, name="Casa Ejemplo", **cols) -> int:
    """A place (town Concord, ownership counts with reason "test", standing open, origin hand) with any column
    overridden. Returns its id."""
    now = common.now()
    row = {"name": name, "town": "Concord", "ownership": "counts", "ownership_reason": "test", "standing": "open",
           "origin": "hand", "added_by": EDITOR, "created_at": now, "updated_at": now, **cols}
    return _insert(con, "place", row)


def mk_person(con, name, grp="adult", email=None) -> int:
    order = con.execute("SELECT COUNT(*) FROM person").fetchone()[0]
    return _insert(con, "person", {"name": name, "grp": grp, "email": email, "sort_order": order})


def mk_tag(con, label, occasion=False) -> int:
    order = con.execute("SELECT COUNT(*) FROM tag").fetchone()[0]
    return _insert(con, "tag", {"label": label, "occasion": int(bool(occasion)), "sort_order": order})


def mk_visit(con, place_id, on, people, occasion=None) -> int:
    """A visit on `on` (YYYY-MM-DD); people is {person_id: rating or None}. Returns the visit id."""
    with db.transaction(con):
        vid = _insert(con, "visit", {"place_id": place_id, "visited_on": on, "occasion": occasion, "added_by": EDITOR,
                                     "created_at": common.now()})
        for person_id, rating in people.items():
            _insert(con, "visit_person", {"visit_id": vid, "person_id": person_id, "rating": rating})
    return vid


def mk_candidate(con, **cols) -> int:
    """A candidate (origin scan, status new, first seen now) with any column overridden. Returns its id."""
    row = {"origin": "scan", "status": "new", "first_seen_at": common.now(), **cols}
    return _insert(con, "candidate", row)


def mk_list(con, name) -> int:
    return _insert(con, "source_list", {"name": name, "created_at": common.now()})
