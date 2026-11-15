"""The Flask app: the mutation guard, security headers, error shape, the routes package, the shell, manage.py."""
import textwrap

from flask import Flask

import app as hometown
import db
import manage
import settings
from common import ApiError

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")


def test_mutation_without_header_is_400_and_foreign_origin_403(editor):
    assert editor.post("/api/ping", json={}).status_code == 200
    r = editor.post("/api/ping", json={}, headers={"X-Hometown": "0"})
    assert r.status_code == 400 and r.get_json()["error"]
    assert editor.post("/api/ping", json={}, headers={"Origin": "https://evil.example.com"}).status_code == 403
    assert editor.post("/api/ping", json=[1]).status_code == 400                     # not an object
    assert editor.post("/api/ping", data="{}").status_code == 400                    # not sent as JSON
    assert editor.post("/api/ping", data="{", content_type="application/json").status_code == 400


def test_mutation_guard_details(client, jwt_for):
    token = jwt_for("alex@example.com")
    r = client.post("/api/ping", json={}, headers={"Cf-Access-Jwt-Assertion": token})
    assert r.status_code == 400                                                       # no X-Hometown
    r = client.post("/api/ping", json={}, headers={"Cf-Access-Jwt-Assertion": token, "X-Hometown": "1"})
    assert r.status_code == 403                                                       # no Origin
    r = client.post("/api/ping", json={}, headers={"Cf-Access-Jwt-Assertion": token, "X-Hometown": "1",
                                                     "Origin": "https://localhost"})
    assert r.status_code == 200      # behind the tunnel the browser says https while the app sees http: host decides


def test_security_headers_and_csp_on_every_response(editor, client):
    for c, path in ((editor, "/"), (editor, "/api/ping"), (editor, "/static/style.css"), (editor, "/manifest.json"),
                    (editor, "/sw.js"), (editor, "/api/nope"), (client, "/api/ping"), (client, "/healthz")):
        r = c.get(path)
        assert r.headers["Content-Security-Policy"] == CSP, path
        assert r.headers["X-Robots-Tag"] == "noindex, nofollow", path
        assert r.headers["Referrer-Policy"] == "no-referrer", path
        assert (r.headers.get("Cache-Control") == "no-store") == path.startswith("/api/"), path


def test_errors_are_json(editor):
    r = editor.get("/api/nope")
    assert r.status_code == 404 and r.get_json() == {"error": "Not Found"}
    assert editor.put("/api/ping", json={}).status_code == 405


def test_api_error_extra_fields_reach_the_response():
    with hometown.app.test_request_context("/api/places"):
        body, status = hometown.api_error(ApiError(409, "Already there.", duplicate_of=7))
        assert status == 409 and body.get_json() == {"error": "Already there.", "duplicate_of": 7}


def test_routes_package_modules_are_registered(tmp_path, monkeypatch):
    assert "/api/ping" in {r.rule for r in hometown.app.url_map.iter_rules()}
    pkg = tmp_path / "fake_routes"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "zeta.py").write_text(textwrap.dedent("""
        from flask import Blueprint
        bp = Blueprint("zeta", __name__)
        @bp.get("/api/zeta")
        def zeta():
            return {"ok": True}
    """))
    (pkg / "alpha.py").write_text(textwrap.dedent("""
        from flask import Blueprint
        bp = Blueprint("alpha", __name__)
        @bp.get("/api/alpha")
        def alpha():
            return {"ok": True}
    """))
    (pkg / "helpers.py").write_text("X = 1\n")                                 # no bp: imported, not mounted
    monkeypatch.syspath_prepend(str(tmp_path))
    fresh = Flask("fresh")
    assert hometown.register_routes(fresh, "fake_routes") == ["alpha", "helpers", "zeta"]
    assert list(fresh.blueprints) == ["alpha", "zeta"]                         # sorted by module name
    assert {"/api/alpha", "/api/zeta"} <= {r.rule for r in fresh.url_map.iter_rules()}


def test_init_hooks_run_after_migrate(monkeypatch):
    seen = []
    monkeypatch.setattr(hometown, "INIT_HOOKS", [lambda con: seen.append(con.execute("PRAGMA user_version").fetchone()[0])])
    hometown.init_db()
    assert seen == [db.SCHEMA_VERSION] == [2]


def test_index_uses_app_name(editor, monkeypatch):
    html = editor.get("/").get_data(as_text=True)
    assert "<title>Hometown Table</title>" in html and "__APP_NAME__" not in html
    monkeypatch.setattr(settings, "APP_NAME", 'Our <b>"Table"</b> & co')
    html = editor.get("/").get_data(as_text=True)
    assert "<title>Our &lt;b&gt;&quot;Table&quot;&lt;/b&gt; &amp; co</title>" in html and "<b>" not in html
    m = editor.get("/manifest.json").get_json()
    assert m["name"] == m["short_name"] == 'Our <b>"Table"</b> & co'


def test_manifest_and_service_worker(editor):
    r = editor.get("/manifest.json")
    assert r.mimetype == "application/manifest+json"
    m = r.get_json()
    assert m["name"] == "Hometown Table" and m["start_url"] == "/" and m["display"] == "standalone"
    assert {"192x192", "512x512"} <= {i["sizes"] for i in m["icons"]}
    r = editor.get("/sw.js")
    assert r.status_code == 200 and r.mimetype == "text/javascript" and "no-cache" in r.headers.get("Cache-Control", "")
    assert editor.get("/robots.txt").get_data(as_text=True).startswith("User-agent: *")


def test_static_files_are_served_with_the_right_types(editor):
    assert editor.get("/static/js/main.js").mimetype == "text/javascript"
    assert editor.get("/static/fonts/archivo-latin-wght-normal.woff2").mimetype == "font/woff2"


def test_manage_runs_commands_package_modules(tmp_path, monkeypatch, capsys):
    pkg = tmp_path / "fake_commands"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "say_hello.py").write_text(textwrap.dedent("""
        NAME = "say-hello"
        HELP = "print a greeting"
        def add_arguments(p):
            p.add_argument("who")
        def run(args):
            print("hello " + args.who)
            return 3
    """))
    monkeypatch.syspath_prepend(str(tmp_path))
    assert manage.main(["say-hello", "Riley"], package="fake_commands") == 3
    assert capsys.readouterr().out == "hello Riley\n"
    assert manage.main(["--help"], package="fake_commands") == 0
    assert "say-hello" in capsys.readouterr().out
    assert manage.main([], package="commands") == 2                           # no command named: usage, exit 2
