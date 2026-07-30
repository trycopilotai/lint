#!/usr/bin/env python3
"""Select image targets affected by a Git diff."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CORE_PATHS = frozenset(
    {
        "action.yml",
        "action_entrypoint.py",
        "dlint.py",
        "images/Dockerfile",
        "images/matrix.json",
        "images/sources.json",
        "languages.json",
        "lint.py",
    }
)


def load_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def changed_paths(base: str) -> list[Path]:
    completed = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--name-only", f"{base}...HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    return [Path(line) for line in completed.stdout.splitlines() if line]


def all_rows() -> list[dict[str, Any]]:
    matrix = load_object(ROOT / "images" / "matrix.json")
    rows = matrix.get("images")
    if not isinstance(rows, list):
        raise ValueError("image matrix is missing images")
    output: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("image rows must be objects")
        output.append(row)
    return output


def language_for(path: Path) -> str | None:
    manifest = load_object(ROOT / "languages.json")
    rows = manifest.get("languages")
    if not isinstance(rows, list):
        raise ValueError("language manifest is missing languages")
    for row in rows:
        if not isinstance(row, dict):
            continue
        language_id = row.get("id")
        filenames = row.get("filenames")
        extensions = row.get("extensions")
        if not isinstance(language_id, str):
            continue
        if not isinstance(filenames, list):
            continue
        if not isinstance(extensions, list):
            continue
        if path.name in filenames:
            return language_id
        if path.suffix.lower() in extensions:
            return language_id
    return None


def selected_rows(base: str | None) -> list[dict[str, Any]]:
    rows = all_rows()
    if base is None:
        return rows
    paths = changed_paths(base)
    for path in paths:
        if path.as_posix() in CORE_PATHS:
            return rows
        if path.parts[:2] == (".github", "workflows"):
            return rows

    language_ids: set[str] = set()
    for path in paths:
        if path.parts and path.parts[0] == "fixtures":
            if len(path.parts) > 1:
                language_ids.add(path.parts[1])
        language = language_for(path)
        if language is not None:
            language_ids.add(language)

    selected: list[dict[str, Any]] = []
    for row in rows:
        languages = row.get("languages")
        if not isinstance(languages, list):
            continue
        if language_ids.intersection(str(value) for value in languages):
            selected.append(row)
    return selected


def main() -> int:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--base")
    arguments = argument_parser.parse_args()
    include: list[dict[str, Any]] = []
    for row in selected_rows(arguments.base):
        languages = row["languages"]
        include.append(
            {
                "target": row["target"],
                "language": languages[0],
                "budget_mib": row["budget_mib"],
            }
        )
    print(json.dumps({"include": include}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
