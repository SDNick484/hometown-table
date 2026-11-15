"""The shared files keep the slot shape that lets the plan's 13 later branches merge without conflicts (plan: Shared
files). A task edits only the inside of its own slot; these tests fail if a slot heading or marker goes missing, moves,
or two slots end up on adjacent lines."""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASKS = {1: "Scaffold", 2: "Places", 3: "Settings", 4: "Visits", 5: "Opening hours", 6: "Tonight", 7: "Google client",
         8: "OSM client", 9: "Weekly scan", 10: "Inbox", 11: "Lists", 12: "Where to Eat import", 13: "MCP",
         14: "Installer", 15: "Visit sync"}
README_HEADINGS = {2: "Places", 3: "Settings", 4: "Visits", 6: "Where tonight", 9: "Discovery", 10: "Inbox",
                   11: "Lists", 12: "Import from Where to Eat", 13: "MCP", 14: "Install", 15: "Synced visits"}
SCREEN_TASKS = (2, 3, 4, 6, 10, 11)


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def section(text, heading, next_heading=None):
    start = text.index(f"\n{heading}\n")
    end = text.index(f"\n{next_heading}\n", start) if next_heading else len(text)
    return text[start:end]


def test_claude_md_status_has_one_item_per_task_with_blank_lines_between():
    status = section(read("CLAUDE.md"), "## Status", "## Modules")
    items = re.findall(r"^- Task (\d+) \(([^)]+)\): (planned|done)$", status, re.M)
    assert [int(n) for n, _, _ in items] == list(TASKS)
    assert items[0][2] == "done"
    lines = status.strip("\n").splitlines()[1:]
    item_lines = [i for i, line in enumerate(lines) if line.startswith("- Task ")]
    assert all(b - a >= 2 for a, b in zip(item_lines, item_lines[1:])), "a blank line between items"


def test_claude_md_modules_has_a_heading_per_later_task():
    text = read("CLAUDE.md")
    modules = section(text, "## Modules")
    headings = re.findall(r"^### Task (\d+): .+$", modules, re.M)
    assert [int(n) for n in headings] == list(range(2, 16))
    assert modules.rstrip("\n").splitlines()[-1].strip(), "the file ends inside the last slot, not after it"


def test_readme_has_a_heading_per_user_facing_task():
    text = read("README.md")
    first = text.index("\n## Places\n")
    headings = re.findall(r"^## (.+)$", text[first:], re.M)
    assert headings == list(README_HEADINGS.values())
    for n, heading in README_HEADINGS.items():                 # each slot: its heading, then its own text
        after = text[text.index(f"\n## {heading}\n"):].splitlines()[2]
        assert after.strip() and not after.startswith("## "), heading
        assert after == f"_Lands with Task {n}._" or not after.startswith("_Lands with"), heading


def test_screens_js_has_one_block_per_screen_task():
    src = read("static/js/screens.js")
    headers = re.findall(r"^// --- Task (\d+): .+ ---$", src, re.M)
    assert [int(n) for n in headers] == list(SCREEN_TASKS)
    body = src[src.index("// --- Task"):]
    for block in body.split("// --- Task")[1:]:
        lines = block.splitlines()[1:]
        content = [line for line in lines if line.strip()]
        assert content, "every block holds its placeholder or its imports"
        for line in content:
            assert line == "// (not built yet)" or re.fullmatch(r'import "\./[a-z0-9-]+\.js";', line), line
