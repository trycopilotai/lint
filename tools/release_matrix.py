#!/usr/bin/env python3
"""Expand the release matrix to one row per language package."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def release_rows() -> list[dict[str, Any]]:
    matrix = json.loads((ROOT / "images" / "matrix.json").read_text(encoding="utf-8"))
    rows = matrix.get("images")
    if not isinstance(rows, list):
        raise ValueError("image matrix is missing images")
    expanded: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("image matrix rows must be objects")
        languages = row.get("languages")
        target = row.get("target")
        budget = row.get("budget_mib")
        if not isinstance(languages, list):
            raise ValueError("image matrix languages must be a list")
        if not isinstance(target, str):
            raise ValueError("image matrix target must be a string")
        if not isinstance(budget, int):
            raise ValueError("image matrix budget must be an integer")
        for language in languages:
            if not isinstance(language, str):
                raise ValueError("language names must be strings")
            expanded.append(
                {
                    "language": language,
                    "target": target,
                    "budget_mib": budget,
                }
            )
    return expanded


def main() -> int:
    print(
        json.dumps(
            {"include": release_rows()},
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
