# Hometown Table

A private, phone-first Flask + SQLite app that answers "where tonight?" from the family's list of small,
independently owned restaurants in eight East Bay towns. Spec: `docs/superpowers/specs/2026-11-14-hometown-table-design.md`.
Plan: `docs/superpowers/plans/2026-11-14-hometown-table.md` (each task is one PR; the plan's task block is the contract
for names, signatures, routes and messages; where the plan and the spec disagree, the plan wins).

## Commands (the gate: run all of it before every commit)

```
python -m pytest -q
HOMETOWN_NO_SKIP=1 python -m pytest -q tests/ui tests/mcp   # needs Playwright + Chromium and requirements-mcp.txt
node --test tests/*.test.mjs
for f in static/js/*.js; do node --check "$f"; done
ruff check
shellcheck -s sh installer/*.sh ops/*                    # nothing to check until Tasks 7 and 14 add files
sh installer/build.sh && sh -n dist/setup-hometown.sh   # from Task 14 on
python3 app.py                                           # dev server on 127.0.0.1:8782 (LAN_EDIT=1 to sign in)
python3 manage.py --help                                 # host chores, one per commands/<name>.py
```

Tools: `pip install -r requirements-dev.txt`; Node 20 or newer; `shellcheck`. For `tests/ui/`:
`pip install playwright && python -m playwright install chromium`. For `tests/mcp/`: `pip install -r requirements-mcp.txt`.
Without them those tests skip locally; CI's `ui` and `mcp` jobs set `HOMETOWN_NO_SKIP=1`, which turns a skip into a
failure. `tests/test_hygiene.py` walks `git ls-files`, so `git add` new files before running it.

