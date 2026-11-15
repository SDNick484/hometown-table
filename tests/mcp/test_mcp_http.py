"""The remote MCP service (plan Task 13; spec §10, §11): write levels, the start-up refusals, and the Streamable HTTP app
behind the Access check, with the verified email forwarded as X-Hometown-As. The tools reach the real Flask app through
plugins/mcp_api.py; the Access assertions are signed with a key made here (the service's own Access application, not
the app's)."""
import json
import logging
import socket
import time

import pytest

pytest.importorskip("mcp.server.mcpserver")

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

import mcp_http
import mcp_remote
import mcp_server
from conftest import EDITOR, EDITOR2, READ_KEY, WRITE_KEY, mk_person, mk_place
from plugins.mcp_api import flask_api

TEAM = "example.cloudflareaccess.com"
AUD = "test-mcp-audience-tag"
HOST = "hometown.example.com"
PATH = "/hometown/mcp"
READ_TOOLS = {"suggest_places", "search_places", "get_place", "visit_history", "list_inbox", "list_people", "list_tags",
              "list_lists"}


def env_for(level="none", **over):
    env = {"HOMETOWN_URL": "http://127.0.0.1:8782", "HOMETOWN_READ_KEY": READ_KEY, "HOMETOWN_MCP_WRITES": level,
           "MCP_PUBLIC_HOSTS": HOST, "CF_ACCESS_TEAM_DOMAIN": TEAM, "CF_ACCESS_AUD": AUD}
    if level in ("log", "all"):
        env["HOMETOWN_API_KEY"] = WRITE_KEY
    env.update(over)
    return {k: v for k, v in env.items() if v is not None}


# ---------------------------------------------------------------- write levels and start-up
@pytest.mark.parametrize("raw,level", [(None, "none"), ("", "none"), ("none", "none"), ("log", "log"), (" ALL ", "all"),
                                       ("Log", "log")])
def test_write_level(raw, level):
    env = {} if raw is None else {"HOMETOWN_MCP_WRITES": raw}
    assert mcp_http.write_level(env) == (level, [])


def test_an_unknown_write_level_is_none_with_a_warning():
    assert mcp_http.write_level({"HOMETOWN_MCP_WRITES": "everything"}) == \
        ("none", ["unknown HOMETOWN_MCP_WRITES 'everything'; using none"])


@pytest.fixture
def served(monkeypatch):
    """What main() would serve, instead of serving it."""
    got = []
    monkeypatch.setattr(mcp_http, "serve", lambda server, cfg, api: got.append((server, cfg, api)))
    return got


@pytest.mark.parametrize("level", [None, "none", "everything"])
def test_none_level_refuses_to_start_with_write_key(served, capsys, level):
    env = env_for(HOMETOWN_MCP_WRITES=level, HOMETOWN_API_KEY=WRITE_KEY)
    assert mcp_http.main(env=env) == 2
    err = capsys.readouterr().err
    assert "HOMETOWN_API_KEY must not be in this service's environment" in err
    assert WRITE_KEY not in err and READ_KEY not in err
    assert served == []


@pytest.mark.parametrize("level,missing", [("none", "HOMETOWN_READ_KEY"), ("log", "HOMETOWN_API_KEY"),
                                           ("all", "HOMETOWN_API_KEY")])
def test_main_needs_the_key_for_its_level(served, capsys, level, missing):
    assert mcp_http.main(env=env_for(level, **{missing: ""})) == 2
    assert f"{missing} is empty" in capsys.readouterr().err
    assert served == []


def test_main_refuses_a_public_host_without_access(served, capsys):
    assert mcp_http.main(env=env_for(CF_ACCESS_TEAM_DOMAIN=None, CF_ACCESS_AUD=None)) == 2
    assert "without Cloudflare Access" in capsys.readouterr().err
    assert served == []


def test_main_serves_its_level_on_the_default_port_and_path(served):
    assert mcp_http.main(env=env_for("log", HOMETOWN_URL=None, HOMETOWN_API_KEY=f" {WRITE_KEY} ,old-key")) == 0
    (server, cfg, api), = served
    assert (cfg.bind, cfg.port, cfg.path, cfg.public_hosts) == ("0.0.0.0", 8783, PATH, (HOST,))
    assert (cfg.access.team_domain, cfg.access.audiences) == (TEAM, (AUD,))
    assert (api.base_url, api.key, api.as_email) == ("http://127.0.0.1:8782", WRITE_KEY, None)
    from plugins.mcp_api import tool_names
    assert tool_names(server) == READ_TOOLS | {"log_visit", "set_want_to_try"}


