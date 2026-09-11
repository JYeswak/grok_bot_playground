"""Marks `bin/` as the installable payload subpackage `grok_bot_ops.bin`.

This file is PACKAGING, not code. `pyproject.toml` maps the repo's top-level `bin/` onto
`grok_bot_ops.bin` with `[tool.setuptools.package-dir]`; setuptools only installs a directory as
a package when it has an `__init__.py`, and the alternatives are worse — `data-files` is not
relocatable into a wheel's package tree, and duplicating `bin/` under a `src/` layout would give
the tool two copies that can drift.

It is inert. Nothing imports `grok_bot_ops.bin`: `bin/gb` still adds its own directory to
`sys.path` and imports `gbargs`/`gbtypes`/`gblib` as top-level modules, exactly as it does from a
clone. An `__init__.py` on a directory that is also on `sys.path` changes nothing about that.

It exists only in the exported public tree; the source repo's `bin/` has no `__init__.py`, so
the repo's own gates keep grading the producers as the flat script directory they are.
"""
