#!/usr/bin/env python3
"""Create a deterministic manifest for a release archive and images."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
IMAGE_PREFIX = "ghcr.io/trycopilotai/lint-"
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if block == b"":
                break
            value.update(block)
    return value.hexdigest()


def commit() -> str:
    completed = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def expected_images() -> set[str]:
    matrix = json.loads((ROOT / "images" / "matrix.json").read_text(encoding="utf-8"))
    rows = matrix.get("images")
    if not isinstance(rows, list):
        raise ValueError("image matrix is missing images")
    images = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("image matrix rows must be objects")
        languages = row.get("languages")
        if not isinstance(languages, list):
            raise ValueError("image matrix languages must be a list")
        for language in languages:
            if not isinstance(language, str):
                raise ValueError("language names must be strings")
            image = f"{IMAGE_PREFIX}{language}"
            if image in images:
                raise ValueError(f"duplicate image in release matrix: {image}")
            images.add(image)
    return images


def canonical_json_sha256(value: Any) -> str:
    payload = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_digest_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("image digest manifest is missing images")
    digests: dict[str, str] = {}
    for image, digest in value.items():
        if not isinstance(image, str):
            raise ValueError("image names must be strings")
        if not isinstance(digest, str):
            raise ValueError(f"{image} digest must be a string")
        if DIGEST_PATTERN.fullmatch(digest) is None:
            raise ValueError(f"{image} has an invalid digest")
        digests[image] = digest
    expected = expected_images()
    actual = set(digests)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(
            f"image digest coverage differs: missing={missing} extra={extra}"
        )
    return digests


def image_digests(
    directory: Path,
    *,
    version: str,
    revision: str,
) -> dict[str, str]:
    expected_staging_tag = f"{version}-{revision}"
    records: list[tuple[Path, str, str, str]] = []
    for path in sorted(directory.glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"{path} must contain an object")
        image = value.get("image")
        digest = value.get("digest")
        staging_tag = value.get("staging_tag")
        if not isinstance(image, str):
            raise ValueError(f"{path} is missing image")
        if not isinstance(digest, str):
            raise ValueError(f"{path} is missing digest")
        if not isinstance(staging_tag, str):
            raise ValueError(f"{path} is missing staging_tag")
        records.append((path, image, digest, staging_tag))

    found_images = set()
    for _, image, _, _ in records:
        if image in found_images:
            raise ValueError(f"duplicate image record: {image}")
        found_images.add(image)

    digests: dict[str, str] = {}
    for path, image, digest, staging_tag in records:
        if DIGEST_PATTERN.fullmatch(digest) is None:
            raise ValueError(f"{path} has an invalid digest")
        if staging_tag != expected_staging_tag:
            raise ValueError(f"{path} has an unexpected staging tag")
        if not image.startswith(IMAGE_PREFIX):
            raise ValueError(f"{path} has an unexpected image name")
        language = image.removeprefix(IMAGE_PREFIX)
        if path.name != f"{language}.json":
            raise ValueError(f"{path} does not match its image name")
        digests[image] = digest
    expected = expected_images()
    actual = set(digests)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(
            f"image digest coverage differs: missing={missing} extra={extra}"
        )
    return digests


def main() -> int:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--version", required=True)
    argument_parser.add_argument("--archive", type=Path)
    argument_parser.add_argument("--digests", type=Path, required=True)
    argument_parser.add_argument("--output", type=Path)
    argument_parser.add_argument("--verify-only", action="store_true")
    arguments = argument_parser.parse_args()

    revision = commit()
    digests = image_digests(
        arguments.digests,
        version=arguments.version,
        revision=revision,
    )
    if arguments.verify_only:
        if arguments.archive is not None or arguments.output is not None:
            argument_parser.error("--verify-only does not accept archive output")
        print(
            json.dumps(
                {"images": len(digests), "mode": "built", "status": "ok"},
                sort_keys=True,
            )
        )
        return 0
    if arguments.archive is None:
        argument_parser.error("--archive is required unless --verify-only is used")
    if arguments.output is None:
        argument_parser.error("--output is required unless --verify-only is used")

    tools = json.loads((ROOT / "languages.json").read_text(encoding="utf-8"))["tools"]
    manifest: dict[str, Any] = {
        "release": arguments.version,
        "source": {
            "commit": revision,
            "archive": arguments.archive.name,
            "sha256": sha256(arguments.archive),
        },
        "tools": tools,
    }
    manifest["schema_version"] = 1
    manifest["images"] = digests
    arguments.output.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