def test_behind_access_it_listens_where_the_tunnel_can_reach_it():
    # The tunnel runs on another host (README, Install), and the init script sets no MCP_HTTP_BIND: behind Access the
    # service listens on every interface, as the app does. Without Access it stays on this machine. MCP_HTTP_BIND wins.
    assert mcp_http.http_config(env_for()).bind == "0.0.0.0"
    assert mcp_http.http_config(env_for(MCP_PUBLIC_HOSTS=None, CF_ACCESS_TEAM_DOMAIN=None)).bind == "127.0.0.1"
    assert mcp_http.http_config(env_for(MCP_HTTP_BIND="192.0.2.10")).bind == "192.0.2.10"


def test_main_at_none_holds_the_read_key(served):
    assert mcp_http.main(env=env_for("none", MCP_HTTP_PORT="18783", MCP_HTTP_PATH="/x/mcp")) == 0
    (server, cfg, api), = served
    assert (cfg.port, cfg.path, api.key) == (18783, "/x/mcp", READ_KEY)


# ---------------------------------------------------------------- the HTTP app
@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def token(signing_key):
    def make(email=EDITOR, key=None, **over):
        now = int(time.time())
        claims = {"iss": f"https://{TEAM}", "aud": [AUD], "iat": now, "exp": now + 300, "email": email, "sub": "u1",
                  **over}
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, key or signing_key, algorithm="RS256", headers={"kid": "k1"})
    return make


def make_client(signing_key, api, level="log", allowed=(EDITOR, EDITOR2)):
    async def resolver(_token):
        return signing_key.public_key()

    policy = mcp_remote.AccessPolicy(team_domain=TEAM, audiences=(AUD,), emails=frozenset(allowed))
    verifier = mcp_remote.AccessVerifier(policy, key_resolver=resolver)
    app = mcp_http.build_http_app(env_for(level), verifier=verifier, api=api)
    return TestClient(app, base_url=f"http://{HOST}")


def rpc(client, method, params=None, *, token=None, sid=None, id_=1, headers=None, host=None):
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **(headers or {})}
    if token:
        h["Cf-Access-Jwt-Assertion"] = token
    if sid:
        h["Mcp-Session-Id"] = sid
    if host:
        h["Host"] = host
    body = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        body["id"] = id_
    if params is not None:
        body["params"] = params
    return client.post(PATH, headers=h, json=body)


def payload(resp):
    """The JSON-RPC message from a plain JSON or an SSE body."""
    for line in resp.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:])
    return resp.json()


def open_session(client, tok):
    r = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "test", "version": "1"}}, token=tok)
    assert r.status_code == 200, r.text
    sid = r.headers["mcp-session-id"]
    assert rpc(client, "notifications/initialized", token=tok, sid=sid, id_=None).status_code == 202
    return sid


def call_tool(client, tok, sid, name, args, id_=3, headers=None):
    return payload(rpc(client, "tools/call", {"name": name, "arguments": args}, token=tok, sid=sid, id_=id_,
                       headers=headers))["result"]


def test_a_verified_email_acts_as_that_editor(signing_key, token, con):
    alex = mk_person(con, "Alex", email=EDITOR)
    pid = mk_place(con)
    api = flask_api(WRITE_KEY)
    with make_client(signing_key, api) as client:
        tok = token(EDITOR)
        sid = open_session(client, tok)
        result = call_tool(client, tok, sid, "log_visit", {"place_id": pid, "people": [{"person": "me", "rating": 4}]})
    assert result["isError"] is False, result
    assert [tuple(r) for r in con.execute("SELECT person_id, rating FROM visit_person")] == [(alex, 4)]
    assert api.sent[-1]["headers"]["X-Hometown-As"] == EDITOR
    assert api.as_email is None                                    # the service's own Api never takes an identity


def test_a_forged_x_hometown_as_header_is_ignored(signing_key, token, con):
    mk_person(con, "Alex", email=EDITOR)
    sam = mk_person(con, "Sam", email=EDITOR2)
    pid = mk_place(con)
    api = flask_api(WRITE_KEY)
    with make_client(signing_key, api) as client:
        tok = token(EDITOR2)
        sid = open_session(client, tok)
        result = call_tool(client, tok, sid, "log_visit", {"place_id": pid, "people": ["me"]},
                           headers={"X-Hometown-As": EDITOR})
        assert result["isError"] is False, result
        r = rpc(client, "tools/call", {"name": "log_visit", "arguments": {"place_id": pid, "people": ["me"]}},
                sid=sid, id_=4, headers={"X-Hometown-As": EDITOR})
        assert r.status_code == 403                                  # the header alone, no assertion: nothing
    assert [r[0] for r in con.execute("SELECT person_id FROM visit_person")] == [sam]
    assert {s["headers"].get("X-Hometown-As") for s in api.sent} == {EDITOR2}


