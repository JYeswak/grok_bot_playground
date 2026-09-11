"""grok_bot_ops — the installable wrapper around the `gb` operator CLI.

This package exists for exactly one reason: a console entry point names `package.module:function`
and `bin/gb` is extensionless, so there is no module to name. Everything of substance lives in
`bin/`, which is installed alongside this file as `grok_bot_ops/bin/`. See `cli.py`.
"""

from __future__ import annotations

__all__ = ["__version__"]

# Kept equal to `VERSION` in `bin/gb` and to `[project] version` in `pyproject.toml`.
# `bin/gb-export-public.py` refuses to build an export where the three disagree, so this is a
# checked constant rather than a comment asking three files to be edited together.
__version__ = "1.0.0"
