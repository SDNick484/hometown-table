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
Every place has an **ownership verdict** and a **standing**. The verdict is yours, never the app's: *Counts* (small and
independently owned, even a local family with three locations), *Not sure*, or *Doesn't count* (a chain or a
restaurant group). Standing is *Open*, *Temporarily closed* or *Closed*. Open a place to change either: you give a reason
and, if it happened earlier, the date, and the change is written to the place's history with who made it. A place sold
to a group keeps its visits; its history shows "Counted → Doesn't count" and the date.

**Past places** (the header link) lists every place that is closed or no longer counts, newest change first, each with a
line like "Closed Mar 2027 · 14 visits" or "Stopped counting Jun 2027 (sold to a group) · 6 visits". Open one to change it
back.

Places are never deleted once they have visits ("This place has visits. Mark it closed or doesn't count instead.").
A place made by mistake, with no visits, has a Delete button that asks first. Saving the edit form while someone else
has saved first stops with "Someone else just changed this place. Reload to see it." and changes nothing. Adding a place
close to one with the same name shows the existing one and needs an explicit "Add anyway".

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
_Lands with Task 12._

## MCP
_Lands with Task 13._

## Install
_Lands with Task 14._
