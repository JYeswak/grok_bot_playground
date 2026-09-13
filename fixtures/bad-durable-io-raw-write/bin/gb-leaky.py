#!/usr/bin/env python3
"""A producer of the kind g22 exists to catch. Not imported, never run — parsed."""
from __future__ import annotations

import json
import pathlib
import subprocess


def emit(out: pathlib.Path, doc: dict) -> None:
    out.write_text(json.dumps(doc, indent=1) + '\n')   # truncate-then-write: tearable


def fetch() -> str:
    proc = subprocess.Popen(['curl', '-s', 'https://x.ai'], stdout=subprocess.PIPE)
    out, _ = proc.communicate()                          # no deadline: hangs the tick
    return out.decode()
