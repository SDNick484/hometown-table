"""Static checks on the installer, the two OpenRC services and the backup. Nothing here installs anything: the
scripts are read, syntax-checked, and the two pieces that are safe to run (the HEALTH_CHECKS editor and the backup)
run against temporary files."""
import base64
import io
import os
import re
import shutil
import sqlite3
import subprocess
import tarfile

import pytest

import settings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEAD = os.path.join(ROOT, "installer", "setup-head.sh")
BUILD = os.path.join(ROOT, "installer", "build.sh")
INIT_APP = os.path.join(ROOT, "installer", "init-hometown")
INIT_MCP = os.path.join(ROOT, "installer", "init-hometown-mcp")
BACKUP = os.path.join(ROOT, "ops", "hometown-backup")
MCP_KEYS = ("HOMETOWN_MCP_WRITES", "MCP_HTTP_PORT", "MCP_HTTP_PATH", "MCP_PUBLIC_HOSTS", "CF_ACCESS_TEAM_DOMAIN",
            "CF_ACCESS_AUD", "MCP_ALLOWED_EMAILS", "HOMETOWN_URL")
SECRET_KEYS = ("HOMETOWN_READ_KEY", "HOMETOWN_API_KEY", "GOOGLE_PLACES_KEY", "CF_AUD", "CF_ACCESS_AUD")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def env_example_keys():
    return re.findall(r"^([A-Z][A-Z0-9_]*)=", read(os.path.join(ROOT, ".env.example")), re.M)


def sh_n(path):
    return subprocess.run(["sh", "-n", path], capture_output=True, text=True)


def test_every_setting_has_an_add_setting_line():
    head = read(HEAD)
    added = set(re.findall(r'^add_setting "\$CONF" ([A-Z][A-Z0-9_]*) ', head, re.M))
    wanted = set(settings.ENV_KEYS) | set(env_example_keys())
    assert wanted - added == set(), "settings the installer never writes to the config"
    assert set(MCP_KEYS) <= added


def test_installer_scripts_parse():
    for path in (HEAD, BUILD, INIT_APP, INIT_MCP, BACKUP):
        r = sh_n(path)
        assert r.returncode == 0, f"{path}: {r.stderr}"


def test_head_installs_the_packages_and_keeps_data():
    head = read(HEAD)
    apk = re.search(r"apk add --no-cache ([^>]+?) >", head).group(1).split()
    assert {"py3-flask", "py3-gunicorn", "py3-jwt", "py3-cryptography", "sqlite", "python3", "py3-virtualenv",
            "tzdata"} <= set(apk)
    uninstall = head[head.index('"--uninstall"'):head.index("# an existing install keeps its ports")]
    assert "rm -rf" in uninstall and "$APP_DIR" in uninstall
    for kept in ('"$DATA_DIR"', '"$CONF"', "/var/backups"):
        assert not re.search(r"rm\s+-\w*\s+[^\n]*" + re.escape(kept), uninstall), f"--uninstall must keep {kept}"
    assert "/var/lib/hometown" in head and "/etc/conf.d/hometown" in head and "MCP_DIR=/opt/hometown-mcp" in head and "MCP_VENV=$MCP_DIR/venv" in head


def test_crons_are_installed_only_when_the_file_exists():
    head = read(HEAD)
    crons = re.search(r'^CRONS="([^"]+)"', head, re.M).group(1).split()
    assert crons == ["daily/hometown-backup", "daily/hometown-tidy", "weekly/hometown-scan"]
    assert re.search(r'if \[ -f "\$APP_DIR/ops/\$job" \]; then install -m 755 .*"/etc/periodic/\$c"', head)


def test_services_use_supervise_daemon_and_the_right_ports():
    app, mcp = read(INIT_APP), read(INIT_MCP)
    for text in (app, mcp):
        assert text.startswith("#!/sbin/openrc-run") and 'supervisor="supervise-daemon"' in text
    assert "--workers 2" in app and "${HOMETOWN_BIND:-0.0.0.0}:${HOMETOWN_PORT:-8782}" in app and "app:app" in app
    assert "mcp_http.py" in mcp and "/opt/hometown-mcp/venv/bin/python" in mcp