def test_each_request_acts_for_its_own_email(signing_key, token, con):
    alex = mk_person(con, "Alex", email=EDITOR)
    sam = mk_person(con, "Sam", email=EDITOR2)
    pid = mk_place(con)
    with make_client(signing_key, flask_api(WRITE_KEY)) as client:
        a, s = token(EDITOR), token(EDITOR2)
        sid_a, sid_s = open_session(client, a), open_session(client, s)
        assert call_tool(client, s, sid_s, "log_visit", {"place_id": pid, "people": ["me"]})["isError"] is False
        assert call_tool(client, a, sid_a, "log_visit", {"place_id": pid, "people": ["me"]}, id_=5)["isError"] is False
    rows = con.execute("SELECT vp.person_id FROM visit_person vp ORDER BY vp.visit_id").fetchall()
    assert [r[0] for r in rows] == [sam, alex]


def test_an_unlinked_editor_from_claude_gets_the_fix_in_plain_words(signing_key, token, con):   # Review Focus 4
    mk_place(con)
    with make_client(signing_key, flask_api(WRITE_KEY)) as client:
        tok = token(EDITOR)
        sid = open_session(client, tok)
        result = call_tool(client, tok, sid, "log_visit", {"place": "Casa Ejemplo", "people": ["me"]})
    assert result["isError"] is True
    assert result["content"][0]["text"] == "Error executing tool log_visit: Link your email to a person in Settings."
    assert con.execute("SELECT COUNT(*) FROM visit").fetchone()[0] == 0


REJECTS = {
    "no_assertion": lambda tok, other: None,
    "garbage": lambda tok, other: "not-a-jwt",
    "wrong_audience": lambda tok, other: tok(aud=["some-other-app"]),
    "expired": lambda tok, other: tok(exp=int(time.time()) - 600, iat=int(time.time()) - 900),
    "other_key": lambda tok, other: tok(key=other),
    "email_not_allowed": lambda tok, other: tok("riley@example.com"),
}


@pytest.mark.parametrize("case", REJECTS)
def test_rejected_requests_never_reach_the_app(signing_key, other_key, token, con, case):
    pid = mk_place(con)
    api = flask_api(WRITE_KEY)
    with make_client(signing_key, api) as client:
        r = rpc(client, "tools/call", {"name": "set_want_to_try", "arguments": {"place_id": pid, "on": True}},
                token=REJECTS[case](token, other_key), headers={"X-Hometown-As": EDITOR})
    assert (r.status_code, r.json()) == (403, {"error": "forbidden"})
    assert api.sent == []


def test_none_level_has_no_write_tools_over_http(signing_key, token, con):
    pid = mk_place(con)
    api = flask_api(READ_KEY)
    with make_client(signing_key, api, level="none") as client:
        tok = token()
        sid = open_session(client, tok)
        tools = payload(rpc(client, "tools/list", {}, token=tok, sid=sid, id_=2))["result"]["tools"]
        assert {t["name"] for t in tools} == READ_TOOLS
        result = call_tool(client, tok, sid, "log_visit", {"place_id": pid, "people": ["me"]})
    assert result["isError"] is True and result["content"][0]["text"] == "Unknown tool: log_visit"
    assert api.sent == []


def test_wrong_host_is_421(signing_key, token):
    with make_client(signing_key, flask_api(WRITE_KEY)) as client:
        r = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "t", "version": "1"}}, token=token(), host="evil.example.com")
    assert r.status_code == 421


def _closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_health_needs_no_sign_in_and_follows_the_app(signing_key):
    with make_client(signing_key, flask_api(READ_KEY)) as client:
        r = client.get("/healthz")
    assert (r.status_code, r.json()) == (200, {"status": "ok"})
    down = mcp_server.Api(f"http://127.0.0.1:{_closed_port()}", READ_KEY)
    with make_client(signing_key, down) as client:
        r = client.get("/healthz")
    assert r.status_code == 503


def test_an_app_that_answers_503_is_still_reachable(signing_key, monkeypatch):
    api = flask_api(READ_KEY)
    monkeypatch.setattr(api, "_send", lambda method, path, data, headers: (503, b'{"ok": false}'))
    with make_client(signing_key, api) as client:
        assert client.get("/healthz").status_code == 200


def test_secrets_never_reach_a_client_or_the_log(signing_key, other_key, token, con, caplog):
    caplog.set_level(logging.DEBUG)
    pid = mk_place(con)
    good, bad = token(), token(key=other_key)
    with make_client(signing_key, flask_api(WRITE_KEY)) as client:
        bodies = [rpc(client, "tools/list", {}, token=bad).text]
        sid = open_session(client, good)
        bodies.append(json.dumps(call_tool(client, good, sid, "get_place", {"place_id": pid})))
        bodies.append(json.dumps(call_tool(client, good, sid, "log_visit", {"place_id": pid, "people": ["me"]}, id_=4)))
    blob = caplog.text + "\n".join(bodies)
    for secret in (WRITE_KEY, READ_KEY, good, bad, "Bearer"):
        assert secret not in blob
