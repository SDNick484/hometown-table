"""/healthz and health.problems (spec §12)."""
import health
import settings


def add_run(con, started, finished=None, error=None):
    con.execute("INSERT INTO scan_run (started_at, finished_at, error) VALUES (?, ?, ?)", (started, finished, error))


def test_healthz_ok_without_key_and_503_after_a_failed_scan(client, con, monkeypatch):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.get_json() == {"ok": True}
    add_run(con, 100, 200, error="Google answered 500")
    assert client.get("/healthz").status_code == 200                 # no key: scanning is off, nothing to report
    monkeypatch.setattr(settings, "GOOGLE_PLACES_KEY", "test-google-key")
    r = client.get("/healthz")
    assert r.status_code == 503 and r.get_json() == {"ok": False, "problems": ["last scan failed"]}


def test_scan_age(con, clock, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_PLACES_KEY", "test-google-key")
    con.execute("UPDATE meta SET value = ? WHERE key = 'created_at'", (str(clock.t),))
    assert health.problems(con) == []                                 # a new install gets 9 days for its first scan
    clock.advance(9 * 86400 + 1)
    assert health.problems(con) == ["no scan in 9 days"]
    add_run(con, clock.t - 600, clock.t - 60)
    assert health.problems(con) == []
    add_run(con, clock.t - 30, None)                                  # running now: not a failure
    assert health.problems(con) == []
    clock.advance(10 * 86400)
    assert health.problems(con) == ["no scan in 9 days"]


def test_healthz_needs_no_sign_in(client):
    assert client.get("/healthz").status_code == 200
