"""mcp_remote.py is a verbatim hand copy of SDNick484/mcp-server-shared's src/mcp_shared/remote.py under three header
lines (Matinee's format). This checks the body still hashes to the sha256 its header pins, so an edit to the copy fails
here; the fix belongs in mcp-server-shared, then a fresh copy. Needs no MCP SDK: it only reads the file."""
import hashlib
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UPSTREAM_COMMIT = "fc64077c165c5b182f30c59b4ab91a3492831fed"
UPSTREAM_SHA256 = "bfb7780bd3e1cf5d6173d5ec391309ed563e18ed8632b75f69afe0114f1beecf"
HEADER = (f"# Verbatim copy of SDNick484/mcp-server-shared src/mcp_shared/remote.py @ {UPSTREAM_COMMIT}",
          f"# upstream-sha256: {UPSTREAM_SHA256}",
          "# Hand copy, no sync tooling. To update: re-copy, change these two lines, run pytest.")


def test_remote_copy_matches_its_pinned_sha():
    with open(os.path.join(ROOT, "mcp_remote.py"), "rb") as f:
        lines = f.read().decode("utf-8").split("\n")
    assert tuple(lines[:3]) == HEADER
    pinned = re.fullmatch(r"# upstream-sha256: ([0-9a-f]{64})", lines[1]).group(1)
    body = "\n".join(lines[3:]).encode("utf-8")
    assert hashlib.sha256(body).hexdigest() == pinned
