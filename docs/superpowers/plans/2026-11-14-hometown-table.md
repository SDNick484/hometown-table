# Hometown Table Implementation Plan

> **For agentic workers:** Each task is delivered as one Prompt issue run through issue-queue. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A private, phone-first app that answers "where tonight?" from the family's list of small, independently owned restaurants in eight East Bay towns, with visits, per-person ratings, a Google-plus-OSM discovery inbox, curated lists, and an MCP reachable from claude.ai.

**Architecture:** A new standalone Flask + SQLite app in the public repo `hometown-table`. `app.py` holds the Flask object and hooks and auto-registers one blueprint per module in `routes/`. Route-free logic lives in top-level modules that take a `sqlite3.Connection`. The frontend is hash-routed native ES modules with no build step. A second service, `hometown-mcp`, runs the MCP over Streamable HTTP behind Cloudflare Access and calls the app's `/api/` on loopback with a bearer key.

**Tech Stack:** Python 3.12, Flask 3, gunicorn, PyJWT + cryptography (Alpine `py3-jwt`, `py3-cryptography`), SQLite (WAL), pytest, Playwright (UI smoke), plain HTML/CSS/JS ES modules, `node --test`, Alpine + OpenRC installer; MCP venv with `mcp`, `uvicorn`, `pyjwt[crypto]`.

**Spec:** `docs/superpowers/specs/2026-11-14-hometown-table-design.md` (committed with this plan in Task 1). Section numbers below (§) refer to it.

## Constraints

