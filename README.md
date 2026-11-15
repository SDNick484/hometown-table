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
Open **Settings** from the header.

- **People.** Add each person who eats out with you, as an adult or a kid. Names must be different, can't be
  `everyone`, `adults` or `kids`, can't hold a comma and can't be only digits. An editor's email on a person links
  them to that sign-in, so the visit form starts with them ticked and "me" means them. The email has to be one of
  `ADMIN_EMAILS`. Someone who has been on a visit can't be deleted; Remove makes them inactive, and Reactivate
  brings them back.
- **Tags.** Six start out: good for groups, private room, special occasion, takes reservations, kid-friendly and show off
  to guests. Rename, reorder, add or delete them. A ticked tag is an occasion tag: the "Occasion" mode on Tonight picks
  places that have one. Deleting a tag says how many places it is on first.
- **Status.** The towns, rest days, whether the Google key is set (never the key itself), this month's Google calls
  and the last weekly scan.
- **Export.** `/api/export.json` holds everything; `/api/export.csv` has one row per person on each visit
  (place, town, ownership, date, occasion, person, rating, their note).

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
_Lands with Task 12._

## MCP
_Lands with Task 13._

## Install
_Lands with Task 14._
