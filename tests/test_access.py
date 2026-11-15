"""Cloudflare Access JWTs, bearer keys from loopback, LAN_EDIT and acting-as (spec §11, §6.1)."""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from flask import g

import access
import app as hometown
import settings
from conftest import AUD, EDITOR, READ_KEY, TEAM, WRITE_KEY, mk_person

NO_SIGN_IN = {"error": "Sign in through Cloudflare Access."}


def jwt_header(token):
    return {"Cf-Access-Jwt-Assertion": token}


def test_no_jwt_is_401(client): assert client.get("/api/ping").status_code == 401


def test_no_jwt_message_and_every_path_is_covered(client):
    r = client.get("/api/ping")
    assert r.get_json() == NO_SIGN_IN
    for path in ("/", "/manifest.json", "/sw.js", "/static/style.css", "/static/js/core.js", "/robots.txt"):
        assert client.get(path).status_code == 401, path
    assert client.get("/healthz").status_code == 200


def test_valid_jwt_is_an_editor(client, jwt_for):
    r = client.get("/api/ping", headers=jwt_header(jwt_for(EDITOR)))
    assert r.status_code == 200 and r.get_json() == {"ok": True}


def test_jwt_for_unlisted_email_is_403(client, jwt_for):
    r = client.get("/api/ping", headers=jwt_header(jwt_for("casey@example.com")))
    assert r.status_code == 403 and r.get_json()["error"]


def test_email_case_does_not_matter(client, jwt_for):
    assert client.get("/api/ping", headers=jwt_header(jwt_for("Alex@Example.COM"))).status_code == 200


def test_bad_audience_expired_and_wrong_issuer_are_401(client, jwt_for):
    past = int(time.time()) - 7200
    for token in (jwt_for(EDITOR, aud="another-application"), jwt_for(EDITOR, exp=past, iat=past - 60, nbf=past - 60),
                  jwt_for(EDITOR, iss="https://another-team.cloudflareaccess.com"), jwt_for(EDITOR, exp=None),
                  jwt_for(None), "not-a-jwt"):
        r = client.get("/api/ping", headers=jwt_header(token))
        assert r.status_code == 401 and r.get_json() == NO_SIGN_IN


def test_a_token_signed_with_another_key_is_401(client):
    stranger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    token = jwt.encode({"aud": AUD, "iss": f"https://{TEAM}", "email": EDITOR, "iat": now, "exp": now + 600},
                       stranger, algorithm="RS256", headers={"kid": "hometown-test-key"})
    assert client.get("/api/ping", headers=jwt_header(token)).status_code == 401
    hs = jwt.encode({"aud": AUD, "iss": f"https://{TEAM}", "email": EDITOR, "exp": now + 600}, "a-shared-secret-that-is-long-enough-0123",
                    algorithm="HS256", headers={"kid": "hometown-test-key"})
    assert client.get("/api/ping", headers=jwt_header(hs)).status_code == 401


def test_unconfigured_access_refuses_everyone(client, jwt_for, monkeypatch):
    monkeypatch.setattr(settings, "CF_AUD", "")
    assert client.get("/api/ping", headers=jwt_header(jwt_for(EDITOR))).status_code == 401


