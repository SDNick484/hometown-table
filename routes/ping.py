"""GET and POST /api/ping: {"ok": true} for any editor. The access and mutation-guard tests use it; the MCP service
can use it to check its key."""
from flask import Blueprint, jsonify

from access import editor_required

bp = Blueprint("ping", __name__)


@bp.route("/api/ping", methods=["GET", "POST"])
@editor_required
def ping():
    return jsonify(ok=True)
