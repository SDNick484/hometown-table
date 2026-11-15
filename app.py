"""The Flask object, its hooks, the shell routes and /healthz. Every other route lives in a module of the `routes`
package, whose `bp` (a flask.Blueprint) is registered here, modules sorted by name. Route-free logic lives in
top-level modules that take a sqlite3.Connection.

Hooks, in order, on every request:
  1. sign-in (access.require): every path except /healthz needs an editor; 401, or 403 (unlisted email, read key on a
     write)
  2. the mutation guard: every non-GET under /api/ needs X-Hometown: 1 and a JSON object body (else 400), and an Origin
     naming this host (else 403; bearer-key requests are exempt from the Origin check)
  3. after: the CSP and the other security headers on every response; Cache-Control: no-store under /api/

A routes module that needs something done at start (a seed) appends a function taking the connection to INIT_HOOKS
when it is imported; init_db() runs them after migrate(), once every routes module has been imported."""
import html
import importlib
import json
import mimetypes
import os
import pkgutil
import sys
from collections.abc import Callable
from sqlite3 import Connection
from urllib.parse import urlsplit

if __name__ == "__main__":
    sys.modules.setdefault("app", sys.modules[__name__])   # `python3 app.py`: routes modules' `import app` finds this one

from flask import Flask, g, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

import access
import db as database
import health
import settings
from common import ApiError

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
mimetypes.add_type("text/javascript", ".js")   # ES modules need a JavaScript type, whatever the host's tables say
mimetypes.add_type("font/woff2", ".woff2")

app = Flask(__name__, static_folder=STATIC, static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024

INIT_HOOKS: list[Callable[[Connection], None]] = []


def db() -> Connection:
    """This request's connection, opened on first use and closed at teardown."""
    if "db" not in g:
        g.db = database.connect(settings.DB_PATH)
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    con = g.pop("db", None)
    if con is not None:
        con.close()


def init_db():
    """Create DATA_DIR, bring the database to the current schema, then run every INIT_HOOKS function. Runs at import
    (so at every start, in each gunicorn worker): hooks must be idempotent and do their writes in db.transaction."""
    os.makedirs(settings.DATA_DIR, exist_ok=True)
    con = database.connect(settings.DB_PATH)
    try:
        database.migrate(con)
        for hook in INIT_HOOKS:
            hook(con)
    finally:
        con.close()


# ---------------------------------------------------------------- hooks
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
PUBLIC_PATHS = ("/healthz",)


@app.before_request
def require_editor():
    if request.path not in PUBLIC_PATHS:
        access.require()


@app.before_request
def guard_mutations():
    """X-Hometown: 1 is a header a cross-site page can't send without a preflight we never allow. The scheme of the
    Origin isn't compared: behind the tunnel the browser says https while the app sees http."""
    if not request.path.startswith("/api/") or request.method in access.READ_METHODS:
        return
    if request.headers.get("X-Hometown") != "1" or not isinstance(request.get_json(silent=True), dict):
        raise ApiError(400, "Bad request.")
    ident = access.identity()
    if ident is not None and ident.kind.startswith("key_"):
        return
    if urlsplit(request.headers.get("Origin") or "").netloc.lower() != request.host.lower():
        raise ApiError(403, "Cross-origin request blocked.")


@app.after_request
def security_headers(resp):
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.errorhandler(ApiError)
def api_error(e):
    return jsonify({"error": e.message, **e.extra}), e.status


@app.errorhandler(HTTPException)
def http_error(e):
    return jsonify(error=e.name), e.code


# ---------------------------------------------------------------- the shell
@app.get("/")
def index():
    """The single page, with APP_NAME (escaped) as its title and header. Everything after this is hash-routed by
    static/js/router.js."""
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
        page = f.read().replace("__APP_NAME__", html.escape(settings.APP_NAME, quote=True))
    resp = app.response_class(page, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/manifest.json")
def manifest():
    """static/manifest.json with name and short_name set to APP_NAME."""
    with open(os.path.join(STATIC, "manifest.json"), encoding="utf-8") as f:
        data = {**json.load(f), "name": settings.APP_NAME, "short_name": settings.APP_NAME}
    return app.response_class(json.dumps(data), mimetype="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    """Served from the root so its scope is the whole app; never cached, so a new version is picked up."""
    return send_from_directory(STATIC, "sw.js", mimetype="text/javascript", max_age=0)


@app.get("/robots.txt")
def robots():
    return send_from_directory(STATIC, "robots.txt", mimetype="text/plain")


@app.get("/healthz")
def healthz():
    found = health.problems(db())
    if found:
        return jsonify(ok=False, problems=found), 503
    return jsonify(ok=True)


# ---------------------------------------------------------------- the routes package
def register_routes(flask_app: Flask, package: str = "routes") -> list[str]:
    """Import every module of `package`, sorted by name, and register each one's `bp`. Returns the module names."""
    pkg = importlib.import_module(package)
    names = sorted(m.name for m in pkgutil.iter_modules(pkg.__path__))
    for name in names:
        module = importlib.import_module(f"{package}.{name}")
        bp = getattr(module, "bp", None)
        if bp is not None:
            flask_app.register_blueprint(bp)
    return names


register_routes(app)
init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=settings.PORT)