def test_init_scripts_export_the_keys_their_service_reads():
    def exported(text):
        return set(" ".join(re.findall(r"^export (.+)$", text, re.M)).split())
    assert set(settings.ENV_KEYS) <= exported(read(INIT_APP))
    assert set(MCP_KEYS) | {"HOMETOWN_READ_KEY", "HOMETOWN_API_KEY"} <= exported(read(INIT_MCP))


def test_mcp_service_drops_the_write_key_unless_writes_are_on():
    mcp = read(INIT_MCP)           # at level none the service refuses to start if it can see the write key
    assert re.search(r"unset HOMETOWN_API_KEY", mcp) and re.search(r"log\|all", mcp)


def test_no_secret_is_echoed():
    for path in (HEAD, INIT_APP, INIT_MCP, BACKUP):
        for n, line in enumerate(read(path).splitlines(), 1):
            if re.match(r"\s*(echo|printf|cat\b)", line) or "| tee" in line:
                assert not re.search(r"\$\{?\w*(KEY|SECRET|TOKEN)\w*", line), f"{path}:{n} prints a secret: {line}"
                assert not re.search(r"conf_val\s+\w*(KEY|AUD)", line), f"{path}:{n}"
    head = read(HEAD)
    assert not re.search(r"conf_val\s+HOMETOWN_\w*KEY", head)
    assert re.search(r'add_setting "\$CONF" HOMETOWN_READ_KEY "\$\(genkey\)"', head)
    assert re.search(r'add_setting "\$CONF" HOMETOWN_API_KEY "\$\(genkey\)"', head)


def test_config_is_root_only_because_it_holds_keys():
    assert re.search(r'chmod 600 "\$CONF"', read(HEAD))


def test_health_checks_get_both_healthz_urls():
    head = read(HEAD)
    assert re.search(r"Hometown Table\|http://127\.0\.0\.1:\$PORT/healthz\|hometown\b", head)
    assert re.search(r"Hometown MCP\|http://127\.0\.0\.1:\$MCP_PORT/healthz\|hometown-mcp", head)
    assert '"$OPS_CONF.bak"' in head


def _health_fn():
    head = read(HEAD)
    return head[head.index("# --- add_health_check begin"):head.index("# --- add_health_check end")]


def _add_health(tmp_path, conf_text, line, service):
    conf = tmp_path / "ct210-ops"
    if conf_text is not None:
        conf.write_text(conf_text)
    script = f'OPS_CONF={conf}\n{_health_fn()}\nadd_health_check "{line}" {service}\necho "added=${{HC_ADDED:-}} manual=${{HC_MANUAL:-}}"\n'
    out = subprocess.run(["sh", "-c", script], capture_output=True, text=True, check=True).stdout
    return conf, out


@pytest.mark.parametrize("body", [
    'KEEP_DAYS=14\nHEALTH_CHECKS="Other|http://127.0.0.1:9/healthz|other\nThird|http://127.0.0.1:8/|third"\nX=1\n',
    'HEALTH_CHECKS="Other|http://127.0.0.1:9/healthz|other"\nX=1\n',
    'HEALTH_CHECKS="\nOther|http://127.0.0.1:9/healthz|other\n"\nX=1\n',
    "KEEP_DAYS=14\n",
])
def test_health_check_line_is_added_once_and_the_file_still_parses(tmp_path, body):
    line = "Hometown Table|http://127.0.0.1:8782/healthz|hometown"
    conf, out = _add_health(tmp_path, body, line, "hometown")
    assert "added= hometown" in out
    text = conf.read_text()
    assert text.count(line) == 1
    assert subprocess.run(["sh", "-n", str(conf)]).returncode == 0
    shown = subprocess.run(["sh", "-c", f'. {conf}; echo "$HEALTH_CHECKS"'], capture_output=True, text=True).stdout
    assert line in shown.splitlines()
    if "Other|" in body:
        assert "Other|http://127.0.0.1:9/healthz|other" in shown and conf.with_name("ct210-ops.bak").exists()
    again, out2 = _add_health(tmp_path, None, line, "hometown")
    assert again.read_text() == text and "added= " in out2 and "added= hometown" not in out2


