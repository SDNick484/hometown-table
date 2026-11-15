"""Where tonight (spec §7): GET /api/decide runs decide.decide() on the query string; GET /api/decide/choices lists
what the home screen's filter row offers."""
from flask import Blueprint, jsonify, request

import access
import decide
from access import editor_required
from app import db

bp = Blueprint("decide", __name__)


@bp.get("/api/decide")
@editor_required
def run():
    params = decide.parse_params(request.args)
    con = db()
    me = access.me_person_id(con) if params["audience"].casefold() == "me" else None
    return jsonify(decide.decide(con, params, me_person_id=me))


@bp.get("/api/decide/choices")
@editor_required
def choices():
    return jsonify(decide.choices(db()))
