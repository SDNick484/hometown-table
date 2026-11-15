#!/usr/bin/env python3
"""Hometown Table's MCP tools (spec §10), over the app's HTTP API.

The MCP holds no database access. Every tool calls the app's /api/ through `Api`: `Authorization: Bearer <key>` (the
app accepts keys from loopback only), `X-Hometown: 1` on writes, and `X-Hometown-As: <email>` when the call acts for an
editor, which is how "me" works. So every write gets the same validation as the phone, and an app error comes back as
a tool error carrying the app's own message.

`build_server(api, level)` registers the read tools always, and the write tools of the level: "log" adds log_visit and
set_want_to_try, "all" adds import_places, sort_candidate, set_ownership and set_standing. Results never carry editor
emails (`added_by`, `by`, a person's `email`): people are named as the family entered them.

Two ways to run it:
  - mcp_http.py, the hometown-mcp service: Streamable HTTP behind Cloudflare Access. Each request's Access email is put
    in the ASGI scope (ACCESS_EMAIL_KEY) by mcp_http, and the tools act for it.
  - `python3 mcp_server.py [--as EMAIL]`, stdio, for development against a local app or on the host itself:
    HOMETOWN_URL (default http://127.0.0.1:8782), HOMETOWN_MCP_WRITES, and HOMETOWN_READ_KEY or HOMETOWN_API_KEY with
    the same rules as the service (mcp_http.service_key). --as sends X-Hometown-As, so "me" is that editor.
"""
import argparse
import copy
import json
import logging
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

log = logging.getLogger("hometown.mcp")

DEFAULT_URL = "http://127.0.0.1:8782"
LEVELS = ("none", "log", "all")
READ_TOOLS = ("suggest_places", "search_places", "get_place", "visit_history", "list_inbox", "list_people", "list_tags",
              "list_lists")
WRITE_TOOLS = {"none": (), "log": ("log_visit", "set_want_to_try"),
               "all": ("log_visit", "set_want_to_try", "import_places", "sort_candidate", "set_ownership", "set_standing")}
# Where mcp_http puts the Access-verified email of an HTTP request (ASGI scope; a client can't set it).
ACCESS_EMAIL_KEY = "hometown.access_email"
# Keys whose values are editor emails (or lan/mcp): never in a tool result.
EMAIL_KEYS = frozenset({"added_by", "by", "email"})
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
UNREACHABLE = "Hometown Table is unreachable right now. Try again in a minute."
BAD_KEY = "Hometown Table refused this service's key (HTTP 401). Check HOMETOWN_READ_KEY and HOMETOWN_API_KEY."

# (readOnlyHint, destructiveHint, idempotentHint, openWorldHint)
HINTS = {
    "log_visit": (False, False, False, False),         # a new visit each call
    "set_want_to_try": (False, False, True, False),
    "import_places": (False, False, True, True),       # asks Google about lines it doesn't know
    "sort_candidate": (False, True, False, False),     # dismiss hides a candidate
    "set_ownership": (False, True, True, False),
    "set_standing": (False, True, True, False),
}


