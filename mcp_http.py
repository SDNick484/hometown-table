#!/usr/bin/env python3
"""The hometown-mcp service: Hometown Table's MCP over Streamable HTTP, behind Cloudflare Access (spec §10, §11).

The tools are mcp_server.py's. mcp_remote.py (a verbatim copy of mcp-server-shared's remote.py) adds the Access
assertion check on every request, the Host check and /healthz. This module adds one thing in front of it: AccessEmail
verifies the same assertion and puts its email in the ASGI scope, where the tools find it and send it to the app as
X-Hometown-As, so "me" is whoever signed in to Access. A client's own X-Hometown-As header is never read, and a request
whose assertion doesn't verify is refused by mcp_remote before any tool runs.

Settings (the environment; installer/init-hometown-mcp exports them from /etc/conf.d/hometown):
  HOMETOWN_MCP_WRITES   none (default), log (log_visit, set_want_to_try) or all; anything else is none, with a warning
  HOMETOWN_READ_KEY     the key at none (GET only)
  HOMETOWN_API_KEY      the key at log and all. At none the service refuses to start if it can see it. A comma-separated
                        value (the app accepts several, for rotation) means its first key.
  HOMETOWN_URL          the app, default http://127.0.0.1:8782 (bearer keys only work from loopback)
  MCP_HTTP_PORT (8783), MCP_HTTP_PATH (/hometown/mcp), MCP_PUBLIC_HOSTS, CF_ACCESS_TEAM_DOMAIN, CF_ACCESS_AUD,
  MCP_ALLOWED_EMAILS, MCP_HTTP_BIND: read by mcp_remote.http_config
  MCP_HTTP_BIND is unset on the host (the init script doesn't export it). The tunnel reaches the service from another
  host, so with Access configured the default is 0.0.0.0, as the app's HOMETOWN_BIND: every request but /healthz still
  needs a valid Access assertion. Without Access it is 127.0.0.1, and mcp_remote serves this machine only.
"""
import argparse
import logging
import os
import sys
from collections.abc import Mapping

import anyio.to_thread

import mcp_remote
import mcp_server

DEFAULT_PORT = 8783
DEFAULT_PATH = "/hometown/mcp"
log = logging.getLogger("hometown.mcp")


def write_level(env: Mapping[str, str]) -> tuple[str, list[str]]:
    """(level, startup warnings) from HOMETOWN_MCP_WRITES: none (the default), log or all. An unknown value is none."""
    raw = (env.get("HOMETOWN_MCP_WRITES") or "").strip()
    want = raw.lower()
    if not want:
        return "none", []
    if want in mcp_server.LEVELS:
        return want, []
    return "none", [f"unknown HOMETOWN_MCP_WRITES {raw!r}; using none"]


def service_key(env: Mapping[str, str], level: str) -> tuple[str, str]:
    """(name, value) of the key this level uses: HOMETOWN_READ_KEY at none, else HOMETOWN_API_KEY. Of several
    comma-separated keys, the first."""
    name = "HOMETOWN_READ_KEY" if level == "none" else "HOMETOWN_API_KEY"
    keys = [k.strip() for k in (env.get(name) or "").split(",") if k.strip()]
    return name, keys[0] if keys else ""


def refusal(env: Mapping[str, str], level: str) -> str | None:
    """Why the settings are unsafe or incomplete, or None. Never includes a key."""
    if level == "none" and (env.get("HOMETOWN_API_KEY") or "").strip():
        return ("HOMETOWN_API_KEY must not be in this service's environment while HOMETOWN_MCP_WRITES is none (it can "
                "write); at none it uses HOMETOWN_READ_KEY. Set HOMETOWN_MCP_WRITES=log or all, or re-run the installer "
                "to refresh /etc/init.d/hometown-mcp.")
    name, key = service_key(env, level)
    if not key:
        return f"{name} is empty; at write level {level} the service reads Hometown Table with it."
    return None


def hometown_url(env: Mapping[str, str]) -> str:
    return ((env.get("HOMETOWN_URL") or "").strip() or mcp_server.DEFAULT_URL).rstrip("/")


def http_config(env: Mapping[str, str]) -> mcp_remote.HttpConfig:
    env = dict(env)
    if not (env.get("MCP_HTTP_BIND") or "").strip() and (env.get("CF_ACCESS_TEAM_DOMAIN") or "").strip():
        env["MCP_HTTP_BIND"] = "0.0.0.0"
    parser = argparse.ArgumentParser()
    mcp_remote.add_http_arguments(parser, default_port=DEFAULT_PORT, default_path=DEFAULT_PATH)
    return mcp_remote.http_config(parser.parse_args([]), env)