### Language and versions
- Python 3.12 (CI also runs the newest 3.x). Runtime dependencies are only Alpine packages `py3-flask py3-gunicorn py3-jwt py3-cryptography` plus `sqlite` (the backup's `sqlite3`). Everything else is the standard library.
- The MCP service alone has a venv: `requirements-mcp.txt` = `mcp>=2.3,<3`, `uvicorn>=0.31`, `pyjwt[crypto]>=2.10`. App modules never import `mcp`, `uvicorn` or anything from the venv.
- Frontend: native ES modules in `static/js/`, `static/js/package.json` = `{"type": "module"}`, no framework, bundler or npm step. Node ≥ 20 for `node --check` and `node --test` only.

### The checks (the gate). Every PR runs all of them locally and CI must be green
```
python -m pytest -q
node --test tests/*.test.mjs
for f in static/js/*.js; do node --check "$f"; done
ruff check
shellcheck -s sh installer/*.sh ops/*
sh installer/build.sh && sh -n dist/setup-hometown.sh
```
CI jobs (`.github/workflows/ci.yml`): `pytest` (Python 3.12 and 3.x on Ubuntu), `alpine` (Alpine's own `py3-*` packages, `git` for the hygiene test), `lint` (ruff 0.17.5, `node --check`, `node --test`), `shellcheck`, `ui` (Playwright + Chromium, viewport 412 px), `mcp` (`requirements-mcp.txt`, `pytest tests/mcp`). The `ui` and `mcp` jobs set `HOMETOWN_NO_SKIP=1`, which turns any skip into a failure.

### Tools the checks need installed
- Python 3.12 with `pip install -r requirements-dev.txt` (flask, gunicorn, pyjwt, cryptography, pytest, ruff==0.17.5).
- Node ≥ 20.
- `shellcheck`.
- For `tests/ui/`: `pip install playwright && python -m playwright install chromium`. Headless; no display server. Without them the UI tests skip locally.
- For `tests/mcp/`: `pip install -r requirements-mcp.txt`. Without it they skip locally.
- No compiler. No network in any test: Google and OSM calls go through `common.http_json`, which tests replace with `fake_http`.

### Never committed
- Real visit history, ratings, occasions or notes; family members' names; emails other than `@example.com` and GitHub noreply; home or work addresses; real curated lists (Reddit threads, the taco trail); the Google API key, `CF_AUD`, the team domain, `HOMETOWN_READ_KEY`/`HOMETOWN_API_KEY`; `deploy/deploy.env`, `.env`, `data/`, `tests/hygiene_denylist.txt`.
- Tests and recorded fixtures use invented people (Alex, Sam, Jordan, Riley, Casey), invented restaurants ("Casa Ejemplo", "Pho Example", "Chain Burger Example"), `example.com` hosts, RFC 5737 addresses, and coordinates inside the town boxes. Google and OSM fixtures are hand-written in the real response shapes, not recorded from live calls.
- `tests/test_hygiene.py` enforces emails, URL hosts (allowed: `example.com`, `noreply.github.com`, `github.com`, `cloudflare.com`, `cloudflareaccess.com`, `googleapis.com`, `google.com`, `openstreetmap.org`, `overpass-api.de`, `komoot.io`, `www.w3.org`, `127.0.0.1`, the RFC 5737 ranges) and the git-ignored denylist.

### Project rules (from gift-exchange, with the spec's §13 departures)
- Every route except `/healthz` needs a verified editor (§11). Every mutation needs `X-Hometown: 1`, a JSON object body, and same-origin `Origin` (bearer-key requests exempt from Origin). Otherwise 400, or 403 for a foreign Origin. A read key on a non-GET is 403 `{"error": "The read key can only read."}`.
- Bodies via `common.json_body()`, strings via `common.text(data, key, max_len)`; over-length text is 400, never cut.
- Everything typed goes through `esc()` before `innerHTML`; `tests/test_frontend.py` scans every module. No `confirm(`/`alert(`, no inline handlers or styles.
- Headers and CSP exactly as §11. `<title>` and manifest use `APP_NAME` (default `Hometown Table`).
- Schema changes only through idempotent `db.migrate()` with `SCHEMA_VERSION`. Task 1 creates the whole v1 schema; no other task changes it. A task that finds it needs a schema or settings change stops and asks the owner.
- Timestamps are integer epoch seconds; dates are `YYYY-MM-DD` in America/Los_Angeles (`common.today()`).
- Commits: Conventional Commits, one logical change each, author `SDNick484 <SDNick484@users.noreply.github.com>`. Docs use OpenRC commands and say `vim`.
- Ports: app 8782 (`HOMETOWN_PORT`), MCP 8783 (`MCP_HTTP_PORT`).

## Shared files (every task adds to these; their shape lets branches merge without conflicts)

Git conflicts when two branches change the **same or adjacent lines**. So every shared file holds one slot per later task, each slot separated from its neighbours by a blank line and a heading or marker line that no task edits. A task edits only the lines inside its own slot, replacing the placeholder. No task appends to the end of a shared file, and no slot is a single line that several tasks extend.

| File | Shape | What a task changes |
|---|---|---|
| `CLAUDE.md` → **Status** | a loose list, one item per task, a blank line between items: `- Task 7 (Google client): planned` | its own item's `planned` → `done` |
| `CLAUDE.md` → **Modules** | one `### Task N: <name>` heading per task, each followed by the line `Not built yet.` and a blank line | replaces `Not built yet.` with its module notes (names, rules) |
| `README.md` | one `## <Feature>` heading per user-facing task (2, 3, 4, 6, 9, 10, 11, 12, 13, 14), each followed by `_Lands with Task N._` and a blank line | replaces its placeholder with the user docs |
| `static/js/screens.js` | one block per screen task (2, 3, 4, 6, 10, 11): `// --- Task N: <name> ---`, then `// (not built yet)`, then a blank line | replaces `// (not built yet)` with its `import "./<screen>.js";` lines (screens self-register) |
| `routes/` | a package; `app.py` registers the `bp` of every module in it, sorted by name | adds its own `routes/<name>.py`; never edits another |
| `commands/` | a package; `manage.py` runs `commands/<name>.py` (`NAME`, `HELP`, `add_arguments(p)`, `run(args) -> int`) | adds its own `commands/<name>.py` |
| `tests/conftest.py` | the shared fixtures and factories below; it loads every `tests/plugins/*.py` as a pytest plugin | never edited after Task 1; a task's own fixtures go in `tests/plugins/<task>.py` |
| `tests/fixtures/<service>/` | one JSON file per recorded response | adds its own files |
| `settings.py`, `.env.example`, `db.py` | complete in Task 1 | not edited |

---

### Task 1: Scaffold, schema, access, gate and shared files

**Files:**
- Create: `app.py`, `settings.py`, `db.py`, `common.py`, `access.py`, `health.py`, `manage.py`, `routes/__init__.py`, `routes/ping.py`, `commands/__init__.py`, `static/index.html`, `static/style.css` (Graphite Red, adapted from where-to-eat), `static/fonts/` (Archivo woff2 + `OFL-Archivo.txt` from where-to-eat), `static/manifest.json`, `static/icon.svg`, `static/sw.js`, `static/robots.txt`, `static/js/package.json`, `static/js/core.js`, `static/js/api.js`, `static/js/router.js`, `static/js/main.js`, `static/js/screens.js`, `requirements.txt`, `requirements-dev.txt`, `requirements-mcp.txt`, `ruff.toml`, `.gitignore`, `.env.example`, `deploy/deploy.env.example`, `LICENSE`, `CLAUDE.md`, `README.md`, `.github/workflows/ci.yml`; the spec and this plan under `docs/superpowers/`
- Test: `tests/conftest.py`, `tests/plugins/__init__.py`, `tests/fixtures/.gitkeep`, `tests/test_db.py`, `tests/test_common.py`, `tests/test_access.py`, `tests/test_app.py`, `tests/test_health.py`, `tests/test_hygiene.py`, `tests/test_frontend.py`, `tests/frontend.test.mjs`, `tests/ui/conftest.py`, `tests/ui/test_shell.py`, `tests/mcp/__init__.py`

**Produces:**
- `settings`: `DATA_DIR`, `DB_PATH`, `PORT=8782`, `APP_NAME`, `TOWNS: tuple[Town]` (`Town(name, lat, lng, radius_km)`, parsed from `TOWNS`, §4 defaults, `TOWN_RADIUS_KM=4.0`), `REST_DAYS=45`, `CF_TEAM_DOMAIN`, `CF_AUD`, `ADMIN_EMAILS: frozenset[str]` (lowercased), `LAN_EDIT=False`, `READ_KEYS`, `API_KEYS: tuple[str]` (comma-split `HOMETOWN_READ_KEY`/`HOMETOWN_API_KEY`; a value in both is dropped from `READ_KEYS`), `GOOGLE_PLACES_KEY`, `GOOGLE_MAX_PRO_PER_MONTH=3000`, `GOOGLE_PREFILL=True`, `SCAN_TILE_KM=1.5`, `SCAN_MIN_TILE_KM=0.25`, `SCAN_TYPES=("restaurant",)`, `CLOSURE_CHECK_DAYS=28`, `TZ="America/Los_Angeles"`.
- `db`: `SCHEMA_VERSION=1`, `connect(path) -> Connection` (Row factory, `foreign_keys=ON`, WAL, SQL function `fold`), `migrate(con)`, `transaction(con)` (context manager, `BEGIN IMMEDIATE`). Tables, all in v1:
  - `place(id, name, town, address, lat, lng, cuisine, phone, website, hours, want_to_try INT DEFAULT 0, notes, ownership CHECK IN ('counts','unsure','no'), ownership_reason, standing CHECK IN ('open','temp_closed','closed') DEFAULT 'open', standing_since, closure_flag CHECK IN ('CLOSED_PERMANENTLY','CLOSED_TEMPORARILY') NULL, closure_checked_at INT, google_place_id UNIQUE, osm_ref, wte_id, origin CHECK IN ('hand','scan','list','where_to_eat'), first_seen_at INT, added_by, created_at INT, updated_at INT, version INT DEFAULT 1)`
  - `place_event(id, place_id → place ON DELETE CASCADE, kind CHECK IN ('ownership','standing'), old_value, new_value, reason, on_date, by, created_at)`
  - `person(id, name, grp CHECK IN ('adult','kid'), email UNIQUE, sort_order INT, active INT DEFAULT 1)`
  - `tag(id, label, occasion INT DEFAULT 0, sort_order INT)`, `place_tag(place_id, tag_id, PRIMARY KEY both, both CASCADE)`
  - `visit(id, place_id → place, visited_on, occasion, note, added_by, created_at, version INT DEFAULT 1)`, `visit_person(visit_id → visit CASCADE, person_id → person, rating INT CHECK (rating BETWEEN 1 AND 5), note, PRIMARY KEY (visit_id, person_id))`
  - `source_list(id, name, url, created_at)`, `list_entry(id, list_id → source_list CASCADE, line, candidate_id, place_id, status CHECK IN ('matched','unmatched'), created_at)`
  - `candidate(id, google_place_id UNIQUE, osm_ref, osm_name, osm_address, osm_lat, osm_lng, osm_extra /* JSON: cuisine, phone, website, opening_hours */, chain_hint INT DEFAULT 0, wte_id, wte_json, g_name, g_address, g_lat, g_lng, g_status, g_type, g_fetched_at INT, origin CHECK IN ('scan','list','where_to_eat'), status CHECK IN ('pending','new','backlog','dismissed','accepted','chain_hidden'), first_seen_at INT, decided_at INT, place_id)`
  - `scan_run(id, started_at, finished_at, tiles, search_calls, detail_calls, new_candidates, error)`, `google_usage(month PRIMARY KEY, pro_calls INT, id_calls INT)`, `meta(key PRIMARY KEY, value)`
- `common`: `ApiError(status, message)`, `json_body() -> dict`, `text(data, key, max_len, required=False) -> str|None`, `int_arg(value, lo, hi, default)`, `fold(s) -> str`, `names_match(a, b) -> bool` (folded word sets, one a subset of the other, ignoring `the`, `restaurant`, `cafe`, `&`, `and`), `km_between(lat1, lng1, lat2, lng2) -> float`, `now() -> int`, `today() -> str`, `month_key() -> str` (both in `settings.TZ`), `town_for(address, lat, lng) -> str|None` (§4), `http_json(url, *, data=None, headers=None, timeout=8) -> dict` (the only outbound HTTP function; raises `UpstreamError`).
- `access`: `Identity(email, kind, acting_as)` with `kind` in `access|lan|key_read|key_write`; `identity() -> Identity|None`; `editor_required` (decorator; 401 `{"error": "Sign in through Cloudflare Access."}`, 403 for a non-listed email, 403 for a read key on a non-GET); `who() -> str` (`added_by`: the email, `lan`, or `mcp`); `me_email() -> str|None` (Access email, or `X-Hometown-As` when sent with a valid key from loopback and listed in `ADMIN_EMAILS`); `me_person_id(con) -> int|None`.
- `app`: `app`, `db()`, `INIT_HOOKS: list[Callable[[Connection], None]]` (modules append at import), `init_db()` (runs after every `routes` module is imported: `migrate`, then each hook), `routes/ping.py` (`GET`/`POST /api/ping`, for access tests); routes `GET /` (index shell, `__APP_NAME__` filled), `GET /manifest.json`, `GET /sw.js`, `GET /healthz`; mutation guard and headers as §11; `ApiError` → `{"error": msg}`.
- `health.problems(con) -> list[str]`: with `GOOGLE_PLACES_KEY` set, `"last scan failed"` if the newest `scan_run` has `error`, `"no scan in 9 days"` if none finished in 9 days; `/healthz` is 200 `{"ok": true}` or 503 `{"ok": false, "problems": [...]}`.
- Frontend: `core.js` exports `esc`, `fmtDate`, `ago(dateStr, today)`, `stars(n)`, `townLabel`; `api.js` exports `get(path)`, `send(method, path, body)` (adds `X-Hometown: 1`, JSON); `router.js` exports `register(pattern, {label, nav, render})` and `start(el)`; `main.js` starts the router; `screens.js` with the slot blocks.
- Test fixtures (`tests/conftest.py`): `con`, `client` (anonymous), `editor` (Access JWT for `alex@example.com`, mutation headers by default), `editor2` (`sam@example.com`), `keyed(kind, as_email=None)` (bearer read or write key from 127.0.0.1), `clock` (freezes `common.now`/`today`; `clock.set(local_iso)` in America/Los_Angeles, `clock.set_utc(utc_iso)`, `clock.advance(seconds)`), `jwt_for(email, **claims_overrides) -> str` (signs with the test key served as the team's certs), `fake_http` (`fake_http.on(predicate_or_substring, json_or_file_or_exception)`; unmatched calls fail the test; `fake_http.calls`), `fixture_json(path)`; factories `mk_place(con, name="Casa Ejemplo", **cols) -> int` (defaults: town Concord, ownership counts, reason "test", standing open, origin hand), `mk_person(con, name, grp="adult", email=None) -> int`, `mk_tag(con, label, occasion=False) -> int`, `mk_visit(con, place_id, on, people: dict[int, int|None], occasion=None) -> int`, `mk_candidate(con, **cols) -> int`, `mk_list(con, name) -> int`. `tests/ui/conftest.py`: `live_url` (threaded server on a free port), `page` (Playwright Chromium, 412×915, editor JWT in extra headers). `HOMETOWN_NO_SKIP=1` turns skips into failures.

**Consumes:** nothing.

**Needs merged or decided first:** nothing (the spec is approved).

**Needs the owner:** Yes. Create the public repo `SDNick484/hometown-table` and allow Actions; add `tests/hygiene_denylist.txt` locally with the family's real names and emails.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_db.py
def test_fresh_db_is_v1_with_every_table(con):
    assert con.execute("PRAGMA user_version").fetchone()[0] == 1
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"place","place_event","person","tag","place_tag","visit","visit_person","source_list",
            "list_entry","candidate","scan_run","google_usage","meta"} <= names
def test_migrate_twice_is_a_noop(con): ...            # schema text unchanged after a second migrate
def test_rating_outside_1_to_5_is_refused(con): ...   # IntegrityError for 0 and 6
# tests/test_common.py
def test_names_match():
    assert names_match("Casa Ejemplo", "casa ejemplo restaurant")
    assert names_match("Phở Example", "pho example") and not names_match("Casa Ejemplo", "Casa Otra")
def test_town_for_prefers_address_city_then_nearest():
    assert town_for("100 Main St, Walnut Creek, CA", 37.97, -122.03) == "Walnut Creek"
    assert town_for(None, 37.978, -122.031) == "Concord" and town_for(None, 37.33, -121.89) is None
def test_today_is_los_angeles(clock):
    clock.set_utc("2026-11-15T07:30"); assert today() == "2026-11-14"
def test_text_over_limit_is_400(): ...                  # ApiError 400, never truncated
# tests/test_access.py
def test_no_jwt_is_401(client): assert client.get("/api/ping").status_code == 401
def test_jwt_for_unlisted_email_is_403(client, jwt_for): ...
def test_bad_audience_expired_and_wrong_issuer_are_401(client, jwt_for): ...
def test_read_key_cannot_write(keyed): assert keyed("read").post("/api/ping", json={}).status_code == 403
def test_key_from_non_loopback_is_ignored(client): ...   # REMOTE_ADDR 192.0.2.10 + Bearer → 401
def test_acting_as_needs_a_key_and_a_listed_email(client, keyed): ...
# tests/test_app.py
def test_mutation_without_header_is_400_and_foreign_origin_403(editor): ...
def test_security_headers_and_csp_on_every_response(editor): ...  # exact CSP from §11
def test_routes_package_modules_are_registered(): ...  # a module in routes/ with bp is mounted
def test_index_uses_app_name(editor): ...
# tests/test_health.py
def test_healthz_ok_without_key_and_503_after_a_failed_scan(client, con, monkeypatch): ...
```
`/api/ping` (GET and POST, `@editor_required`, returns `{"ok": true}`) lives in `routes/ping.py` for these tests. Also `tests/test_frontend.py` (escaping scan, no `confirm(`/`alert(`, no inline handlers or styles, CSP-safe), `tests/frontend.test.mjs` (`esc`, `ago`, `stars`), `tests/ui/test_shell.py` (the shell loads at 412 px and shows `APP_NAME`), `tests/test_hygiene.py` (gift-exchange's, with the allowed hosts above).

- [ ] **Step 2: Run them and see them fail.** `python -m pytest -q`. Expected: import errors.

- [ ] **Step 3: Implement** the modules above. Adapt `access.identity` from where-to-eat's `access_identity` (PyJWKClient on `https://<CF_TEAM_DOMAIN>/cdn-cgi/access/certs`, RS256, audience `CF_AUD`, issuer `https://<CF_TEAM_DOMAIN>`); keys compared with `hmac.compare_digest`. `app.py` registers `routes` modules with `pkgutil.iter_modules`, sorted. `manage.py` dispatches `commands` the same way.

- [ ] **Step 4: Create the shared files in their slot shape** (table above). `CLAUDE.md`: commands (the gate), rules (Constraints above), layout, then **Status** with items for Tasks 1–14 (Task 1 `done`), then **Modules** with headings for Tasks 2–14. `README.md`: intro, development, then the placeholder headings. `static/js/screens.js`: the six slot blocks. `.env.example`: every `settings.py` key and the MCP keys (`HOMETOWN_MCP_WRITES`, `MCP_HTTP_PORT`, `MCP_HTTP_PATH=/hometown/mcp`, `MCP_PUBLIC_HOSTS`, `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`, `MCP_ALLOWED_EMAILS`, `HOMETOWN_URL=http://127.0.0.1:8782`), each with a comment line. CI uses globs (`ops/*`, `installer/*.sh`, `static/js/*.js`, `tests/*.test.mjs`) so later files need no CI edit.

- [ ] **Step 5: Run the gate.** All six commands pass; pytest reports skips only for `tests/ui` (if Playwright is absent) and `tests/mcp` (empty).

- [ ] **Step 6: Commit** (`chore: scaffold app, schema, access and gate`; spec and plan as `docs: add design and plan`).

---

### Task 2: Places, ownership, standing and Past places

**Files:**
- Create: `places.py`, `routes/places.py`, `static/js/place.js`, `static/js/place-edit.js`, `static/js/past.js`
- Modify: `static/js/screens.js` (Task 2 slot), `CLAUDE.md` (Status item, Task 2 Modules heading), `README.md` (Places section)
- Test: `tests/test_places.py`, `tests/ui/test_place.py`

**Produces:**
- `places.create(con, data: dict, by: str) -> int` (needs `name`, `ownership`, `ownership_reason`; may carry `origin` (default `hand`), `first_seen_at`, `google_place_id`, `osm_ref`, `wte_id`, `want_to_try`, `tag_ids` and every §5.4 field; `town` from `common.town_for` unless given; duplicate guard §5.4 raises `ApiError(409, …)` with `{"duplicate_of": id}` unless `data["add_anyway"]`), `places.get(con, place_id) -> dict` (fields, `tags`, `lists`, `history`, `last_visit` `{on, people: [{name, rating}]}`, `visit_count`), `places.update(con, place_id, data, version, by) -> dict` (409 `"Someone else just changed this place. Reload to see it."` on a stale version), `places.set_ownership(con, place_id, value, reason, by, on_date=None) -> dict`, `places.set_standing(con, place_id, value, reason, by, on_date=None) -> dict` (both write `place_event`; `set_standing` clears `closure_flag`), `places.delete(con, place_id)` (409 if visits, §5.3), `places.search(con, q, include_past=False, limit=20) -> list[dict]`, `places.past(con) -> list[dict]`, `places.set_tags(con, place_id, tag_ids)`, `places.set_want_to_try(con, place_id, on: bool)`.
- Routes: `POST /api/places`, `GET/PATCH/DELETE /api/places/<id>`, `POST /api/places/<id>/ownership`, `POST /api/places/<id>/standing`, `PUT /api/places/<id>/tags`, `PUT /api/places/<id>/want`, `GET /api/places?q=&past=`, `GET /api/past`.
- Screens: `#/p/<id>` (place page; links to `#/p/<id>/visits`), `#/p/<id>/edit`, `#/past` (nav "Past places").

**Consumes:** Task 1 only (`db`, `common`, `access.editor_required`, `access.who`, factories).

**Needs merged or decided first:** nothing.

**Needs the owner:** No.

- [ ] **Step 1: Write the failing tests**

```python
def test_sold_to_group_keeps_visits_and_moves_to_past(editor, con):   # Review Focus 1
    pid = mk_place(con); a = mk_person(con, "Alex"); mk_visit(con, pid, "2026-10-01", {a: 5})
    r = editor.post(f"/api/places/{pid}/ownership", json={"value": "no", "reason": "sold to a group"})
    assert r.status_code == 200
    place = editor.get(f"/api/places/{pid}").json
    assert place["ownership"] == "no" and place["visit_count"] == 1
    assert place["history"][0] == {"kind": "ownership", "old": "counts", "new": "no",
                                   "reason": "sold to a group", "on": "2026-11-14", "by": "alex@example.com"}
    assert [p["id"] for p in editor.get("/api/past").json["places"]] == [pid]
def test_ownership_needs_a_reason(editor, con): ...                      # 400 "Give a reason."
def test_delete_with_visits_is_409_and_without_is_204(editor, con): ...
def test_stale_version_is_409_and_saves_nothing(editor, editor2, con): ...
def test_duplicate_within_150m_needs_add_anyway(editor, con): ...        # 409 with duplicate_of
def test_town_from_address_city(editor): ...                             # "…, Martinez, CA" → Martinez
def test_set_standing_clears_closure_flag(editor, con): ...
def test_search_is_accent_insensitive_and_hides_past_by_default(editor, con): ...
```
`tests/ui/test_place.py`: open `#/p/<id>`, change ownership with a reason, see the history line and the "Past places" entry.

- [ ] **Step 2: Run** `python -m pytest -q tests/test_places.py`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `places.py` and `routes/places.py` (blueprint `bp`, every route `@editor_required`), then the three screens.
- [ ] **Step 4: Fill the Task 2 slots** in `screens.js`, `CLAUDE.md` and `README.md`.
- [ ] **Step 5: Run the gate.** All pass.
- [ ] **Step 6: Commit** (`feat(places): places, ownership and standing history, past places`).

---

### Task 3: People, tags, export and Settings

**Files:**
- Create: `people.py`, `tags.py`, `export.py`, `routes/settings.py`, `static/js/settings.js`
- Modify: `static/js/screens.js` (Task 3 slot), `CLAUDE.md` (Status item, Task 3 heading), `README.md` (Settings section)
- Test: `tests/test_people.py`, `tests/test_tags.py`, `tests/test_export.py`, `tests/ui/test_settings.py`

**Produces:**
- `people.list(con, include_inactive=False)`, `people.create(con, name, grp, email=None) -> int` (§6.1 name rules; email must be in `ADMIN_EMAILS`; 400 messages `"That name is reserved."`, `"That email isn't an editor."`), `people.update(con, person_id, **fields)`, `people.deactivate(con, person_id)` (delete only without visits).
- `tags.SEED = (("good for groups", True), ("private room", True), ("special occasion", True), ("takes reservations", False), ("kid-friendly", False), ("show off to guests", False))`, `tags.seed(con)` (only when the table is empty; `routes/settings.py` appends it to `app.INIT_HOOKS`), `tags.list(con)`, `tags.create/rename/reorder/delete(con, …)` (delete returns the number of places it was on).
- `export.to_json(con) -> dict`, `export.to_csv(con) -> str` (one row per visit person: place, town, ownership, visited_on, occasion, person, rating, note).
- Routes: `GET/POST /api/people`, `PATCH/DELETE /api/people/<id>`, `GET/POST /api/tags`, `PATCH/DELETE /api/tags/<id>`, `PUT /api/tags/order`, `GET /api/export.json`, `GET /api/export.csv`, `GET /api/settings/status` (`towns`, `rest_days`, `google_key_set`, `google_prefill`, last `scan_run`, this month's `google_usage`).
- Screen: `#/settings` (nav "Settings"): people, tags (with occasion switch), status, export links.

**Consumes:** Task 1 only.

**Needs merged or decided first:** nothing.

**Needs the owner:** No.

- [ ] **Step 1: Write the failing tests**

```python
def test_reserved_and_bad_names_are_400(editor):
    for n in ("everyone", "Kids", "a,b", "123"):
        assert editor.post("/api/people", json={"name": n, "grp": "adult"}).status_code == 400
def test_email_must_be_an_editor(editor): ...
def test_person_with_visits_is_deactivated_not_deleted(editor, con): ...
def test_seed_runs_once_with_three_occasion_tags(con):
    tags.seed(con); tags.seed(con)
    assert [t["label"] for t in tags.list(con) if t["occasion"]] == ["good for groups", "private room", "special occasion"]
def test_delete_tag_reports_place_count(editor, con): ...
def test_csv_has_one_row_per_visit_person(editor, con): ...
def test_status_never_shows_keys(editor): ...   # response has google_key_set true/false, no key text
```
- [ ] **Step 2: Run** them; expected FAIL.
- [ ] **Step 3: Implement.** The seed hooks in through `app.INIT_HOOKS`, so no shared file changes.
- [ ] **Step 4: Fill the Task 3 slots.**
- [ ] **Step 5: Run the gate.**
- [ ] **Step 6: Commit** (`feat(settings): people, tags, export`).

---

### Task 4: Visits and ratings

**Files:**
- Create: `visits.py`, `routes/visits.py`, `static/js/visits.js`
- Modify: `static/js/screens.js` (Task 4 slot), `CLAUDE.md`, `README.md` (Visits section)
- Test: `tests/test_visits.py`, `tests/ui/test_visits.py`

**Produces:**
- `visits.log(con, place_id, data, by, me_person_id) -> int`: `data = {"visited_on", "people": [{"person": id|name|"me", "rating": 1..5|None, "note"}], "occasion", "note"}`. `visited_on` defaults to `common.today()`, never in the future (400 `"That date is in the future."`). At least one person (400 `"Who went?"`). `"me"` with no linked person is 400 `"Link your email to a person in Settings."`. Names match active people, case- and accent-insensitive (400 `"No one called <name>."`). Clears `want_to_try` on the place in the same transaction.
- `visits.update(con, visit_id, data, version, by)` (409 on stale), `visits.delete(con, visit_id)`, `visits.history(con, place_id=None, person=None, occasion=None, since=None, until=None, limit=50) -> list[dict]`.
- `visits.latest_ratings(con, place_id, person_ids) -> dict[int, int]` and `visits.score(con, place_id, person_ids) -> float|None` (§6.3), `visits.liked_by_all(con, place_id, person_ids) -> bool`. Task 6 consumes these three.
- Routes: `POST /api/places/<id>/visits`, `PATCH/DELETE /api/visits/<id>`, `GET /api/visits?place=&person=&occasion=&since=&until=`, `GET /api/me` (`{"email", "person": {id, name}|null}`).
- Screen: `#/p/<id>/visits` (visit list and the log-visit form; "who went" defaults to me).

**Consumes:** Task 1 only (`access.me_person_id`, factories).

**Needs merged or decided first:** nothing.

**Needs the owner:** No.

- [ ] **Step 1: Write the failing tests**

```python
def test_late_night_visit_is_dated_today_in_los_angeles(editor, con, clock):   # Review Focus 5
    clock.set_utc("2026-11-15T07:30")          # 23:30 on Nov 14 in Concord
    pid = mk_place(con); mk_person(con, "Alex", email="alex@example.com")
    r = editor.post(f"/api/places/{pid}/visits", json={"people": [{"person": "me", "rating": 5}]})
    assert editor.get(f"/api/visits?place={pid}").json["visits"][0]["visited_on"] == "2026-11-14"
def test_me_without_link_is_400_and_saves_nothing(keyed, con):                  # Review Focus 4
    pid = mk_place(con)
    r = keyed("write", as_email="sam@example.com").post(f"/api/places/{pid}/visits",
            json={"people": [{"person": "me", "rating": 4}]}, headers={"X-Hometown": "1"})
    assert r.status_code == 400 and r.json["error"] == "Link your email to a person in Settings."
    assert con.execute("SELECT COUNT(*) FROM visit").fetchone()[0] == 0
def test_logging_clears_want_to_try(editor, con): ...
def test_future_date_and_unknown_name_are_400(editor, con): ...
def test_score_uses_each_persons_latest_rating(con):
    pid = mk_place(con); a, b = mk_person(con, "Alex"), mk_person(con, "Sam")
    mk_visit(con, pid, "2026-01-01", {a: 2, b: 4}); mk_visit(con, pid, "2026-06-01", {a: 5})
    assert visits.score(con, pid, [a, b]) == 4.5 and visits.liked_by_all(con, pid, [a, b])
def test_person_without_rating_is_on_the_visit(editor, con): ...
```
- [ ] **Step 2: Run**; FAIL. - [ ] **Step 3: Implement.** - [ ] **Step 4: Fill the Task 4 slots.** - [ ] **Step 5: Gate.** - [ ] **Step 6: Commit** (`feat(visits): visits, per-person ratings, me`).

---

### Task 5: Opening-hours parser

**Files:**
- Create: `hours.py`
- Modify: `CLAUDE.md` (Status item, Task 5 heading)
- Test: `tests/test_hours.py`

**Produces:** `hours.open_at(spec: str|None, when: datetime) -> bool|None` (None = unknown or unparseable, §7.2 subset), `hours.closes_at(spec, when) -> str|None` (`"9 pm"`), `hours.parse(spec) -> list[tuple[int, int, int]]|None` (weekday 0=Mo, start minute, end minute; past-midnight ranges split onto the next day).

**Consumes:** nothing beyond the scaffold.

**Needs merged or decided first:** nothing.

**Needs the owner:** No.

- [ ] **Step 1: Write the failing tests** (`when` is a naive datetime in LA local time)

```python
@pytest.mark.parametrize("spec,when,expected", [
    ("Mo-Fr 11:00-21:00", "2026-11-13 20:59", True), ("Mo-Fr 11:00-21:00", "2026-11-14 12:00", False),
    ("Tu-Su 11:00-14:30,17:00-21:00", "2026-11-14 15:00", False),
    ("Fr,Sa 17:00-01:00", "2026-11-15 00:30", True), ("24/7", "2026-11-14 04:00", True),
    ("Mo off; Tu-Su 10:00-20:00", "2026-11-16 12:00", False),
    ("Mo-Fr 11:00-21:00; PH off", "2026-11-13 12:00", None), ("sunrise-sunset", "2026-11-14 12:00", None),
    (None, "2026-11-14 12:00", None), ("", "2026-11-14 12:00", None)])
def test_open_at(spec, when, expected): ...
def test_closes_at(): assert hours.closes_at("Mo-Su 11:00-21:00", dt("2026-11-14 18:00")) == "9 pm"
```
- [ ] **Step 2: Run**; FAIL. - [ ] **Step 3: Implement** (anything outside the subset makes the whole spec unknown). - [ ] **Step 4: Fill the Task 5 slots.** - [ ] **Step 5: Gate.** - [ ] **Step 6: Commit** (`feat(hours): parse common opening_hours`).

---

### Task 6: The decider ("Tonight")

**Files:**
- Create: `decide.py`, `routes/decide.py`, `static/js/tonight.js`
- Modify: `static/js/screens.js` (Task 6 slot), `CLAUDE.md`, `README.md` (Where tonight section)
- Test: `tests/test_decide.py`, `tests/ui/test_tonight.py`

**Produces:**
- `decide.MODES = ("not_lately", "want", "never_been", "new_in_town", "occasion", "any")`
- `decide.parse_params(args: Mapping) -> dict` (raises `ParamError`, mapped to 400; accepts query strings and MCP kwargs; `near` as `"lat,lng"`; `tag`/`list` repeatable; `open_now` in `true|false|strict`; `limit` 1–50, default 10).
- `decide.decide(con, params, me_person_id=None, now=None) -> {"mode", "total", "places": [card]}`, where a card is `{id, name, town, cuisine, unsure, want_to_try, tags, last_visit, score, distance_km?, open: true|false|null, closes_at}`. Rules exactly as §7.1–7.3. `audience` resolves to person ids: `everyone`, `adults`, `kids`, `me`, or a name (400 `"No one called <name>."`). `seed` shuffles with `random.Random(seed)` after filtering and before `offset`/`limit`.
- Route: `GET /api/decide`. Screen: `#/` (home, nav "Tonight"): mode chips, audience select, filter row, Shuffle, cards linking to `#/p/<id>`. Remembers the last mode and audience per device in `localStorage`, wrapped in try/catch.

**Consumes:** Task 4's `visits.latest_ratings`, `visits.score`, `visits.liked_by_all`; Task 5's `hours.open_at`, `hours.closes_at`; Task 1's `access.me_person_id`.

**Needs merged or decided first:** Tasks 4 and 5.

**Needs the owner:** No.

- [ ] **Step 1: Write the failing tests**

```python
def test_not_lately_uses_rest_days_and_audience(con, clock):
    clock.set("2026-11-14T18:00"); a, k = mk_person(con, "Alex"), mk_person(con, "Riley", grp="kid")
    old = mk_place(con, name="Old Favorite"); recent = mk_place(con, name="Recent")
    mk_visit(con, old, "2026-09-01", {a: 5, k: 5}); mk_visit(con, recent, "2026-11-01", {a: 5, k: 5})
    assert [p["name"] for p in decide.decide(con, {"mode": "not_lately"})["places"]] == ["Old Favorite"]
def test_kids_audience_ignores_adult_only_visits(con): ...
def test_ineligible_places_never_appear(con): ...   # ownership no, standing closed/temp_closed
def test_unsure_places_appear_marked(con): ...
def test_new_in_town_needs_first_seen_after_baseline(con): ...   # meta baseline_at
def test_occasion_uses_flag_not_label_and_ignores_rest_days(con): ...
def test_open_now_keeps_unknown_unless_strict(con, clock): ...
def test_seed_is_reproducible_and_changes_order(con): ...
def test_list_filter_and_liked_by_all(con): ...
def test_me_audience_without_link_is_400(editor, con): ...
def test_bad_mode_is_400(editor): ...
```
`tests/ui/test_tonight.py`: three seeded places, the default mode lists the eligible one, Shuffle reorders, tapping opens `#/p/<id>`.

- [ ] **Step 2: Run**; FAIL. - [ ] **Step 3: Implement.** - [ ] **Step 4: Fill the Task 6 slots.** - [ ] **Step 5: Gate.** - [ ] **Step 6: Commit** (`feat(decide): where-tonight modes, filters, shuffle`).

---

### Task 7: Google Places client and the monthly cap

**Files:**
- Create: `google.py`, `commands/tidy.py`, `ops/hometown-tidy`
- Modify: `CLAUDE.md` (Status item, Task 7 heading)
- Test: `tests/test_google.py`, `tests/plugins/google.py` (helpers `g_ids_page(ids, next_token=None)`, `g_details(id, name, lat, lng, status="OPERATIONAL")`), `tests/fixtures/google/*.json`

**Produces:**
- `google.enabled() -> bool` (key set).
- `google.search_ids(con, *, text, rect=None, included_type=None) -> tuple[list[str], dict[str, str]]`: ids and `{old: new}` moves. POST `https://places.googleapis.com/v1/places:searchText` with `X-Goog-FieldMask: places.id,places.movedPlaceId,nextPageToken`, `strictTypeFiltering` when `included_type`, `locationRestriction.rectangle` when `rect=(south, west, north, east)`, following `nextPageToken`. Counts `id_calls`; never capped.
- `google.details(con, place_id) -> dict|None`: GET `https://places.googleapis.com/v1/places/<id>` with field mask `id,displayName,formattedAddress,location,businessStatus,primaryType`. Returns `{name, address, lat, lng, status, type}`. Raises `google.CapReached` when this month's `pro_calls` ≥ `GOOGLE_MAX_PRO_PER_MONTH`, checked and incremented in one transaction before the call. Returns None for 404.
- `google.usage(con) -> {"month", "pro_calls", "id_calls", "cap"}`.
- `google.tidy(con, now=None) -> int`: clears `g_name, g_address, g_lat, g_lng, g_status, g_type, g_fetched_at` on candidates whose `g_fetched_at` is 30 days old or more. `commands/tidy.py` (`NAME="tidy"`) runs it. `ops/hometown-tidy` runs `python3 /opt/hometown/manage.py tidy` as the `hometown` user.

**Consumes:** Task 1 only (`common.http_json`, `fake_http`).

**Needs merged or decided first:** nothing.

**Needs the owner:** No for the PR. The live key is supplied at deploy (Task 14).

- [ ] **Step 1: Write the failing tests**

```python
def test_cap_stops_pro_calls_and_counts_every_one(con, fake_http, monkeypatch):   # Review Focus 2
    monkeypatch.setattr(settings, "GOOGLE_MAX_PRO_PER_MONTH", 2)
    fake_http.on("places/", g_details("p1", "Casa Ejemplo", 37.97, -122.03))
    google.details(con, "p1"); google.details(con, "p2")
    with pytest.raises(google.CapReached): google.details(con, "p3")
    assert len(fake_http.calls) == 2 and google.usage(con)["pro_calls"] == 2
def test_cap_resets_next_month(con, fake_http, clock, monkeypatch): ...
def test_search_ids_uses_ids_only_mask_and_follows_pages(con, fake_http): ...   # header asserted exactly
def test_moved_ids_are_reported(con, fake_http): ...
def test_tidy_clears_google_columns_after_30_days_and_keeps_osm(con, clock): ...
def test_no_key_means_disabled_and_no_calls(con, fake_http, monkeypatch): ...
```
- [ ] **Step 2: Run**; FAIL. - [ ] **Step 3: Implement.** - [ ] **Step 4: Fill the Task 7 slots.** - [ ] **Step 5: Gate** (shellcheck covers `ops/hometown-tidy`). - [ ] **Step 6: Commit** (`feat(google): ids-only search, capped details, tidy`).

---

### Task 8: OpenStreetMap client

**Files:**
- Create: `osm.py`
- Modify: `CLAUDE.md` (Status item, Task 8 heading)
- Test: `tests/test_osm.py`, `tests/plugins/osm.py` (helpers `overpass_el(ref, name, lat, lng, **tags)`), `tests/fixtures/osm/*.json`

**Produces:**
- `osm.food_places(bbox) -> list[dict]`: one Overpass query for `amenity~"^(restaurant|cafe|fast_food|food_court|ice_cream)$"` and `shop=bakery` in the box. Each element becomes `{ref: "node/123", name, address, lat, lng, cuisine, phone, website, opening_hours, is_chain}`, with `is_chain` true when `brand` or `brand:wikidata` is set. The address is built from `addr:*`.
- `osm.match(name, lat, lng, elements) -> dict|None`: the nearest element within 100 m whose name `common.names_match`es.
- `osm.search(name, bbox) -> list[dict]`: Nominatim search limited to the box (for add-by-hand), same shape.
- `osm.geocode(address, town) -> tuple[float, float]|None`: Nominatim, then Photon (where-to-eat's order).
- Requests send `User-Agent: HometownTable/1 (+https://github.com/SDNick484/hometown-table)`. Overpass URL `https://overpass-api.de/api/interpreter`.

**Consumes:** Task 1 only.

**Needs merged or decided first:** nothing.

**Needs the owner:** No.

- [ ] **Step 1: Write the failing tests**

```python
def test_brand_tag_marks_chain(fake_http):
    fake_http.on("overpass", {"elements": [overpass_el("node/1", "Chain Burger Example", 37.97, -122.03, brand="Chain Burger")]})
    assert osm.food_places((37.9, -122.1, 38.0, -122.0))[0]["is_chain"] is True
def test_match_needs_name_and_100m(): ...   # 90 m same name matches; 120 m doesn't; 10 m other name doesn't
def test_geocode_falls_back_to_photon(fake_http): ...
def test_upstream_error_is_raised_not_swallowed(fake_http): ...
```
- [ ] **Step 2: Run**; FAIL. - [ ] **Step 3: Implement.** - [ ] **Step 4: Fill the Task 8 slots.** - [ ] **Step 5: Gate.** - [ ] **Step 6: Commit** (`feat(osm): food places, match, search, geocode`).

---

### Task 9: The weekly scan

**Files:**
- Create: `scan.py`, `commands/scan.py`, `ops/hometown-scan`
- Modify: `CLAUDE.md`, `README.md` (Discovery section)
- Test: `tests/test_scan.py`

**Produces:**
- `scan.tiles(town: Town, tile_km) -> list[rect]`, `scan.split(rect) -> list[rect]` (four quarters).
- `scan.run(con, now=None) -> dict` (the `scan_run` row). Follows §8.2 in the order fixed by the spec: search every tile × `SCAN_TYPES`; split a tile that returns 60, down to `SCAN_MIN_TILE_KM`; apply moves; insert unknown ids as `pending` candidates; fetch details for `pending` ones, newest first, until `CapReached`; match each to OSM from one `osm.food_places` call per town box; attach to an existing id-less place or `where_to_eat` candidate within 150 m with a matching name; otherwise set the status to `chain_hidden` if `is_chain`, else `backlog` (no `baseline_at` yet) or `new`; store OSM fields; run the closure check for places not checked in `CLOSURE_CHECK_DAYS`; set `meta.baseline_at` at the end of the first run that finishes. Errors are recorded in `scan_run.error` and re-raised. `commands/scan.py` (`NAME="scan"`) runs it and exits 1 on error. `ops/hometown-scan` runs it weekly as `hometown`.
- `scan.place_closure_flag(con, place_id, status)`: sets `closure_flag` for `CLOSED_PERMANENTLY` or `CLOSED_TEMPORARILY`; `OPERATIONAL` clears a flag that was dismissed earlier.

**Consumes:** Task 7's `google.search_ids`, `google.details`, `google.CapReached`, `google.enabled`; Task 8's `osm.food_places`, `osm.match`; Task 1's `common.names_match`, `common.km_between`, `settings.TOWNS`.

**Needs merged or decided first:** Tasks 7 and 8.

**Needs the owner:** No for the PR. The first live scan runs after Task 14's deploy, with the key set.

- [ ] **Step 1: Write the failing tests** (Google and OSM faked through the Task 7 and 8 plugins)

```python
def test_first_scan_is_backlog_and_sets_baseline(con, fake_scan): ...
def test_second_scan_new_ids_are_new(con, fake_scan): ...
def test_full_tile_is_split_until_min_size(con, fake_scan): ...
def test_known_ids_are_not_duplicated_and_moves_update_the_id(con, fake_scan): ...
def test_chain_brand_is_hidden(con, fake_scan): ...
def test_attaches_to_where_to_eat_candidate_and_hand_place(con, fake_scan): ...
def test_cap_leaves_pending_hidden_and_next_run_fills_them(con, fake_scan, monkeypatch): ...  # Review Focus 2
def test_closure_flag_set_not_standing(con, fake_scan): ...
def test_error_is_recorded_and_exit_code_1(con, fake_scan): ...
def test_no_key_does_nothing(con, fake_http): ...
```
`fake_scan` lives in `tests/test_scan.py`. It wires `fake_http` from the Task 7 and 8 helpers for one town box of two tiles.
- [ ] **Step 2: Run**; FAIL. - [ ] **Step 3: Implement.** - [ ] **Step 4: Fill the Task 9 slots.** - [ ] **Step 5: Gate.** - [ ] **Step 6: Commit** (`feat(scan): weekly discovery scan with baseline and closure flags`).

---

### Task 10: Inbox, flags and Add place

**Files:**
- Create: `inbox.py`, `routes/inbox.py`, `static/js/inbox.js`, `static/js/add.js`
- Modify: `static/js/screens.js` (Task 10 slot), `CLAUDE.md`, `README.md` (Inbox section)
- Test: `tests/test_inbox.py`, `tests/ui/test_inbox.py`

**Produces:**
- `inbox.rows(con, tab, offset=0, limit=20) -> {"tab", "total", "rows", "counts": {new, backlog, flags, chain_hidden}}`. `tab` is `new|backlog|chain_hidden|flags`; `pending`, `dismissed` and `accepted` never show. A row with no OSM match and expired or empty `g_*` re-fetches `google.details` for that row only. On `CapReached` the row keeps its id and shows `"Google details wait for next month."`.
- `inbox.accept(con, candidate_id, data, by) -> int`: `data` has `ownership`, `ownership_reason`, optional `want_to_try`, `tags` and field overrides. Builds the place from OSM fields, or `wte_json` for `where_to_eat` candidates, then the overrides. A Google value is used only when the override says `{"from_google": true}` for that field and `GOOGLE_PREFILL` is on (otherwise 400). With no pin, `osm.geocode`. Calls `places.create(..., origin=candidate.origin, first_seen_at=candidate.first_seen_at)`; the candidate becomes `accepted` and gets `place_id`; the candidate's list entries move to the place. `ownership = no` still creates the place.
- `inbox.dismiss(con, candidate_id)`, `inbox.unhide(con, candidate_id)` (`chain_hidden` → `new`), `inbox.confirm_flag(con, place_id, by)` (`places.set_standing` with reason "Google lists it as closed"), `inbox.dismiss_flag(con, place_id)`.
- `inbox.lookup(con, name, town=None) -> list[dict]`: `osm.search` plus, with a key, `google.search_ids` and `google.details` for the top three (counted). Each result is marked `in_list` (an existing place) or `in_inbox`.
- Routes: `GET /api/inbox?tab=&offset=`, `POST /api/inbox/<id>/accept`, `POST /api/inbox/<id>/dismiss`, `POST /api/inbox/<id>/unhide`, `POST /api/places/<id>/flag/confirm`, `POST /api/places/<id>/flag/dismiss`, `GET /api/lookup?name=&town=`.
- Screens: `#/inbox` (nav "Inbox" with the new count; tabs New, Backlog, Flags, and the collapsed Hidden as chains; Google values labelled "Google Maps"; OSM credit) and `#/add` (lookup, then the same accept-style form, then `POST /api/places`).

**Consumes:** Task 2's `places.create`, `places.set_standing`; Task 7's `google.details`, `google.search_ids`, `google.CapReached`, `google.enabled`; Task 8's `osm.geocode`, `osm.search`.

**Needs merged or decided first:** Tasks 2, 7 and 8.

**Needs the owner:** No.

- [ ] **Step 1: Write the failing tests**

```python
def test_accept_builds_place_from_osm_and_keeps_lists(editor, con): ...
def test_google_value_needs_explicit_from_google_and_prefill_on(editor, con, monkeypatch): ...
def test_doesnt_count_creates_a_past_place_and_scan_wont_offer_it(editor, con): ...
def test_where_to_eat_candidate_uses_its_own_fields(editor, con): ...
def test_refetch_only_unmatched_expired_rows_on_screen(editor, con, fake_http): ...
def test_cap_reached_row_says_so(editor, con, fake_http, monkeypatch): ...
def test_confirm_flag_sets_standing_with_history(editor, con): ...
def test_pending_dismissed_accepted_never_listed(editor, con): ...
def test_lookup_marks_existing_places(editor, con, fake_http): ...
```
- [ ] **Step 2: Run**; FAIL. - [ ] **Step 3: Implement.** - [ ] **Step 4: Fill the Task 10 slots.** - [ ] **Step 5: Gate.** - [ ] **Step 6: Commit** (`feat(inbox): sort candidates, closure flags, add by hand`).

---

### Task 11: Curated lists

**Files:**
- Create: `lists.py`, `routes/lists.py`, `static/js/lists.js`
- Modify: `static/js/screens.js` (Task 11 slot), `CLAUDE.md`, `README.md` (Lists section)
- Test: `tests/test_lists.py`, `tests/ui/test_lists.py`

**Produces:**
- `lists.clean_line(line) -> str|None`. Strips bullets (`-`, `*`, `•`), numbering (`1.`, `1)`), and anything after ` - `, ` – `, ` — ` or `: ` that follows a name. Empty means None.
- `lists.create(con, name, url=None) -> int` (unique by folded name; 409 `"There's already a list called <name>."`), `lists.get_or_create(con, name, url=None) -> int`, `lists.all(con)`, `lists.get(con, list_id) -> {list, places, candidates, unmatched}`, `lists.delete(con, list_id)` (entries go; places and candidates stay).
- `lists.import_lines(con, list_id, lines) -> {"matched_places", "new_candidates", "linked_candidates", "unmatched", "unmatched_lines"}`. Follows §8.5. Up to 200 lines (400 over). Duplicate lines within one import count once. Matching uses `google.search_ids(text=line, rect=<bounding box of all town boxes>)`, taking the first id. A `backlog` or `dismissed` candidate goes back to `new`. A `chain_hidden` one stays hidden but gains the list. An `accepted` one links to its place.
- `lists.retry(con, entry_id, text)`, `lists.drop(con, entry_id)`.
- Routes: `GET/POST /api/lists` (`POST` `{name, url, get_or_create}`; with `get_or_create` true an existing list is returned with 200 instead of 409), `GET/DELETE /api/lists/<id>`, `POST /api/lists/<id>/import` `{lines}`, `POST /api/lists/entries/<id>/retry` `{text}`, `DELETE /api/lists/entries/<id>`.
- Screens: `#/lists` (nav "Lists"), and `#/lists/<id>` (its places and candidates, the paste box, and unmatched lines with Search again and Drop). This is one departure from §8.5: unmatched lines live on the list's page, not in an inbox tab, so the Inbox and Lists screens don't share code.

**Consumes:** Task 7's `google.search_ids`, `google.enabled`; Task 1's `common.fold` and factories.

**Needs merged or decided first:** Task 7.

**Needs the owner:** No.

- [ ] **Step 1: Write the failing tests**

```python
def test_messy_paste_is_cleaned_and_deduplicated(editor, con, fake_http):   # Review Focus 3
    lid = mk_list(con, "Example Taco Trail")
    fake_http.on("searchText", g_ids_page(["g1"]))
    r = editor.post(f"/api/lists/{lid}/import", json={"lines": [
        "1. Casa Ejemplo – great al pastor", "", "• casa ejemplo", "  - Taqueria Ejemplo: open late"]}).json
    assert r["new_candidates"] + r["linked_candidates"] + r["matched_places"] + r["unmatched"] == 2
def test_dismissed_candidate_reopens_not_duplicates(editor, con, fake_http): ...
def test_chain_hidden_stays_hidden_but_gains_list(editor, con, fake_http): ...
def test_existing_place_by_name_links_without_google(editor, con, fake_http): ...
def test_no_key_keeps_lines_unmatched(editor, con, monkeypatch): ...
def test_over_200_lines_is_400(editor, con): ...
def test_clean_line(): assert lists.clean_line("2) Pho Example — get the #7") == "Pho Example"
```
- [ ] **Step 2: Run**; FAIL. - [ ] **Step 3: Implement.** - [ ] **Step 4: Fill the Task 11 slots.** - [ ] **Step 5: Gate.** - [ ] **Step 6: Commit** (`feat(lists): curated lists and paste import`).

---

### Task 12: Import from Where to Eat

**Files:**
- Create: `wte_import.py`, `commands/import_where_to_eat.py`
- Modify: `CLAUDE.md`, `README.md` (Import from Where to Eat section)
- Test: `tests/test_wte_import.py`, `tests/fixtures/where_to_eat/export.json` (invented: four places, one closed, one in San Francisco, one already present)

**Produces:** `wte_import.run(con, export: dict) -> {"created", "skipped_closed", "skipped_outside", "skipped_existing"}`. Follows §9. Each import creates `where_to_eat` candidates with status `new`, `wte_id`, and `wte_json` (name, address, cuisine, phone, website, hours, lat, lng). A place is in the area if its pin is inside a town box, or its `area` folds to a town name. It counts as existing if it has the same `wte_id`, or is within 150 m with `common.names_match`. A second run creates nothing. `commands/import_where_to_eat.py` (`NAME="import-where-to-eat"`, argument `path`) prints the counts.

**Consumes:** Task 1 only.

**Needs merged or decided first:** nothing.

**Needs the owner:** Yes, after deploy: download Where to Eat's export (**More → Download → JSON**), copy it to the host, and run `python3 /opt/hometown/manage.py import-where-to-eat <file>` as `hometown`.

- [ ] **Step 1: Write the failing tests**

```python
def test_imports_only_open_places_in_the_area(con):
    r = wte_import.run(con, fixture_json("where_to_eat/export.json"))
    assert r == {"created": 1, "skipped_closed": 1, "skipped_outside": 1, "skipped_existing": 1}
def test_second_run_is_a_noop(con): ...
def test_dishes_photos_and_notes_are_not_imported(con): ...
```
- [ ] **Step 2: Run**; FAIL. - [ ] **Step 3: Implement.** - [ ] **Step 4: Fill the Task 12 slots.** - [ ] **Step 5: Gate.** - [ ] **Step 6: Commit** (`feat(import): one-time import from Where to Eat`).

---

### Task 13: MCP server (stdio and remote)

**Files:**
- Create: `mcp_server.py`, `mcp_http.py`, `mcp_remote.py` (verbatim copy of `SDNick484/mcp-server-shared` `src/mcp_shared/remote.py` at its current main commit, with Matinee's three header lines: source and commit, `upstream-sha256`, hand-copy note), `docs/mcp-setup.md`
- Modify: `CLAUDE.md`, `README.md` (MCP section)
- Test: `tests/mcp/test_mcp_server.py`, `tests/mcp/test_mcp_http.py`, `tests/mcp/test_remote_copy.py` (the sha256 of the body matches the header)

**Produces:**
- `mcp_server.build_server(api: Api, level: str) -> FastMCP` registers the tools in §10's table, filtered by level. `Api(base_url, key, as_email=None)` wraps `urllib` calls to the app's `/api/`. It sends `Authorization: Bearer`, plus `X-Hometown: 1` on writes and `X-Hometown-As` when set. It turns app errors into tool errors that carry the app's message. Tool parameters mirror the routes: `suggest_places` maps to `GET /api/decide`, `log_visit` to `POST /api/places/<id>/visits`, `import_places` to `POST /api/lists` with `get_or_create` then `/import`, `sort_candidate` to accept or dismiss, and so on. Each tool resolves places by `place_id`, or by `place` name through `GET /api/places?q=`, refusing an ambiguous name and listing the matches.
- `mcp_server.main()`: stdio, with `HOMETOWN_URL` (default `http://127.0.0.1:8782`) and a key from the environment.
- `mcp_http.write_level(env) -> (level, warnings)`: `HOMETOWN_MCP_WRITES` in `none|log|all`, default `none`; an unknown value is `none` with a warning. `mcp_http.main()`: at `none` it refuses to start if `HOMETOWN_API_KEY` is set. It uses `HOMETOWN_READ_KEY` at `none` and `HOMETOWN_API_KEY` otherwise. It serves through `mcp_remote` on `MCP_HTTP_PORT` (8783) and `MCP_HTTP_PATH` (`/hometown/mcp`), and forwards the Access-verified email as `X-Hometown-As` for each request.
- `docs/mcp-setup.md`: the Access application for the MCP path with managed OAuth, adding the connector in claude.ai (which Claude Desktop and Claude Code then use), the write levels, key rotation, and stdio for development.

**Consumes:** the HTTP API of Task 2 (`/api/places…`), Task 3 (`/api/people`, `/api/tags`), Task 4 (`/api/places/<id>/visits`, `/api/visits`), Task 6 (`/api/decide`), Task 10 (`/api/inbox…`), Task 11 (`/api/lists…`); Task 1's `X-Hometown-As` handling.

**Needs merged or decided first:** Tasks 2, 3, 4, 6, 10 and 11.

**Needs the owner:** No for the PR. Owner steps are in Task 14.

- [ ] **Step 1: Write the failing tests** (`tests/mcp/`; skipped without `mcp`, failing in CI's `mcp` job). They run against the real Flask app through a test `Api` that calls `app.test_client()` with the keyed headers.

```python
def test_none_level_has_no_write_tools(): assert "log_visit" not in tool_names(build_server(api, "none"))
def test_log_level_logs_a_visit_for_me(api_as_alex, con): ...   # person "me" resolves through X-Hometown-As
def test_ambiguous_place_name_is_refused_with_choices(api, con): ...
def test_import_places_creates_list_then_imports(api_all, con, fake_http): ...
def test_tool_results_never_contain_editor_emails(api, con): ...  # added_by stripped
def test_none_level_refuses_to_start_with_write_key(monkeypatch): ...
def test_remote_copy_matches_its_pinned_sha(): ...
```
- [ ] **Step 2: Run** `pytest tests/mcp`; FAIL. - [ ] **Step 3: Implement.** - [ ] **Step 4: Fill the Task 13 slots.** - [ ] **Step 5: Gate**, plus `pip install -r requirements-mcp.txt && HOMETOWN_NO_SKIP=1 pytest -q tests/mcp`. - [ ] **Step 6: Commit** (`feat(mcp): stdio and remote MCP with tiered writes`).

---

### Task 14: Installer, services, backups and health checks

**Files:**
- Create: `installer/setup-head.sh`, `installer/build.sh`, `installer/init-hometown`, `installer/init-hometown-mcp`, `ops/hometown-backup`
- Modify: `CLAUDE.md`, `README.md` (Install section)
- Test: `tests/test_installer_static.py`

**Produces:**
- `installer/build.sh` writes `dist/setup-hometown.sh`: the head plus a base64 tarball of every top-level `*.py`, `routes/`, `commands/`, `static/`, `ops/`, `requirements-mcp.txt`, and the two init scripts.
- `setup-head.sh` (Alpine, OpenRC, safe to re-run, `--uninstall` keeps data):
  - apk `py3-flask py3-gunicorn py3-jwt py3-cryptography sqlite python3 py3-virtualenv`
  - user `hometown`; `/opt/hometown` replaced each run; `/var/lib/hometown` and `/etc/conf.d/hometown` kept
  - `add_setting` only adds missing keys
  - generates `HOMETOWN_READ_KEY` and `HOMETOWN_API_KEY` once, never printed
  - venv `/opt/hometown-mcp/venv` from `requirements-mcp.txt`
  - services `hometown` (gunicorn, 2 workers, `${HOMETOWN_BIND:-0.0.0.0}:${HOMETOWN_PORT:-8782}`) and `hometown-mcp`, both under `supervise-daemon`
  - cron: `ops/hometown-backup` daily, `ops/hometown-tidy` daily and `ops/hometown-scan` weekly; each installed only if the file is present (`[ -f ]`), so this task needs neither Task 7 nor Task 9
  - adds `hometown` and `hometown-mcp` `/healthz` URLs to `HEALTH_CHECKS` in `/etc/conf.d/ct210-ops`, keeping `.bak`, as where-to-eat's installer does
  - prints the next steps (owner list below)
- `ops/hometown-backup`: a `sqlite3 .backup`, gzipped, to `/var/backups/ct210/<date>/hometown.db.gz`, using `/etc/conf.d/ct210-ops`'s `KEEP_DAYS`, `BACKUP_TARGET` and log.
- `tests/test_installer_static.py`: every `settings.py` env key and every MCP key in `.env.example` has an `add_setting` line; `build.sh` packs `routes/` and `commands/`; no secret is echoed; both init scripts `export` the keys their service reads.

**Consumes:** Task 1 only (`settings.py`, `.env.example`, `requirements-mcp.txt`, `manage.py`).

**Needs merged or decided first:** nothing.

**Needs the owner:** Yes. Everything that touches the host is the owner's:
- Check that ports 8782 and 8783 are free on CT 210, then run the installer there (`pct push` / `pct exec`).
- Add a Cloudflare Tunnel public hostname for the app, and a route for the MCP path.
- Create two Access applications: the app hostname, with the policy for the two editors; and the MCP path, with managed OAuth.
- Set `CF_TEAM_DOMAIN`, `CF_AUD`, `ADMIN_EMAILS`, `CF_ACCESS_AUD` (MCP) and `HOMETOWN_MCP_WRITES` in `/etc/conf.d/hometown`.
- Create the Google Cloud project with billing, enable Places API (New), make a key restricted to that API and set `GOOGLE_PLACES_KEY`. A budget alert at $1 is recommended.
- Add the connector in claude.ai, and link each editor's email to a person in Settings.

- [ ] **Step 1: Write the failing static tests.** - [ ] **Step 2: Run**; FAIL. - [ ] **Step 3: Implement** the scripts, using where-to-eat's and gift-exchange's installers as the pattern. - [ ] **Step 4: Fill the Task 14 slots.** - [ ] **Step 5: Gate** (shellcheck on `installer/*.sh` and `ops/*`; `sh -n dist/setup-hometown.sh`; CI's `alpine` job). - [ ] **Step 6: Commit** (`feat(installer): Alpine installer, services, backup, health checks`).

---

## Review Focus

1. **A place gets sold to a group.** Its verdict changes to *doesn't count* with a reason. Its visits stay, its history shows the old verdict and the date, it moves to Past places, and the decider never offers it. (Task 2, `test_sold_to_group_keeps_visits_and_moves_to_past`; Task 6, `test_ineligible_places_never_appear`)
2. **The first scan finds 2,500 places against a 3,000-call cap shared with closure checks and the inbox.** Pro calls stop exactly at the cap. Unfetched ids stay hidden as `pending` and are filled the next month. Nothing is billed past the cap. (Task 7, `test_cap_stops_pro_calls_and_counts_every_one`; Task 9, `test_cap_leaves_pending_hidden_and_next_run_fills_them`)
3. **A list pasted from a Reddit post**, with numbering, bullets, dashes followed by commentary, blank lines, a repeated name in different case, and a place dismissed months ago. Lines are cleaned, the repeat counts once, and the dismissed place comes back to the inbox instead of being duplicated. (Task 11, `test_messy_paste_is_cleaned_and_deduplicated`, `test_dismissed_candidate_reopens_not_duplicates`)
4. **"Log that I went to X" from claude.ai by an editor not yet linked to a person.** It's a 400 with the fix in plain words, and no visit is saved. A forged `X-Hometown-As` without a key does nothing. (Task 4, `test_me_without_link_is_400_and_saves_nothing`; Task 1, `test_acting_as_needs_a_key_and_a_listed_email`)
5. **A visit logged at 11:30 pm** is dated that day in Concord, not tomorrow in UTC, so "not lately" and the history stay right. (Task 4, `test_late_night_visit_is_dated_today_in_los_angeles`; Task 1, `test_today_is_los_angeles`)

## Tasks that depend only on the scaffold

Tasks **2, 3, 4, 5, 7, 8, 12 and 14** need nothing but Task 1. They can all run at the same time once Task 1 is merged.

The rest wait for specific tasks:
- **Task 6** waits on 4 and 5.
- **Task 9** waits on 7 and 8.
- **Task 11** waits on 7.
- **Task 10** waits on 2, 7 and 8.
- **Task 13** waits on 2, 3, 4, 6, 10 and 11.

That gives three waves after the scaffold:
- 2, 3, 4, 5, 7, 8, 12, 14
- 6, 9, 10, 11
- 13
