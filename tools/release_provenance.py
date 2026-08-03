#!/usr/bin/env python3
"""Create deterministic in-toto provenance for release subjects."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


REPOSITORY = "https://github.com/trycopilotai/lint"
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
REVISION_PATTERN = re.compile(r"[0-9a-f]{40}")


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if block == b"":
                break
            value.update(block)
    return value.hexdigest()


def release_inputs(
    *,
    version: str,
    revision: str,
    archive: Path,
    manifest: Path,
) -> tuple[dict[str, str], dict[str, Any]]:
    value = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("release manifest must contain an object")
    if value.get("release") != version:
        raise ValueError("release manifest has the wrong version")
    source = value.get("source")
    if not isinstance(source, dict):
        raise ValueError("release manifest is missing source metadata")
    if source.get("commit") != revision:
        raise ValueError("release manifest has the wrong source commit")
    if source.get("archive") != archive.name:
        raise ValueError("release manifest has the wrong archive name")
    archive_digest = sha256(archive)
    if source.get("sha256") != archive_digest:
        raise ValueError("release manifest has the wrong archive digest")

    images = value.get("images")
    if not isinstance(images, dict):
        raise ValueError("release manifest is missing image digests")
    validated_images: dict[str, str] = {}
    for image, digest in images.items():
        if not isinstance(image, str):
            raise ValueError("release image names must be strings")
        if not isinstance(digest, str):
            raise ValueError(f"{image} digest must be a string")
        if DIGEST_PATTERN.fullmatch(digest) is None:
            raise ValueError(f"{image} has an invalid digest")
        validated_images[image] = digest
    if not validated_images:
        raise ValueError("release manifest has no image digests")
    return validated_images, value


def build_statement(
    *,
    version: str,
    revision: str,
    archive: Path,
    manifest: Path,
    workflow: Path,
) -> dict[str, Any]:
    if REVISION_PATTERN.fullmatch(revision) is None:
        raise ValueError("revision must be a 40-character Git commit")
    images, _ = release_inputs(
        version=version,
        revision=revision,
        archive=archive,
        manifest=manifest,
    )
    subjects = [
        {"name": archive.name, "digest": {"sha256": sha256(archive)}},
        {"name": manifest.name, "digest": {"sha256": sha256(manifest)}},
    ]
    for image in sorted(images):
        subjects.append(
            {
                "name": image,
                "digest": {"sha256": images[image].removeprefix("sha256:")},
            }
        )

    tag = f"v{version}"
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "predicate": {
            "buildDefinition": {
                "buildType": f"{REPOSITORY}/.github/workflows/release.yml@v1",
                "externalParameters": {"release_ref": tag},
                "internalParameters": {"release_manifest": manifest.name},
                "resolvedDependencies": [
                    {
                        "uri": f"git+{REPOSITORY}@refs/tags/{tag}",
                        "digest": {"gitCommit": revision},
                    },
                    {
                        "uri": ".github/workflows/release.yml",
                        "digest": {"sha256": sha256(workflow)},
                    },
                ],
            },
            "runDetails": {
                "builder": {"id": f"{REPOSITORY}/actions/workflows/release.yml"},
                "metadata": {"invocationId": f"git+{REPOSITORY}@{revision}#{tag}"},
            },
        },
        "predicateType": "https://slsa.dev/provenance/v1",
        "subject": subjects,
    }


def write_statement(
    *,
    version: str,
    revision: str,
    archive: Path,
    manifest: Path,
    workflow: Path,
    output: Path,
) -> None:
    statement = build_statement(
        version=version,
        revision=revision,
        archive=archive,
        manifest=manifest,
        workflow=workflow,
    )
    output.write_text(
        json.dumps(statement, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--version", required=True)
    argument_parser.add_argument("--revision", required=True)
    argument_parser.add_argument("--archive", type=Path, required=True)
    argument_parser.add_argument("--manifest", type=Path, required=True)
    argument_parser.add_argument("--workflow", type=Path, required=True)
    argument_parser.add_argument("--output", type=Path, required=True)
    arguments = argument_parser.parse_args()
    write_statement(
        version=arguments.version,
        revision=arguments.revision,
        archive=arguments.archive,
        manifest=arguments.manifest,
        workflow=arguments.workflow,
        output=arguments.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