class AccessEmail:
    """Outermost ASGI layer: a request whose Cf-Access-Jwt-Assertion the verifier accepts gets its email in
    scope[mcp_server.ACCESS_EMAIL_KEY]. It never refuses anything itself: mcp_remote's AccessMiddleware, inside it,
    checks the same assertion and answers 403. With no verifier (no Access configured: this machine only) nobody is
    named."""

    def __init__(self, app, verifier: mcp_remote.AccessVerifier | None):
        self.app = app
        self.verifier = verifier

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope.pop(mcp_server.ACCESS_EMAIL_KEY, None)
            email = await self._email(scope)
            if email:
                scope[mcp_server.ACCESS_EMAIL_KEY] = email
        await self.app(scope, receive, send)

    async def _email(self, scope) -> str | None:
        if self.verifier is None or scope.get("path") == mcp_remote.HEALTH_PATH:
            return None
        headers = dict(scope.get("headers") or [])
        token = headers.get(mcp_remote.ACCESS_HEADER.encode(), b"").decode("latin-1")
        if not token:
            return None
        try:
            claims = await self.verifier.verify(token)
        except Exception:  # AccessDenied, or anything else: AccessMiddleware refuses the request either way
            return None
        email = claims.get("email")
        return email.strip().lower() if isinstance(email, str) and email.strip() else None


def _health_check(api: mcp_server.Api):
    async def health() -> bool:
        return await anyio.to_thread.run_sync(api.healthy)

    return health


def app_for(server, cfg: mcp_remote.HttpConfig, api: mcp_server.Api,
            verifier: mcp_remote.AccessVerifier | None = None):
    """The ASGI app: AccessEmail around mcp_remote's app (Access check, Host check, /healthz) around the server.
    One verifier serves both layers."""
    if verifier is None and cfg.access is not None:
        verifier = mcp_remote.AccessVerifier(cfg.access)
    inner = mcp_remote.build_app(server, cfg, verifier, health_check=_health_check(api))
    return AccessEmail(inner, verifier)


def build_http_app(env: Mapping[str, str], verifier: mcp_remote.AccessVerifier | None = None,
                   api: mcp_server.Api | None = None):
    """The ASGI app for these settings. Tests inject `verifier` and `api`."""
    level, _ = write_level(env)
    if api is None:
        api = mcp_server.Api(hometown_url(env), service_key(env, level)[1])
    return app_for(mcp_server.build_server(api, level), http_config(env), api, verifier)


def serve(server, cfg: mcp_remote.HttpConfig, api: mcp_server.Api) -> None:  # pragma: no cover
    """Run until stopped, with the settings mcp_remote.serve_http uses (its uvicorn_settings)."""
    import uvicorn

    app = app_for(server, cfg, api)
    if cfg.access is None:
        log.warning("Serving %s on %s:%s WITHOUT Cloudflare Access checks.", cfg.path, cfg.bind, cfg.port)
    else:
        log.info("Serving %s on %s:%s; requests need a %s assertion.", cfg.path, cfg.bind, cfg.port, cfg.access.issuer)
    uvicorn.run(app, **mcp_remote.uvicorn_settings(cfg))


def main(env: Mapping[str, str] | None = None) -> int:
    """Run the service. Returns 2 with a one-line message on stderr when the settings are unsafe or incomplete."""
    env = dict(os.environ if env is None else env)
    level, warnings = write_level(env)
    problem = refusal(env, level)
    if problem:
        print(f"hometown-mcp: {problem}", file=sys.stderr)
        return 2
    try:
        cfg = http_config(env)
        cfg.check()
    except mcp_remote.ConfigError as exc:
        print(f"hometown-mcp: {exc}", file=sys.stderr)
        return 2
    api = mcp_server.Api(hometown_url(env), service_key(env, level)[1])
    server = mcp_server.build_server(api, level)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(message)s")
    for note in warnings:
        log.warning("%s", note)
    log.info("hometown-mcp starting: %s on %s:%s (%s)", cfg.path, cfg.bind, cfg.port,
             "read-only" if level == "none" else f"writes: {level}")
    serve(server, cfg, api)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