class AppError(ToolError):
    """A failed call to the app. The text is what the model sees: the app's own message, never a key."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _fold(s) -> str:
    """Copy of common.fold (this module never imports the app's modules)."""
    decomposed = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold().strip()


def scrub(value):
    """`value` without editor emails: EMAIL_KEYS dropped everywhere, and any text that is just an email address hidden."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if k not in EMAIL_KEYS}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str) and EMAIL.fullmatch(value.strip()):
        return "(hidden)"
    return value


# ---------------------------------------------------------------- the app's API
class Api:
    """The app's /api/ over HTTP (urllib). `key` is the bearer key; `as_email` is sent as X-Hometown-As."""

    timeout = 20  # an import asks Google about up to 200 lines

    def __init__(self, base_url: str, key: str, as_email: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.key = key
        self.as_email = as_email

    def acting_as(self, email: str | None) -> "Api":
        """A copy that acts for `email`."""
        other = copy.copy(self)
        other.as_email = email
        return other

    def get(self, path: str, params: dict | None = None) -> Any:
        return self.request("GET", path, params=params)

    def send(self, method: str, path: str, body: dict) -> Any:
        return self.request(method, path, body=body)

    def request(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> Any:
        if params:
            pairs = [(k, _param(v)) for k, v in params.items() if v is not None]
            pairs = [(k, x) for k, v in pairs for x in (v if isinstance(v, list) else [v])]
            if pairs:
                path += "?" + urllib.parse.urlencode(pairs)
        headers = {"Authorization": f"Bearer {self.key}", "Accept": "application/json", "User-Agent": "hometown-mcp/1"}
        data = None
        if method != "GET":
            headers["X-Hometown"] = "1"
            headers["Content-Type"] = "application/json"
            data = json.dumps(body or {}).encode()
        if self.as_email:
            headers["X-Hometown-As"] = self.as_email
        try:
            status, raw = self._send(method, path, data, headers)
        except OSError as e:
            log.warning("Hometown %s %s failed: %s", method, path.split("?")[0], type(e).__name__)
            raise AppError(UNREACHABLE) from None
        if 200 <= status < 300:
            try:
                return json.loads(raw) if raw else {}
            except ValueError:
                raise AppError(f"Hometown Table answered something that isn't JSON (HTTP {status}).", status) from None
        log.warning("Hometown %s %s -> HTTP %s", method, path.split("?")[0], status)
        if status == 401:
            raise AppError(BAD_KEY, status)
        raise AppError(_app_message(raw) or f"Hometown Table API error ({status})", status)

    def healthy(self) -> bool:
        """The app answers /healthz (200, or 503 for its own problems, which its own health check reports)."""
        try:
            status, _ = self._send("GET", "/healthz", None, {"User-Agent": "hometown-mcp/1"})
        except OSError:
            return False
        return status in (200, 503)

    def _send(self, method: str, path: str, data: bytes | None, headers: dict) -> tuple[int, bytes]:
        """(status, body) for any HTTP answer; OSError when the app can't be reached. Tests replace this."""
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()


def _param(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return [_param(v) for v in value]
    return str(value)


def _app_message(raw: bytes) -> str | None:
    """The app's {"error": message, **extra} as one line: "message (key: value, ...)"."""
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(body, dict) or not isinstance(body.get("error"), str):
        return None
    extra = ", ".join(f"{k}: {v}" for k, v in scrub(body).items() if k != "error")
    return f"{body['error']} ({extra})" if extra else body["error"]


def place_id_for(api: Api, place_id: int | None, place: str | None) -> int:
    """The place a tool means: `place_id`, or the one place whose name matches `place` (GET /api/places?q=, past places
    included). An exact name beats a longer one; several left is refused with the choices."""
    if place_id is not None:
        return place_id
    name = (place or "").strip()
    if not name:
        raise AppError("Say which place: place_id, or place with its name.")
    found = api.get("/api/places", {"q": name, "past": True, "limit": 50})["places"]
    if not found:
        raise AppError(f'No place matches "{name}". Try search_places.')
    exact = [p for p in found if _fold(p["name"]) == _fold(name)]
    pick = exact or found
    if len(pick) == 1:
        return pick[0]["id"]
    choices = "; ".join(f"{p['name']} ({p.get('town') or 'no town'}, id {p['id']})" for p in pick[:10])
    more = f"; and {len(pick) - 10} more" if len(pick) > 10 else ""
    raise AppError(f'More than one place matches "{name}": {choices}{more}. Say which one with place_id.')


# ---------------------------------------------------------------- the server
INSTRUCTIONS = """Hometown Table: the family's list of small, independently owned restaurants in their East Bay towns,
with visits, per-person ratings (1 to 5 stars), a discovery inbox and curated lists.
- "Where should we go tonight?": suggest_places. Its modes: not_lately (favourites not visited lately; the default),
  want (starred), never_been, new_in_town, occasion, any. audience is everyone, adults, kids, me, or a person's name.
- Name places by place_id when you have it. A place name also works; if it matches several, the error lists them
  with their ids: ask which one, then use place_id.
- People and tags: list_people, list_tags. "me" is the signed-in editor's own person.
"""
_LOG_NOTE = "- To log a visit: log_visit with the place, who went (\"me\" or names) and optional 1-5 ratings.\n"
_NO_WRITES = ("- This connection is read-only: it cannot log visits or change anything. For that, use the Hometown "
              "Table app.\n")
_LOG_ONLY = ("- This connection can log visits and star places, but not sort the inbox, import lists or change a "
             "place's verdict; those are done in the app.\n")


def instructions_for(level: str) -> str:
    if level == "none":
        return INSTRUCTIONS + _NO_WRITES
    return INSTRUCTIONS + _LOG_NOTE + (_LOG_ONLY if level == "log" else "")


def _api_for(api: Api, ctx: Context | None) -> Api:
    """`api`, acting for the Access email mcp_http verified for this HTTP request (none on stdio)."""
    try:
        request = ctx.request_context.request if ctx is not None else None
    except (ValueError, AttributeError):
        request = None
    scope = getattr(request, "scope", None)
    email = scope.get(ACCESS_EMAIL_KEY) if isinstance(scope, dict) else None
    return api.acting_as(email) if email else api


def build_server(api: Api, level: str) -> MCPServer:
    """An MCPServer with the read tools and the write tools of `level` ("none", "log" or "all")."""
    if level not in LEVELS:
        raise ValueError(f"level must be one of {', '.join(LEVELS)}, not {level!r}")
    server = MCPServer("hometown", instructions=instructions_for(level))

    # ------------------------------------------------------------ read
    def suggest_places(ctx: Context, mode: str = "not_lately", audience: str = "everyone", cuisine: str | None = None,
                       town: str | None = None, tag: list[str] | None = None, list: list[str] | None = None,
                       liked_by_all: bool = False, near: str | None = None, open_now: bool | str | None = None,
                       seed: int | str | None = None, limit: int = 10, offset: int = 0) -> dict[str, Any]:
        """Where to eat tonight: the family's decider. Only places that count (independently owned) and are open.

        mode: not_lately (the audience's favourites, score 4+, not visited lately), want (starred, oldest first),
        never_been, new_in_town (found by the scan since it began), occasion (places with an occasion tag), any.
        audience: everyone, adults, kids, me, or one person's name. cuisine: text it contains. town: one of the towns.
        tag, list: tag or list names or ids; every one must match. liked_by_all: everyone who rated it gave 4 or 5.
        near: "lat,lng" (only if the user gave a location). open_now: true (unknown hours kept), "strict", or false.
        seed: shuffles the order (same seed, same order). limit 1 to 50, offset for more."""
        params = {"mode": mode, "audience": audience, "cuisine": cuisine, "town": town, "tag": tag, "list": list,
                  "liked_by_all": liked_by_all or None, "near": near, "open_now": open_now, "seed": seed,
                  "limit": limit, "offset": offset}
        return scrub(_api_for(api, ctx).get("/api/decide", params))

    def search_places(ctx: Context, query: str, include_past: bool = False, limit: int = 20) -> dict[str, Any]:
        """Find places by words in their name, cuisine, town, address or notes (accents and case ignored; every word
        must match). include_past adds places that closed or don't count any more. limit 1 to 50."""
        return scrub(_api_for(api, ctx).get("/api/places", {"q": query, "past": include_past, "limit": limit}))

    def get_place(ctx: Context, place_id: int | None = None, place: str | None = None, visits: int = 5) -> dict[str, Any]:
        """One place: its details, ownership verdict and standing with their history, tags, lists, and its most recent
        visits (up to `visits`, 1 to 50) with who went and their ratings. Give place_id, or place (its name)."""
        a = _api_for(api, ctx)
        pid = place_id_for(a, place_id, place)
        out = a.get(f"/api/places/{pid}")
        out["recent_visits"] = a.get("/api/visits", {"place": pid, "limit": max(1, min(int(visits), 50))})["visits"]
        return scrub(out)

    def visit_history(ctx: Context, place_id: int | None = None, place: str | None = None, person: str | None = None,
                      occasion: str | None = None, since: str | None = None, until: str | None = None,
                      limit: int = 50) -> dict[str, Any]:
        """Visits, newest first, each with who went and their ratings and notes. Filters: a place (place_id or its
        name), a person (name or id), occasion (text it contains), since and until (YYYY-MM-DD, inclusive).
        limit 1 to 500."""
        a = _api_for(api, ctx)
        pid = place_id_for(a, place_id, place) if place_id is not None or place else None
        return scrub(a.get("/api/visits", {"place": pid, "person": person, "occasion": occasion, "since": since,
                                           "until": until, "limit": limit}))

    def list_inbox(ctx: Context, tab: Literal["new", "backlog", "chain_hidden", "flags"] = "new", offset: int = 0,
                   limit: int = 20) -> dict[str, Any]:
        """The discovery inbox, one page of a tab, with the count of every tab. new: places found since the first scan;
        backlog: found by the first scan or imported; chain_hidden: looks like a chain; flags: places Google lists as
        closed. A candidate is sorted with sort_candidate. limit 0 to 50 (0: counts only)."""
        return scrub(_api_for(api, ctx).get("/api/inbox", {"tab": tab, "offset": offset, "limit": limit}))

    def list_people(ctx: Context, include_inactive: bool = False) -> dict[str, Any]:
        """The family: each person's id, name, group (adult or kid), and `linked` (whether an editor's sign-in is
        theirs, which is what makes "me" work)."""
        found = _api_for(api, ctx).get("/api/people", {"inactive": 1 if include_inactive else None})
        for p in found.get("people", []):
            p["linked"] = bool(p.get("email"))
        return scrub(found)

    def list_tags(ctx: Context) -> dict[str, Any]:
        """The tags (id, label, whether it is an occasion tag, how many places have it)."""
        return scrub(_api_for(api, ctx).get("/api/tags"))

    def list_lists(ctx: Context) -> dict[str, Any]:
        """The curated lists (id, name, url) with how many places, inbox candidates and unmatched lines each holds."""
        return scrub(_api_for(api, ctx).get("/api/lists"))

    # ------------------------------------------------------------ log
    def log_visit(ctx: Context, people: list[str | int | dict[str, Any]], place_id: int | None = None,
                  place: str | None = None, visited_on: str | None = None, occasion: str | None = None,
                  note: str | None = None) -> dict[str, Any]:
        """Log a visit to a place. people: who went, each "me" (the signed-in editor's own person), a name, a person
        id, or {"person": "me" or a name or an id, "rating": 1 to 5 or null, "note": "..."}. visited_on: YYYY-MM-DD,
        default today (never in the future). occasion and note are optional. Logging unstars the place."""
        a = _api_for(api, ctx)
        pid = place_id_for(a, place_id, place)
        body = {"people": people, "visited_on": visited_on, "occasion": occasion, "note": note}
        return scrub(a.send("POST", f"/api/places/{pid}/visits", {k: v for k, v in body.items() if v is not None}))

    def set_want_to_try(ctx: Context, on: bool, place_id: int | None = None, place: str | None = None) -> dict[str, Any]:
        """Star (on=true) or unstar a place as one to try."""
        a = _api_for(api, ctx)
        pid = place_id_for(a, place_id, place)
        return scrub(a.send("PUT", f"/api/places/{pid}/want", {"on": on}))

    # ------------------------------------------------------------ all
    def import_places(ctx: Context, names: list[str], list_name: str, list_url: str | None = None) -> dict[str, Any]:
        """Import restaurant names into a curated list (made if there is none by that name). Lines are cleaned
        (numbering, bullets, " - comments"); each is matched to a place or inbox candidate by name, else looked up on
        Google Maps in the towns, and new ones go to the inbox. At most 200 names. Returns the list and the counts:
        matched_places, new_candidates, linked_candidates, unmatched and unmatched_lines."""
        a = _api_for(api, ctx)
        made = a.send("POST", "/api/lists", {"name": list_name, "url": list_url, "get_or_create": True})
        counts = a.send("POST", f"/api/lists/{made['id']}/import", {"lines": names})
        return scrub({"list": made, **counts})

    def sort_candidate(ctx: Context, candidate_id: int, decision: Literal["accept", "dismiss"],
                       ownership: Literal["counts", "unsure", "no"] | None = None, reason: str | None = None,
                       want_to_try: bool = False, tags: list[int] | None = None, name: str | None = None,
                       town: str | None = None, address: str | None = None, cuisine: str | None = None,
                       phone: str | None = None, website: str | None = None, hours: str | None = None,
                       notes: str | None = None, add_anyway: bool = False) -> dict[str, Any]:
        """Sort an inbox candidate (ids from list_inbox). accept makes it a place, with the ownership verdict (counts:
        independently owned; unsure; no: a chain or group, kept out of suggestions) and the reason for it; the other
        fields override what the candidate brings (name is needed when it has none). add_anyway passes a "close by,
        same name" warning. dismiss: not a restaurant or not one of ours; it is never offered again."""
        a = _api_for(api, ctx)
        if decision == "dismiss":
            return scrub(a.send("POST", f"/api/inbox/{candidate_id}/dismiss", {}))
        if decision != "accept":
            raise AppError('decision must be "accept" or "dismiss".')
        fields = {"name": name, "town": town, "address": address, "cuisine": cuisine, "phone": phone,
                  "website": website, "hours": hours, "notes": notes}
        body = {"ownership": ownership, "ownership_reason": reason, "want_to_try": want_to_try, "tags": tags or [],
                "add_anyway": add_anyway, **{k: v for k, v in fields.items() if v is not None}}
        return scrub({"place": a.send("POST", f"/api/inbox/{candidate_id}/accept", body)})

    def set_ownership(ctx: Context, value: Literal["counts", "unsure", "no"], reason: str, place_id: int | None = None,
                      place: str | None = None, on: str | None = None) -> dict[str, Any]:
        """Change a place's ownership verdict, with the reason (required): counts (independently owned), unsure, or no
        (sold to a chain or group: it moves to Past places and is never suggested; its visits stay). on: the date it
        changed, YYYY-MM-DD, default today. Writes the place's history."""
        a = _api_for(api, ctx)
        pid = place_id_for(a, place_id, place)
        return scrub(a.send("POST", f"/api/places/{pid}/ownership", {"value": value, "reason": reason, "on": on}))

    def set_standing(ctx: Context, value: Literal["open", "temp_closed", "closed"], reason: str,
                     place_id: int | None = None, place: str | None = None, on: str | None = None) -> dict[str, Any]:
        """Change whether a place is open, temporarily closed or closed for good, with the reason (required). on: the
        date, YYYY-MM-DD, default today. Writes the place's history and clears a Google closure flag."""
        a = _api_for(api, ctx)
        pid = place_id_for(a, place_id, place)
        return scrub(a.send("POST", f"/api/places/{pid}/standing", {"value": value, "reason": reason, "on": on}))

    tools = {f.__name__: f for f in (suggest_places, search_places, get_place, visit_history, list_inbox, list_people,
                                     list_tags, list_lists, log_visit, set_want_to_try, import_places, sort_candidate,
                                     set_ownership, set_standing)}
    for name in READ_TOOLS + WRITE_TOOLS[level]:
        read_only, destructive, idempotent, open_world = HINTS.get(name, (True, False, True, False))
        server.add_tool(tools[name], annotations=ToolAnnotations(
            read_only_hint=read_only, destructive_hint=destructive, idempotent_hint=idempotent,
            open_world_hint=open_world))
    return server


def main(argv: list[str] | None = None, env: dict | None = None) -> int:
    """stdio. Returns 2 with a one-line message when the settings are unsafe (the same rules as the service)."""
    import mcp_http

    parser = argparse.ArgumentParser(description="Hometown Table MCP over stdio (development).")
    parser.add_argument("--as", dest="as_email", metavar="EMAIL",
                        help="act for this editor (sent as X-Hometown-As; the app honours it only for ADMIN_EMAILS)")
    args = parser.parse_args(argv)
    env = dict(os.environ if env is None else env)
    level, warnings = mcp_http.write_level(env)
    refusal = mcp_http.refusal(env, level)
    if refusal:
        print(f"hometown-mcp: {refusal}", file=sys.stderr)
        return 2
    for note in warnings:
        print(f"hometown-mcp: {note}", file=sys.stderr)
    _name, key = mcp_http.service_key(env, level)
    api = Api(mcp_http.hometown_url(env), key, (args.as_email or "").strip().lower() or None)
    build_server(api, level).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
