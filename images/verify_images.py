#!/usr/bin/env python3
"""Validate image coverage, pins, and optional local sizes."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "images" / "matrix.json"
SOURCES_PATH = ROOT / "images" / "sources.json"
LANGUAGES_PATH = ROOT / "languages.json"
DOCKERFILE_PATH = ROOT / "images" / "Dockerfile"


def load_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def image_rows() -> list[dict[str, Any]]:
    matrix = load_object(MATRIX_PATH)
    rows = matrix.get("images")
    if not isinstance(rows, list):
        raise ValueError("image matrix is missing images")
    parsed: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("image rows must be objects")
        parsed.append(row)
    return parsed


def validate_coverage() -> None:
    manifest = load_object(LANGUAGES_PATH)
    language_rows = manifest.get("languages")
    if not isinstance(language_rows, list):
        raise ValueError("language manifest is missing languages")
    expected = {
        row["id"]
        for row in language_rows
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }

    found: list[str] = []
    targets: list[str] = []
    for row in image_rows():
        languages = row.get("languages")
        target = row.get("target")
        budget = row.get("budget_mib")
        if not isinstance(languages, list):
            raise ValueError("image languages must be a list")
        if not isinstance(target, str):
            raise ValueError("image target must be a string")
        if not isinstance(budget, int):
            raise ValueError("image budget must be an integer")
        found.extend(str(language) for language in languages)
        targets.append(target)

    if len(found) != len(set(found)):
        raise ValueError("a language appears in more than one image row")
    if set(found) != expected:
        missing = sorted(expected - set(found))
        extra = sorted(set(found) - expected)
        raise ValueError(f"image coverage differs: missing={missing} extra={extra}")

    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    for target in targets:
        pattern = rf"\bAS\s+{re.escape(target)}\b"
        if re.search(pattern, dockerfile) is None:
            raise ValueError(f"Dockerfile target is missing: {target}")


def validate_sources() -> None:
    sources = load_object(SOURCES_PATH)
    base_images = sources.get("base_images")
    downloads = sources.get("downloads")
    if not isinstance(base_images, dict):
        raise ValueError("sources are missing base_images")
    if not isinstance(downloads, dict):
        raise ValueError("sources are missing downloads")
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    for item in base_images.values():
        if not isinstance(item, dict):
            raise ValueError("base image entries must be objects")
        digest = item.get("digest")
        if not isinstance(digest, str):
            raise ValueError("base image digest must be a string")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
            raise ValueError(f"invalid base image digest: {digest}")
        if digest not in dockerfile:
            raise ValueError(f"base image digest is unused: {digest}")
    for item in downloads.values():
        if not isinstance(item, dict):
            raise ValueError("download entries must be objects")
        checksum = item.get("sha256")
        if not isinstance(checksum, str):
            raise ValueError("download checksum must be a string")
        if re.fullmatch(r"[0-9a-f]{64}", checksum) is None:
            raise ValueError(f"invalid download checksum: {checksum}")
        if checksum not in dockerfile:
            raise ValueError(f"download checksum is unused: {checksum}")


def local_sizes(prefix: str) -> dict[str, int]:
    sizes: dict[str, int] = {}
    version = load_object(MATRIX_PATH)["version"]
    for row in image_rows():
        budget = row["budget_mib"]
        for language in row["languages"]:
            image = f"{prefix}-{language}:{version}"
            completed = subprocess.run(
                [
                    "docker",
                    "image",
                    "inspect",
                    image,
                    "--format",
                    "{{.Size}}",
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if completed.returncode != 0:
                continue
            size = int(completed.stdout.strip())
            sizes[image] = size
            maximum = budget * 1024 * 1024
            if size > maximum:
                raise ValueError(f"{image} is {size} bytes; budget is {maximum}")
    return sizes


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument(
        "--local-prefix",
        help="also validate locally loaded PREFIX-language images",
    )
    return argument_parser


def main() -> int:
    arguments = parser().parse_args()
    validate_coverage()
    validate_sources()
    response: dict[str, Any] = {
        "status": "ok",
        "languages": 26,
        "targets": len(image_rows()),
    }
    if arguments.local_prefix is not None:
        response["local_sizes"] = local_sizes(arguments.local_prefix)
    print(json.dumps(response, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
