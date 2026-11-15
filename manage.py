"""Command-line chores on the host: `python3 manage.py <command> [args]`, as the `hometown` user.

Every module in the `commands` package is a command: NAME, HELP, add_arguments(parser), run(args) -> int.
`python3 manage.py --help` lists them. Commands open the database themselves (db.connect(settings.DB_PATH), then the
idempotent db.migrate(con))."""
import argparse
import importlib
import pkgutil
import sys


def load(package: str = "commands") -> list:
    """Every command module of `package`, sorted by module name."""
    pkg = importlib.import_module(package)
    return [importlib.import_module(f"{package}.{m.name}") for m in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name)]


def main(argv=None, package: str = "commands") -> int:
    parser = argparse.ArgumentParser(prog="manage.py", description="Hometown Table host chores.")
    sub = parser.add_subparsers(dest="command", metavar="command", required=True)
    for module in load(package):
        p = sub.add_parser(module.NAME, help=module.HELP, description=module.HELP)
        module.add_arguments(p)
        p.set_defaults(run=module.run)
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:   # --help, or a usage error argparse already printed
        return e.code if isinstance(e.code, int) else 2
    return int(args.run(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
