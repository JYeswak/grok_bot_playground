"""The `gb` console script: locate the installed payload and run `bin/gb` in it.

WHY A SHIM AND NOT A RENAME. `bin/gb` is extensionless on purpose — it is the path form an
operator types straight after `git clone`, and `gb.py` reads as a script rather than a tool.
An extensionless file is not an importable module, and `[project.scripts]` can only name
`package.module:function`. So either the CLI gets renamed to satisfy the packaging metadata, or
the packaging metadata gets a module of its own. This is that module.

WHAT IT DELIBERATELY DOES NOT DO. It does not parse an argument, own a default, reimplement a
verb, or wrap the exit code. `runpy.run_path(..., run_name="__main__")` executes the very same
bytes the cloned repo runs, under the same `__main__` guard, so `gb` installed and `bin/gb`
cloned cannot drift in behaviour: there is one implementation and one entry path into it.
`bin/gb` ends in `gbtypes.main(body)`, which raises `SystemExit` with the contract's exit code;
that propagates out of `run_path` and out of this function untouched.

LAYOUT. `pyproject.toml` maps the repo's top-level `bin/` onto the subpackage
`grok_bot_ops.bin`, so after installation the producers sit next to this file at
`<site-packages>/grok_bot_ops/bin/`. `bin/gb` computes `ROOT = parents[1]`, which therefore
resolves to `<site-packages>/grok_bot_ops/` — a tool root with producers and no deployment
artifacts, which is exactly what an installed copy is. Verbs that read artifacts report
ENVIRONMENT (exit 3) there instead of inventing an answer; see the README's "what it does not
do".
"""

from __future__ import annotations

import pathlib
import runpy
import sys

BIN = pathlib.Path(__file__).resolve().parent / "bin"

EXIT_ENVIRONMENT = 3


def main() -> None:
    """Entry point for the `gb` console script."""
    script = BIN / "gb"
    if not script.is_file():
        # A broken install is ENVIRONMENT, never a usage error: the operator typed the right
        # thing and this machine cannot run it.
        print(
            f"gb: the installed payload is incomplete — {script} is missing.\n"
            f"    fix: pip install --force-reinstall grok-bot-ops",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_ENVIRONMENT)
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
