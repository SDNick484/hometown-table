"""Tags and the occasion flag (spec §6.4). Occasion mode uses the flag, never the label, so renaming or deleting a tag
can't break it silently. Labels are unique (folded) and at most 40 characters. This module defines `list`, so it never
calls the builtin."""
import builtins

import db
from common import ApiError, fold

SEED = (("good for groups", True), ("private room", True), ("special occasion", True),
        ("takes reservations", False), ("kid-friendly", False), ("show off to guests", False))
LABEL_MAX = 40


def _row(r) -> dict:
    return {"id": r["id"], "label": r["label"], "occasion": bool(r["occasion"]), "sort_order": r["sort_order"], "places": r["places"]}


_SELECT = ("SELECT t.*, (SELECT COUNT(*) FROM place_tag pt WHERE pt.tag_id = t.id) AS places FROM tag t")


def list(con):
    return [_row(r) for r in con.execute(f"{_SELECT} ORDER BY t.sort_order, t.id")]


def get(con, tag_id) -> dict:
    row = con.execute(f"{_SELECT} WHERE t.id = ?", (tag_id,)).fetchone()
    if row is None:
        raise ApiError(404, "No such tag.")
    return _row(row)


def seed(con):
    """Add the six starting tags when there are none. Safe to run from every worker at once."""
    with db.transaction(con):
        if con.execute("SELECT 1 FROM tag LIMIT 1").fetchone():
            return
        for order, (label, occasion) in enumerate(SEED):
            con.execute("INSERT INTO tag (label, occasion, sort_order) VALUES (?, ?, ?)", (label, int(occasion), order))


def _clean_label(con, label, own_id=None) -> str:
    if not isinstance(label, str) or not label.strip():
        raise ApiError(400, "label is required.")
    label = label.strip()
    if len(label) > LABEL_MAX:
        raise ApiError(400, f"label must be at most {LABEL_MAX} characters.")
    for other in con.execute("SELECT id, label FROM tag"):
        if other["id"] != own_id and fold(other["label"]) == fold(label):
            raise ApiError(400, "There is already a tag with that label.")
    return label


def _clean_occasion(occasion) -> int:
    if not isinstance(occasion, bool):
        raise ApiError(400, "occasion must be true or false.")
    return int(occasion)


def create(con, label, occasion=False) -> int:
    with db.transaction(con):
        label = _clean_label(con, label)
        flag = _clean_occasion(occasion)
        order = con.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM tag").fetchone()[0]
        return con.execute("INSERT INTO tag (label, occasion, sort_order) VALUES (?, ?, ?)", (label, flag, order)).lastrowid


def rename(con, tag_id, label) -> dict:
    return update(con, tag_id, label=label)


def update(con, tag_id, label=None, occasion=None) -> dict:
    """Change the label and/or the occasion flag; None leaves a field alone."""
    with db.transaction(con):
        get(con, tag_id)
        if label is not None:
            con.execute("UPDATE tag SET label = ? WHERE id = ?", (_clean_label(con, label, tag_id), tag_id))
        if occasion is not None:
            con.execute("UPDATE tag SET occasion = ? WHERE id = ?", (_clean_occasion(occasion), tag_id))
    return get(con, tag_id)


def reorder(con, ids):
    """Put the tags in this order. `ids` must name every tag exactly once."""
    with db.transaction(con):
        have = sorted(r[0] for r in con.execute("SELECT id FROM tag"))
        if not isinstance(ids, (tuple, builtins.list)) or sorted(i for i in ids if isinstance(i, int) and not isinstance(i, bool)) != have \
                or len(ids) != len(have):
            raise ApiError(400, "ids must list every tag exactly once.")
        for order, tag_id in enumerate(ids):
            con.execute("UPDATE tag SET sort_order = ? WHERE id = ?", (order, tag_id))


def delete(con, tag_id) -> int:
    """Delete a tag; returns how many places it was on."""
    with db.transaction(con):
        count = get(con, tag_id)["places"]
        con.execute("DELETE FROM tag WHERE id = ?", (tag_id,))
        return count
