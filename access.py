"""Who is asking (spec §11, §6.1). Every route except /healthz needs an editor, one of:

  access     a Cloudflare Access JWT (Cf-Access-Jwt-Assertion) verified here: RS256 signature from the team's certs,
             audience CF_AUD, issuer https://<CF_TEAM_DOMAIN>, expiry; then its email must be in ADMIN_EMAILS
  key_read   Authorization: Bearer <HOMETOWN_READ_KEY>, from loopback only (the MCP service): GET and HEAD only
  key_write  Authorization: Bearer <HOMETOWN_API_KEY>, from loopback only: read and write
  lan        LAN_EDIT=1 and a request from an RFC 1918 or loopback address with no Cloudflare header

A key request may say whose behalf it acts on with X-Hometown-As: <email>, honoured only with a valid key from
loopback and only for an email in ADMIN_EMAILS (anything else is ignored, so "me" then has no person). The answer is
worked out once per request and kept in flask.g."""
import hmac
import ipaddress
import logging
from functools import wraps
from typing import NamedTuple

import jwt
from flask import g, request

import settings
from common import ApiError

log = logging.getLogger(__name__)

NO_SIGN_IN = "Sign in through Cloudflare Access."
NOT_AN_EDITOR = "This account isn't an editor here. Ask the owner to add it to ADMIN_EMAILS."
READ_ONLY = "The read key can only read."
READ_METHODS = ("GET", "HEAD")
CLOUDFLARE_HEADERS = ("Cf-Ray", "Cf-Connecting-Ip", "Cf-Access-Jwt-Assertion")


class Identity(NamedTuple):
    email: str | None       # the Access email; None for keys and lan
    kind: str               # access | lan | key_read | key_write
    acting_as: str | None   # keys only: the listed email from X-Hometown-As


_UNSET = object()


def identity() -> Identity | None:
    """This request's editor, or None."""
    found = g.get("identity", _UNSET)
    if found is _UNSET:
        found, g.access_status = _resolve()
        g.identity = found
    return found


def require() -> Identity:
    """The editor, or ApiError: 401 without a valid sign-in, 403 for a signed-in email that isn't listed, 403 for the
    read key on anything but GET/HEAD."""
    ident = identity()
    if ident is None:
        raise ApiError(403, NOT_AN_EDITOR) if g.get("access_status") == 403 else ApiError(401, NO_SIGN_IN)
    if ident.kind == "key_read" and request.method not in READ_METHODS:
        raise ApiError(403, READ_ONLY)
    return ident


def editor_required(view):
    """Decorator for every route: the view runs only for an editor (see require())."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        require()
        return view(*args, **kwargs)
    return wrapper


def who() -> str:
    """The `added_by` / `by` value for a write: the Access email, `lan`, or `mcp` for a bearer key."""
    ident = require()
    return {"access": ident.email, "lan": "lan"}.get(ident.kind) or "mcp"


def me_email() -> str | None:
    """The email "me" means: the Access email, or the X-Hometown-As email a key request acts for. None for lan."""
    ident = identity()
    if ident is None:
        return None
    return ident.email if ident.kind == "access" else ident.acting_as


def me_person_id(con) -> int | None:
    """The active person linked to me_email(), or None."""
    email = me_email()
    if not email:
        return None
    row = con.execute("SELECT id FROM person WHERE lower(email) = ? AND active = 1", (email,)).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------- how
def _resolve() -> tuple[Identity | None, int]:
    """(identity, 0), or (None, 401 or 403)."""
    key = _bearer()
    if key is not None and _is_local():
        kind = _key_kind(key)
        if kind is not None:
            return Identity(None, kind, _acting_as()), 0
    token = request.headers.get("Cf-Access-Jwt-Assertion")
    if token:
        email = _verify_jwt(token)
        if email is None:
            return None, 401
        if email not in settings.ADMIN_EMAILS:
            log.warning("Access: %s signed in but is not in ADMIN_EMAILS", email)
            return None, 403
        return Identity(email, "access", None), 0
    if settings.LAN_EDIT and _is_local(lan=True):
        return Identity(None, "lan", None), 0
    return None, 401


def _bearer() -> str | None:
    scheme, _, value = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


def _key_kind(key: str) -> str | None:
    """Every configured key is compared, in constant time, so timing says nothing about which (if any) matched."""
    given = key.encode()
    write = [hmac.compare_digest(given, k.encode()) for k in settings.API_KEYS]
    read = [hmac.compare_digest(given, k.encode()) for k in settings.READ_KEYS]
    return "key_write" if any(write) else "key_read" if any(read) else None


def _acting_as() -> str | None:
    email = request.headers.get("X-Hometown-As", "").strip().lower()
    return email if email and email in settings.ADMIN_EMAILS else None


def _is_local(lan: bool = False) -> bool:
    """From this host (or, with lan, a private RFC 1918 address) and not relayed by Cloudflare: a tunnel running on
    this host would also reach the app from loopback, and it always adds Cf-* headers."""
    if any(h in request.headers for h in CLOUDFLARE_HEADERS):
        return False
    try:
        ip = ipaddress.ip_address(request.remote_addr or "")
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return True
    return lan and ip.version == 4 and any(ip in ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


_jwk_clients: dict[str, jwt.PyJWKClient] = {}


def _verify_jwt(token: str) -> str | None:
    """The token's email (lowercased) when it is a valid Access JWT for this application, else None."""
    team, aud = settings.CF_TEAM_DOMAIN, settings.CF_AUD
    if not (team and aud):
        log.warning("Access: a JWT arrived but CF_TEAM_DOMAIN and CF_AUD are not both set")
        return None
    url = f"https://{team}/cdn-cgi/access/certs"
    try:
        client = _jwk_clients.get(url)
        if client is None:
            client = _jwk_clients[url] = jwt.PyJWKClient(url, cache_keys=True)
        key = client.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=aud, issuer=f"https://{team}",
                            options={"require": ["exp", "iss", "aud"]})
    except jwt.PyJWTError as e:
        log.warning("Access: JWT rejected: %s", e)
        return None
    email = claims.get("email")
    return email.strip().lower() if isinstance(email, str) and email.strip() else None