def test_health_check_for_a_longer_service_name_still_added(tmp_path):
    body = 'HEALTH_CHECKS="Hometown Table|http://127.0.0.1:8782/healthz|hometown"\n'
    conf, out = _add_health(tmp_path, body, "Hometown MCP|http://127.0.0.1:8783/healthz|hometown-mcp", "hometown-mcp")
    assert "|hometown-mcp" in conf.read_text() and "added= hometown-mcp" in out


def test_missing_ops_conf_is_left_alone(tmp_path):
    conf, out = _add_health(tmp_path, None, "A|http://127.0.0.1:1/|a", "a")
    assert not conf.exists()


def test_build_packs_the_app_and_the_init_scripts(tmp_path):
    r = subprocess.run(["sh", BUILD], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = os.path.join(ROOT, "dist", "setup-hometown.sh")
    assert os.path.isfile(out) and sh_n(out).returncode == 0
    text = read(out)
    payload = text.split("\n__PAYLOAD_BELOW__\n", 1)[1]
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(payload)), mode="r:gz") as tar:
        names = tar.getnames()
    assert all(n == "hometown" or n.startswith("hometown/") for n in names)
    rel = {n[len("hometown/"):] for n in names}
    for want in ("app.py", "manage.py", "settings.py", "routes/ping.py", "commands", "static/index.html",
                 "ops/hometown-backup", "requirements-mcp.txt", "installer/init-hometown",
                 "installer/init-hometown-mcp"):
        assert want in rel, want
    assert not [n for n in rel if n.startswith(("tests", ".git", ".env", "data", ".venv")) or "__pycache__" in n]
    build = read(BUILD)
    assert "routes" in build and "commands" in build and "static" in build and "ops" in build


def test_dist_is_git_ignored():
    r = subprocess.run(["git", "check-ignore", "dist/setup-hometown.sh"], cwd=ROOT, capture_output=True)
    assert r.returncode == 0


@pytest.mark.skipif(shutil.which("sqlite3") is None or shutil.which("gzip") is None, reason="needs sqlite3 and gzip")
def test_backup_writes_a_gzipped_consistent_copy_and_prunes(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    con = sqlite3.connect(data / "hometown.db")
    con.execute("CREATE TABLE t (x)")
    con.execute("INSERT INTO t VALUES ('kept')")
    con.commit()
    con.close()
    local = tmp_path / "backups"
    old = local / "2000-01-01"
    old.mkdir(parents=True)
    os.utime(old, (1, 1))
    ops = tmp_path / "ct210-ops"
    ops.write_text(f"KEEP_DAYS=3\nLOCAL_DIR={local}\n")
    env = dict(os.environ, OPS_CONF=str(ops), DATA_DIR=str(data), LOG=str(tmp_path / "ops.log"))
    r = subprocess.run(["sh", BACKUP], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    days = [p for p in local.iterdir() if p.name != "2000-01-01"]
    assert len(days) == 1 and not old.exists()
    gz = days[0] / "hometown.db.gz"
    assert gz.is_file()
    restored = tmp_path / "restored.db"
    subprocess.run(f"gzip -dc {gz} > {restored}", shell=True, check=True)
    assert sqlite3.connect(restored).execute("SELECT x FROM t").fetchone() == ("kept",)
    assert "ok" in (tmp_path / "ops.log").read_text()


def test_backup_without_a_database_is_not_an_error(tmp_path):
    env = dict(os.environ, OPS_CONF=str(tmp_path / "none"), DATA_DIR=str(tmp_path / "empty"),
               LOCAL_DIR=str(tmp_path / "b"), LOG=str(tmp_path / "ops.log"))
    assert subprocess.run(["sh", BACKUP], env=env).returncode == 0
