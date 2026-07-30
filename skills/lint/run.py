#!/usr/bin/env python3
"""Launch the lint engine from a plugin or standalone skill."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def engine_path() -> Path:
    directory = Path(__file__).resolve().parent
    candidates = (
        directory / "lint.py",
        directory.parent.parent / "lint.py",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("lint.py is missing from the installed skill")


def main() -> int:
    command = [sys.executable, str(engine_path())]
    command.extend(sys.argv[1:])
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
