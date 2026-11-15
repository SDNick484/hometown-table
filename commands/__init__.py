"""Host chores run as `python3 manage.py <name>`. Each module defines NAME (the command), HELP (one line),
add_arguments(parser) and run(args) -> exit status. manage.py finds every module here, sorted by name. A task adds its
own module and never edits another's."""
