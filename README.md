# Hometown Table

A private, phone-first app that answers "where tonight?" from a family's own list of small, independently owned
restaurants in a handful of towns: places they like but haven't been to in a while, places on the want-to-try list,
and places that are new. It keeps every visit and what each person thought, finds new local places with a weekly
scan, and answers questions from claude.ai through an MCP. Flask and SQLite, no build step, behind Cloudflare Access.

Work in progress, built task by task: the design is in `docs/superpowers/specs/` and the build plan in
`docs/superpowers/plans/`.

## Development

```
python -m venv .venv && . .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
node --test tests/*.test.mjs
ruff check
LAN_EDIT=1 python3 app.py     # http://127.0.0.1:8782/ (LAN_EDIT=1 lets your own machine in without Access)
```

Every setting is listed, with what it does, in `.env.example`. The UI smoke tests need
`pip install playwright && python -m playwright install chromium`; without them they skip.

## Places
_Lands with Task 2._

## Settings
_Lands with Task 3._

## Visits
_Lands with Task 4._

## Where tonight
_Lands with Task 6._

## Discovery
_Lands with Task 9._

## Inbox
_Lands with Task 10._

## Lists
_Lands with Task 11._

## Import from Where to Eat
One time, to seed the inbox from the list you already keep in Where to Eat. In Where to Eat choose **More, Download,
JSON**, copy the file to the host, and run as the `hometown` user:

```
python3 /opt/hometown/manage.py import-where-to-eat export.json
```

Open places in the eight towns (by map pin, or by the town named in their area) land in the Inbox as new candidates.
Closed places, places elsewhere, and places already here (same Where to Eat id, or the same name within 150 m) are
skipped, and the command prints how many of each. Running it again creates nothing. Dishes, photos and notes stay in
Where to Eat.

## MCP
_Lands with Task 13._

## Install
_Lands with Task 14._
