"""Task 13 helpers: the MCP's `Api` pointed at the real Flask app, and sync wrappers to list and call a server's tools.

FlaskApi is mcp_server.Api with its one network method replaced: each request goes through app.test_client() from
127.0.0.1, with exactly the headers the Api built (the bearer key, X-Hometown on writes, X-Hometown-As when set), so the
app's real sign-in, mutation guard and validation answer. Every request is kept in `.sent`.

Nothing here imports mcp, mcp_server or anyio when the module loads: the MCP SDK is only in requirements-mcp.txt, and
tests/mcp/ skips without it (CI's mcp job sets HOMETOWN_NO_SKIP=1, so there it must run)."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from conftest import EDITOR, READ_KEY, WRITE_KEY

APP_URL = "http://127.0.0.1:8782"


def flask_api(key=WRITE_KEY, as_email=None):
    """An mcp_server.Api whose requests reach the Flask app in this process."""
    import app as hometown
    import mcp_server

    class FlaskApi(mcp_server.Api):
        sent: list

        def _send(self, method, path, data, headers):
            self.sent.append({"method": method, "path": path, "headers": dict(headers), "data": data})
            r = hometown.app.test_client().open(path, method=method, data=data, headers=headers,
                                                environ_base={"REMOTE_ADDR": "127.0.0.1"})
            return r.status_code, r.get_data()

    api = FlaskApi(APP_URL, key, as_email)
    api.sent = []
    return api


@pytest.fixture
def api():
    """The read key, acting for no one: what the service holds at write level none."""
    return flask_api(READ_KEY)


@pytest.fixture
def api_as_alex():
    """The write key, acting for alex@example.com (an editor in ADMIN_EMAILS), as the remote service does for a request
    whose Access assertion names Alex."""
    return flask_api(WRITE_KEY, as_email=EDITOR)


@pytest.fixture
def api_all():
    """The write key, acting for no one."""
    return flask_api(WRITE_KEY)


def run_async(fn, *args):
    """anyio.run(fn, *args) on a thread of its own: the Playwright tests (tests/ui) leave an event loop running in the
    main thread when both directories run in one session, and anyio refuses to start a second one there."""
    import anyio
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(anyio.run, fn, *args).result()


def tool_names(server) -> set:
    return {t.name for t in run_async(server.list_tools)}


def call(server, name, **arguments):
    """The tool's structured result. A tool error raises mcp_server.ToolError (its text is what the model sees)."""
    result = run_async(server.call_tool, name, arguments)
    assert not result.is_error, result.content
    return result.structured_content


def call_error(server, name, **arguments) -> str:
    """The message a failing tool call gives the model, without the SDK's "Error executing tool <name>: " prefix."""
    import mcp_server
    try:
        call(server, name, **arguments)
    except mcp_server.ToolError as e:
        text = str(e)
        prefix = f"Error executing tool {name}: "
        return text[len(prefix):] if text.startswith(prefix) else text
    raise AssertionError(f"{name} did not fail")