def test_empty_admin_emails_refuses_everyone(client, jwt_for, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_EMAILS", frozenset())
    assert client.get("/api/ping", headers=jwt_header(jwt_for(EDITOR))).status_code == 403


def test_read_key_cannot_write(keyed): assert keyed("read").post("/api/ping", json={}).status_code == 403


def test_read_key_reads_and_write_key_writes(keyed):
    r = keyed("read").post("/api/ping", json={}, headers={"X-Hometown": "1"})
    assert r.status_code == 403 and r.get_json() == {"error": "The read key can only read."}
    assert keyed("read").get("/api/ping").status_code == 200
    assert keyed("write").get("/api/ping").status_code == 200
    assert keyed("write").post("/api/ping", json={}, headers={"X-Hometown": "1"}).status_code == 200   # no Origin needed


def test_wrong_key_is_401(client):
    r = client.get("/api/ping", headers={"Authorization": f"Bearer {WRITE_KEY}x"}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 401


def test_rotation_accepts_every_listed_key(client, monkeypatch):
    monkeypatch.setattr(settings, "API_KEYS", ("old-write-key-0123456789", WRITE_KEY))
    for key in settings.API_KEYS:
        r = client.get("/api/ping", headers={"Authorization": f"Bearer {key}"}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
        assert r.status_code == 200


def test_key_from_non_loopback_is_ignored(client):
    r = client.get("/api/ping", headers={"Authorization": f"Bearer {READ_KEY}"}, environ_base={"REMOTE_ADDR": "192.0.2.10"})
    assert r.status_code == 401


def test_key_through_cloudflare_is_ignored(client):
    """A tunnel on the same host would reach the app from loopback: a Cloudflare header means it came from outside."""
    r = client.get("/api/ping", headers={"Authorization": f"Bearer {READ_KEY}", "Cf-Ray": "0123456789abcdef-SJC"},
                   environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 401


def test_acting_as_needs_a_key_and_a_listed_email(client, keyed):
    def me(headers, remote="127.0.0.1"):
        with hometown.app.test_request_context("/api/ping", headers=headers, environ_base={"REMOTE_ADDR": remote}):
            return access.identity(), access.me_email()

    ident, email = me({"Authorization": f"Bearer {WRITE_KEY}", "X-Hometown-As": "Sam@Example.com"})
    assert ident == access.Identity(None, "key_write", "sam@example.com") and email == "sam@example.com"
    ident, email = me({"Authorization": f"Bearer {READ_KEY}", "X-Hometown-As": "sam@example.com"})
    assert ident.kind == "key_read" and email == "sam@example.com"
    assert me({"Authorization": f"Bearer {WRITE_KEY}", "X-Hometown-As": "casey@example.com"})[1] is None   # not listed
    assert me({"Authorization": f"Bearer {WRITE_KEY}"})[1] is None
    assert me({"X-Hometown-As": "sam@example.com"}) == (None, None)                                         # no key: nobody
    assert me({"Authorization": f"Bearer {WRITE_KEY}", "X-Hometown-As": "sam@example.com"}, remote="192.0.2.10") == (None, None)
    # and over HTTP: a forged header without a key is still 401
    assert client.get("/api/ping", headers={"X-Hometown-As": "sam@example.com"}).status_code == 401


def test_access_identity_ignores_x_hometown_as(jwt_for):
    with hometown.app.test_request_context("/api/ping", headers={**jwt_header(jwt_for(EDITOR)), "X-Hometown-As": "sam@example.com"}):
        assert access.identity() == access.Identity(EDITOR, "access", None)
        assert access.me_email() == EDITOR and access.who() == EDITOR


def test_who_names_the_writer(keyed, jwt_for, monkeypatch):
    with hometown.app.test_request_context("/", headers={"Authorization": f"Bearer {WRITE_KEY}"},
                                            environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        assert access.who() == "mcp"
    monkeypatch.setattr(settings, "LAN_EDIT", True)
    with hometown.app.test_request_context("/", environ_base={"REMOTE_ADDR": "192.168.1.20"}):
        assert access.identity() == access.Identity(None, "lan", None) and access.who() == "lan" and access.me_email() is None


def test_lan_edit_is_off_by_default_and_only_for_private_addresses(client, monkeypatch):
    assert client.get("/api/ping", environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 401
    monkeypatch.setattr(settings, "LAN_EDIT", True)
    assert client.get("/api/ping", environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 200
    assert client.get("/api/ping", environ_base={"REMOTE_ADDR": "10.0.0.5"}).status_code == 200
    assert client.get("/api/ping", environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200   # development
    assert client.get("/api/ping", environ_base={"REMOTE_ADDR": "198.51.100.7"}).status_code == 401
    r = client.get("/api/ping", headers={"Cf-Connecting-Ip": "203.0.113.9"}, environ_base={"REMOTE_ADDR": "192.168.1.20"})
    assert r.status_code == 401                                     # came through the tunnel without a JWT


def test_me_person_id(con, jwt_for):
    alex = mk_person(con, "Alex", email=EDITOR)
    mk_person(con, "Sam")
    with hometown.app.test_request_context("/", headers=jwt_header(jwt_for(EDITOR))):
        assert access.me_person_id(con) == alex
    with hometown.app.test_request_context("/", headers=jwt_header(jwt_for("sam@example.com"))):
        assert access.me_person_id(con) is None
    con.execute("UPDATE person SET active = 0 WHERE id = ?", (alex,))
    with hometown.app.test_request_context("/", headers=jwt_header(jwt_for(EDITOR))):
        assert access.me_person_id(con) is None


def test_identity_is_worked_out_once_per_request(jwt_for, monkeypatch):
    calls = []
    real = access._verify_jwt
    monkeypatch.setattr(access, "_verify_jwt", lambda token: calls.append(token) or real(token))
    with hometown.app.test_request_context("/", headers=jwt_header(jwt_for(EDITOR))):
        access.identity(); access.identity(); access.who()
        assert len(calls) == 1 and g.get("identity") is not None


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_sign_in_is_checked_before_anything_else(client, method):
    """Before routing (no 405 hint about which methods exist) and before the mutation guard."""
    assert getattr(client, method)("/api/ping", json={}).status_code == 401
    assert getattr(client, method)("/api/no-such-route", json={}).status_code == 401
