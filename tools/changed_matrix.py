#!/usr/bin/env python3
"""Select image targets affected by a Git diff."""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
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
        "images/dependency_closures.json",
        "images/generate_image_inventory.py",
        "images/license_sources.json",
        "images/matrix.json",
        "images/smoke.py",
        "images/sources.json",
        "images/verify_cli.py",
        "images/verify_images.py",
        "images/verify_registry_size.py",
        "images/verify_tool_version.py",
        "languages.json",
        "lint.py",
        "tools/changed_matrix.py",
        "tools/generate_legal_payloads.py",
        "tools/verify_dependency_closures.py",
    }
)

# Files the Dockerfile COPYs into a specific image. Without
# this they fall through to extension matching, which sends
# source files to whichever language claims their suffix and
# leaves extensionless lockfiles unselected:
# kotlin_entrypoint.c and xml_entrypoint.c would select the
# clang row and requirements_entrypoint.py the black row, so
# changing an image's own entrypoint rebuilt and smoke-tested
# a different image and left the changed one untouched.
IMAGE_INPUT_TARGETS = {
    "images/closures/buildifier-go-build-info.txt": "bazel",
    "images/closures/ktlint-runtime-classpath.txt": "kotlin",
    "images/closures/shfmt-go-build-info.txt": "shell",
    "images/julia_entrypoint.jl": "julia",
    "images/kotlin_entrypoint.c": "kotlin",
    "images/requirements_entrypoint.py": "requirements",
    "images/swift/Package.resolved": "swift",
    "images/xml_entrypoint.c": "xml",
}


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
        for filename in filenames:
            if fnmatch.fnmatch(path.name, filename):
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
    target_ids: set[str] = set()
    for path in paths:
        if path.parts[:2] == ("images", "licenses"):
            if len(path.parts) < 4:
                return rows
            target_ids.add(path.parts[2])
            continue
        if path.parts[:2] == ("images", "inventories"):
            matched = re.fullmatch(
                r"(?P<target>[a-z0-9]+(?:-[a-z0-9]+)*)-(?:amd64|arm64)\.json",
                path.name,
            )
            if matched is None:
                return rows
            target_ids.add(matched.group("target"))
            continue
        image_target = IMAGE_INPUT_TARGETS.get(path.as_posix())
        if image_target is not None:
            language_ids.add(image_target)
            continue
        if path.parts and path.parts[0] == "fixtures":
            if len(path.parts) > 1:
                language_ids.add(path.parts[1])
        language = language_for(path)
        if language is not None:
            language_ids.add(language)

    selected: list[dict[str, Any]] = []
    for row in rows:
        target = row.get("target")
        languages = row.get("languages")
        if isinstance(target, str) and target in target_ids:
            selected.append(row)
            continue
        if not isinstance(languages, list):
            continue
        if language_ids.intersection(str(value) for value in languages):
            selected.append(row)
    return selected


def include_rows(base: str | None) -> list[dict[str, Any]]:
    include: list[dict[str, Any]] = []
    for row in selected_rows(base):
        target = row.get("target")
        languages = row.get("languages")
        budget_mib = row.get("budget_mib")
        if not isinstance(target, str):
            raise ValueError("image row target must be a string")
        if not isinstance(languages, list):
            raise ValueError("image row languages must be a list")
        if not isinstance(budget_mib, int):
            raise ValueError("image row budget_mib must be an integer")
        for language in languages:
            if not isinstance(language, str):
                raise ValueError("image row language ids must be strings")
            include.append(
                {
                    "target": target,
                    "language": language,
                    "budget_mib": budget_mib,
                }
            )
    return include


def main() -> int:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--base")
    arguments = argument_parser.parse_args()
    print(
        json.dumps(
            {"include": include_rows(arguments.base)},
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
