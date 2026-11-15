"""One module per feature, each with `bp = Blueprint("<module name>", __name__)` and its routes, every one decorated
with `@editor_required`. app.py registers the `bp` of every module here, sorted by module name. A task adds its own
module and never edits another's."""