CI (`.github/workflows/ci.yml`) runs `pytest` (3.12 and 3.x), `alpine` (Alpine's `py3-*` packages), `lint`,
`shellcheck` (and the installer build), `ui` and `mcp`. Every step uses globs; a glob with no files yet is a no-op that
says so. No task needs to edit the workflow.

## Rules that must keep holding

- Python 3.12. Runtime dependencies are only Alpine packages `py3-flask py3-gunicorn py3-jwt py3-cryptography`, plus
  `tzdata` (ZoneInfo needs it; a bare Alpine has none) and `sqlite` (the backup's `sqlite3`). Everything else is the
  standard library. App modules never import `mcp`, `uvicorn` or anything from the MCP venv.
- No frontend framework, bundler or npm step: native ES modules in `static/js/`, `static/js/package.json` =
  `{"type": "module"}`.
- No network in any test. Google and OSM go through `common.http_json`, which tests replace with `fake_http`; the
  test setup fails any other socket connect outside loopback.
- No personal data in the repo: invented people (Alex, Sam, Jordan, Riley, Casey), invented restaurants
  ("Casa Ejemplo", "Pho Example", "Chain Burger Example"), `example.com` hosts, RFC 5737 addresses, coordinates inside
  the town boxes. Never commit real visits, ratings, lists, names, emails, addresses, the Google key, `CF_AUD`, the team
  domain, the bearer keys, `deploy/deploy.env`, `.env`, `data/` or `tests/hygiene_denylist.txt`.
- Every route except `/healthz` needs a verified editor (`access.py`); routes still carry `@editor_required`. Every
  mutation (non-GET under `/api/`) needs `X-Hometown: 1`, a JSON object body, and a same-host `Origin` (bearer-key
  requests are exempt from Origin): otherwise 400, or 403 for a foreign Origin. A read key on a non-GET is 403
  `{"error": "The read key can only read."}`.
- Bodies via `common.json_body()`, strings via `common.text(data, key, max_len)`; over-length text is 400, never cut.
  Errors are `raise ApiError(status, message, **extra)` -> `{"error": message, **extra}`.
- Everything typed goes through `esc()` before `innerHTML`; `tests/test_frontend.py` scans every module. Markup
  interpolations are one `esc(...)` or `render*(...)` call. No `confirm(`/`alert(`, no inline handlers, styles or
  scripts, no `document.title`.
- CSP `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'`,
  `X-Robots-Tag: noindex, nofollow`, `Referrer-Policy: no-referrer` on everything; `Cache-Control: no-store` on `/api/`.
  `<title>` and the manifest use `APP_NAME` (default `Hometown Table`).
- Schema changes only through idempotent `db.migrate()` with `SCHEMA_VERSION`. Task 1 created the whole v1 schema and
  every setting; no other task changes `db.py`, `settings.py` or `.env.example`. A task that finds it needs a schema
  or settings change stops and asks the owner.
- Timestamps are integer epoch seconds (`common.now()`); dates are `YYYY-MM-DD` in America/Los_Angeles
  (`common.today()`); months are `common.month_key()`. Call them through the module (`common.now()`), never
  `from common import now`, so the `clock` fixture reaches them. The same goes for `common.http_json`.
- Commits: Conventional Commits, one logical change each, author `SDNick484 <SDNick484@users.noreply.github.com>`.
  Docs use OpenRC commands and say `vim`.
- Ports: app 8782 (`HOMETOWN_PORT`), MCP 8783 (`MCP_HTTP_PORT`).

## Layout

```
app.py              Flask object, hooks (sign-in, mutation guard, security headers), error handlers, the shell
                    routes (/, /manifest.json, /sw.js, /robots.txt, /healthz), db(), INIT_HOOKS, init_db(),
                    register_routes() (every routes/ module's bp, sorted by name)
settings.py         every setting, read once at import (ENV_KEYS lists the environment keys); Town, parse_towns
db.py               V1 schema, SCHEMA_VERSION (PRAGMA user_version), connect(), transaction(), migrate()
common.py           ApiError, UpstreamError, json_body, text, int_arg, fold, names_match, km_between, now, today,
                    month_key, town_for, http_json (the only outbound HTTP)
access.py           Identity, identity(), require(), editor_required, who(), me_email(), me_person_id()
health.py           problems(con) for /healthz
manage.py           `python3 manage.py <command>`: runs commands/<name>.py
routes/             one module per feature with `bp`; ping.py (GET/POST /api/ping)
commands/           one module per host chore: NAME, HELP, add_arguments(p), run(args) -> int
static/             index.html (GET / fills __APP_NAME__), style.css (Graphite Red), fonts/ (Archivo + OFL),
                    manifest.json, icon.svg, sw.js (served at /sw.js), robots.txt
static/js/          core.js (esc, fmtDate, ago, stars, townLabel, todayLA, renderError, toast), api.js (get, send),
                    router.js (register, start), main.js (entry), screens.js (one import block per screen task)
tests/              conftest.py (fixtures and factories), test_<module>.py, test_frontend.py, test_hygiene.py,
                    test_slots.py (shared-file shape), frontend.test.mjs, plugins/ (per-task pytest plugins),
                    fixtures/<service>/ (hand-written JSON), ui/ (Playwright), mcp/ (Task 13)
.github/workflows/  ci.yml
deploy/             deploy.env.example (deploy.env is git-ignored)
```

## How to add to it (Task 1 conventions)

- **A route module**: `routes/<name>.py` with `bp = Blueprint("<name>", __name__)`; every view `@editor_required`;
  use `from app import db` for the request's connection (defined before routes are imported). Something to run at
  start: `import app; app.INIT_HOOKS.append(fn)` where `fn(con)` is idempotent and writes inside `db.transaction`
  (two gunicorn workers run it at once).
- **A command**: `commands/<name>.py` with `NAME`, `HELP`, `add_arguments(p)`, `run(args) -> int`. It opens the
  database itself: `con = db.connect(settings.DB_PATH); db.migrate(con)`.
- **A screen**: `static/js/<screen>.js` calls `register(pattern, {label, nav, render})` from `router.js` at import;
  add `import "./<screen>.js";` in your own block of `screens.js`. `nav` orders the header links: Tonight 10,
  Inbox 20, Lists 30, Past places 40, Settings 50; omit it for screens reached by links. `render(ctx)` gets
  `{view, params, query, path, alive, rerender}`; check `ctx.alive()` after every `await`.
- **Access in code**: `access.who()` for `added_by`/`by` (the email, `lan`, or `mcp`); `access.me_person_id(con)` for
  "me" (the Access email, or `X-Hometown-As` sent with a valid key from loopback and listed in `ADMIN_EMAILS`).
- **Tests**: fixtures `con`, `client` (anonymous), `editor` (alex@example.com, mutation headers set), `editor2`
  (sam@example.com), `keyed(kind, as_email=None)` (bearer key from 127.0.0.1, no `X-Hometown` header), `clock`
  (`set(local_iso)`, `set_utc(utc_iso)`, `advance(s)`), `jwt_for(email, **claims)`, `fake_http`
  (`on(substring_or_predicate, dict | "service/file.json" | exception | callable | [one per call])`, `.calls`; the
  newest matching rule answers; an unmatched call fails the test). Import factories from conftest:
  `from conftest import mk_place, mk_person, mk_tag, mk_visit, mk_candidate, mk_list, fixture_json`.
  `tests/conftest.py` is never edited after Task 1: a task's fixtures and helpers go in `tests/plugins/<task>.py`
  (loaded as a plugin; import helpers with `from plugins.<task> import ...`). Recorded responses go in
  `tests/fixtures/<service>/`, hand-written in the real response shapes.
- **tests/mcp/** is deliberately not a package: a `tests/mcp/__init__.py` would make `import mcp` find the test
  directory instead of the MCP library. Test file names there must not repeat a name used elsewhere under `tests/`.
  `pytest tests/mcp` with no test files in it exits 0 and says so (see `pytest_sessionfinish` in conftest).
- **Schema notes**: the plan's columns plus `place.want_since` (epoch seconds when want_to_try was last turned on:
  "want" mode is oldest starred first; set it with want_to_try, clear it when the star goes) and
  `place.closure_dismissed` (the Google status an editor dismissed; the scan doesn't flag that status again, and
  `OPERATIONAL` clears it). `meta.created_at` is when the database was made (health's grace period); `meta.baseline_at`
  is Task 9's. `place_event.by` and `visit.added_by` hold `access.who()`. Visits keep places: `visit.place_id` has no
  cascade, so a place with visits can't be deleted; a place's events, tags and list entries go with it.
- **Shared files**: `CLAUDE.md` (Status, Modules), `README.md` and `static/js/screens.js` hold one slot per task. Edit
  only the inside of your own slot (your Status item's `planned` -> `done`; your Modules heading's `Not built yet.`
  line; your README heading's `_Lands with Task N._` line; your screens.js block's `// (not built yet)` line). Never
  edit a heading or marker line, another slot, or the end of a file. `tests/test_slots.py` checks the shape.

## Status

- Task 1 (Scaffold, schema, access, gate and shared files): done

- Task 2 (Places, ownership, standing and Past places): done

- Task 3 (People, tags, export and Settings): planned

- Task 4 (Visits and ratings): planned

- Task 5 (Opening-hours parser): planned

- Task 6 (The decider, "Tonight"): planned

- Task 7 (Google Places client and the monthly cap): planned

- Task 8 (OpenStreetMap client): planned

- Task 9 (The weekly scan): planned

- Task 10 (Inbox, flags and Add place): planned

- Task 11 (Curated lists): planned

- Task 12 (Import from Where to Eat): planned

- Task 13 (MCP server, stdio and remote): planned

- Task 14 (Installer, services, backups and health checks): planned

## Modules

### Task 2: Places, ownership, standing and Past places
- `places.py` (route-free, takes a `Connection`): `create`, `get`, `update`, `set_ownership`, `set_standing`, `delete`,
  `search`, `past`, `set_tags`, `set_want_to_try`; the plan's signatures. `routes/places.py` is the thin layer.
- Ownership and standing change only through `set_ownership` / `set_standing` (a reason is required: 400 "Give a
  reason."; `on` is an optional past date, default today). Each real change writes a `place_event` and bumps `version`;
  `set_standing` also stamps `standing_since` and clears `closure_flag`. Setting the value a place already has writes no
  event (ownership still updates its reason). `update` refuses `ownership`, `standing` and `ownership_reason` (400).
- A place creation writes no `place_event`: history is changes. `past()` therefore dates a place that arrived closed or
  not counting by its own `standing_since` / `created_at` and `ownership_reason`.
- `get()` returns every column plus `tags`, `lists`, `history` (newest first: `{kind, old, new, reason, on, by}`),
  `last_visit` and `visit_count`. Lists are `{"places": [...]}`: `GET /api/places?q=&past=1&limit=` (folded over name,
  cuisine, town, address, notes, every word must match) and `GET /api/past` (adds `kind` "closed"|"ownership", `since`,
  `reason`, `visit_count`).
- Bodies: `PATCH /api/places/<id>` needs `version`; `POST .../ownership|standing` `{value, reason, on?}`;
  `PUT .../tags` `{tag_ids}`; `PUT .../want` `{on}` (`want_to_try` also accepted); `DELETE` answers 204 with no body, which
  `api.js` reports as an `ApiError` with status 204 (place.js treats that as success).
- Duplicate guard: 150 m and `common.names_match`, 409 `{duplicate_of}`, `add_anyway` overrides. A place's `google_place_id`
  collision is 409.
- Screens: `place.js` (`#/p/:id`, exports label maps and `eventTitle`), `place-edit.js` (`#/p/:id/edit`; tag boxes appear
  only if `GET /api/tags` answers), `past.js` (`#/past`, nav 40, exports `pastLine`).

### Task 3: People, tags, export and Settings
Not built yet.

### Task 4: Visits and ratings
Not built yet.

### Task 5: Opening-hours parser
Not built yet.

### Task 6: The decider ("Tonight")
Not built yet.

### Task 7: Google Places client and the monthly cap
Not built yet.

### Task 8: OpenStreetMap client
Not built yet.

### Task 9: The weekly scan
Not built yet.

### Task 10: Inbox, flags and Add place
Not built yet.

### Task 11: Curated lists
Not built yet.

### Task 12: Import from Where to Eat
Not built yet.

### Task 13: MCP server (stdio and remote)
Not built yet.

### Task 14: Installer, services, backups and health checks
Not built yet.
