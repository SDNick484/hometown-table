"""Playwright fixtures: `live_url` (the app on a free loopback port, in a thread) and `page` (headless Chromium at a
412 x 915 phone viewport, signed in through Access as alex@example.com). Without Playwright or its Chromium the UI
tests skip; with HOMETOWN_NO_SKIP=1 (CI's ui job) that skip is a failure.

`page` fails the test on an uncaught page error or a Content-Security-Policy violation, so every screen's UI test also
checks that the screen runs under the CSP."""
import threading

import pytest
from werkzeug.serving import make_server

import app as hometown
from conftest import EDITOR, make_jwt

VIEWPORT = {"width": 412, "height": 915}


@pytest.fixture(scope="session")
def live_url():
    server = make_server("127.0.0.1", 0, hometown.app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture(scope="session")
def _browser():
    sync_api = pytest.importorskip("playwright.sync_api", reason="Playwright is not installed: pip install playwright")
    pw = sync_api.sync_playwright().start()
    try:
        browser = pw.chromium.launch()
    except Exception as e:  # noqa: BLE001 - any launch failure means the browser is missing here
        pw.stop()
        pytest.skip(f"Playwright's Chromium is not installed: python -m playwright install chromium ({e})")
    yield browser
    browser.close()
    pw.stop()


@pytest.fixture
def page(_browser, live_url):
    context = _browser.new_context(viewport=VIEWPORT, base_url=live_url, service_workers="block",
                                   extra_http_headers={"Cf-Access-Jwt-Assertion": make_jwt(EDITOR)})
    p = context.new_page()
    problems = []
    p.on("pageerror", lambda e: problems.append(f"page error: {e}"))
    p.on("console", lambda m: problems.append(f"console: {m.text}")
         if m.type == "error" and "Content Security Policy" in m.text else None)
    yield p
    context.close()
    assert not problems, "\n".join(problems)
