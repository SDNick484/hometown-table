"""Per-task pytest plugins: tests/plugins/<task>.py holds that task's fixtures and helpers. tests/conftest.py loads
every module here as a plugin; tests import helpers with `from plugins.<task> import ...`."""
