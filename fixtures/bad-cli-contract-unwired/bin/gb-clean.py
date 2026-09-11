#!/usr/bin/env python3
"""A compliant producer: atomic write, deadlined child."""

import pathlib
import subprocess


def emit(out: pathlib.Path, body: str) -> None:
    atomic_write_text(out, body)  # noqa: F821 - fixture text, never imported


def probe() -> str:
    return subprocess.run(["true"], timeout=5, capture_output=True).stdout.decode()
