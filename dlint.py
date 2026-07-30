#!/usr/bin/env python3
"""Run lint.py with its Docker backend."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIRECTORY = str(Path(__file__).resolve().parent)
if SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, SCRIPT_DIRECTORY)
import lint


def main() -> int:
    return lint.main(["--docker", *sys.argv[1:]])


if __name__ == "__main__":
    sys.exit(main())
