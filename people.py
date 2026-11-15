"""People (spec §6.1): the family's short list. A name is unique (folded) and can't be reserved, hold a comma or be all
digits; an optional email links a person to an editor's sign-in (unique, and it must be in ADMIN_EMAILS), which is what
makes "me" work. A person with visits is deactivated, never deleted. Takes a sqlite3.Connection; no Flask here except
ApiError."""
import re

import db
import settings
from common import ApiError, fold

RESERVED = frozenset({"everyone", "adults", "kids"})
GROUPS = ("adult", "kid")
NAME_MAX = 40
FIELDS = ("name", "grp", "email", "sort_order", "active")


def _row(r) -> dict:
    return {"id": r["id"], "name": r["name"], "grp": r["grp"], "email": r["email"], "sort_order": r["sort_order"],
            "active": bool(r["active"])}


def list(con, include_inactive=False):
    sql = "SELECT * FROM person" + ("" if include_inactive else " WHERE active = 1") + " ORDER BY sort_order, id"
    return [_row(r) for r in con.execute(sql)]


def get(con, person_id) -> dict:
    row = con.execute("SELECT * FROM person WHERE id = ?", (person_id,)).fetchone()
    if row is None:
        raise ApiError(404, "No such person.")
    return _row(row)


def _clean_name(con, name, own_id=None) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ApiError(400, "name is required.")
    name = name.strip()
    if len(name) > NAME_MAX:
        raise ApiError(400, f"name must be at most {NAME_MAX} characters.")
    if fold(name) in RESERVED:
        raise ApiError(400, "That name is reserved.")
    if "," in name:
        raise ApiError(400, "A name can't contain a comma.")
    if re.fullmatch(r"[0-9]+", name):
        raise ApiError(400, "A name can't be only digits.")
    for other in con.execute("SELECT id, name FROM person"):
        if other["id"] != own_id and fold(other["name"]) == fold(name):
            raise ApiError(400, "Someone already has that name.")
    return name


def _clean_group(grp) -> str:
    if grp not in GROUPS:
        raise ApiError(400, "grp must be adult or kid.")
    return grp


def _clean_email(con, email, own_id=None):
    if email is None or (isinstance(email, str) and not email.strip()):
        return None
    if not isinstance(email, str):
        raise ApiError(400, "email must be text.")
    email = email.strip().lower()
    if email not in settings.ADMIN_EMAILS:
        raise ApiError(400, "That email isn't an editor.")
    other = con.execute("SELECT id FROM person WHERE lower(email) = ?", (email,)).fetchone()
    if other and other["id"] != own_id:
        raise ApiError(409, "That email is already linked to someone else.")
    return email


def create(con, name, grp, email=None) -> int:
    with db.transaction(con):
        name = _clean_name(con, name)
        grp = _clean_group(grp)
        email = _clean_email(con, email)
        order = con.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM person").fetchone()[0]
        return con.execute("INSERT INTO person (name, grp, email, sort_order) VALUES (?, ?, ?, ?)",
                           (name, grp, email, order)).lastrowid


def update(con, person_id, **fields) -> dict:
    """Change any of name, grp, email (None clears it), sort_order, active. Unknown fields are 400."""
    unknown = set(fields) - set(FIELDS)
    if unknown:
        raise ApiError(400, f"Unknown field: {sorted(unknown)[0]}.")
    with db.transaction(con):
        get(con, person_id)
        sets = {}
        if "name" in fields:
            sets["name"] = _clean_name(con, fields["name"], person_id)
        if "grp" in fields:
            sets["grp"] = _clean_group(fields["grp"])
        if "email" in fields:
            sets["email"] = _clean_email(con, fields["email"], person_id)
        if "sort_order" in fields:
            order = fields["sort_order"]
            if isinstance(order, bool) or not isinstance(order, int) or not 0 <= order <= 10000:
                raise ApiError(400, "sort_order must be a whole number from 0 to 10000.")
            sets["sort_order"] = order
        if "active" in fields:
            if not isinstance(fields["active"], bool):
                raise ApiError(400, "active must be true or false.")
            sets["active"] = int(fields["active"])
        if sets:
            con.execute(f"UPDATE person SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), person_id))
    return get(con, person_id)


def deactivate(con, person_id) -> str:
    """Delete a person with no visits ("deleted"); otherwise keep them, inactive ("deactivated")."""
    with db.transaction(con):
        get(con, person_id)
        used = con.execute("SELECT 1 FROM visit_person WHERE person_id = ? LIMIT 1", (person_id,)).fetchone()
        if used:
            con.execute("UPDATE person SET active = 0 WHERE id = ?", (person_id,))
            return "deactivated"
        con.execute("DELETE FROM person WHERE id = ?", (person_id,))
        return "deleted"
