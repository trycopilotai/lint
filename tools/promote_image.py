#!/usr/bin/env python3
"""Promote one immutable image alias without overwriting it."""

from __future__ import annotations

import argparse
import json
import re
import subprocess


DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")


def inspect_command(reference: str) -> list[str]:
    return [
        "docker",
        "buildx",
        "imagetools",
        "inspect",
        reference,
        "--format",
        "{{json .Manifest.Digest}}",
    ]


def missing_alias(reference: str, message: str) -> bool:
    normalized_reference = reference.lower()
    normalized_message = message.lower()
    if normalized_reference not in normalized_message:
        return False
    markers = (
        "manifest unknown",
        f"{normalized_reference}: not found",
        f"{normalized_reference} not found",
    )
    return any(marker in normalized_message for marker in markers)


def inspect_digest(reference: str) -> str | None:
    completed = subprocess.run(
        inspect_command(reference),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        if missing_alias(reference, completed.stderr):
            return None
        message = completed.stderr.strip()
        raise RuntimeError(f"could not inspect {reference}: {message}")

    try:
        digest = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"could not parse digest for {reference}") from error
    if not isinstance(digest, str):
        raise RuntimeError(f"registry returned no digest for {reference}")
    if DIGEST_PATTERN.fullmatch(digest) is None:
        raise RuntimeError(f"registry returned an invalid digest for {reference}")
    return digest


def promote(image: str, version: str, staging_tag: str, digest: str) -> None:
    if DIGEST_PATTERN.fullmatch(digest) is None:
        raise ValueError("digest must be a sha256 digest")
    alias = f"{image}:{version}"
    existing = inspect_digest(alias)
    if existing is not None:
        if existing != digest:
            raise ValueError(
                f"{alias} already resolves to {existing}, expected {digest}"
            )
        return

    staging = f"{image}:{staging_tag}@{digest}"
    subprocess.run(
        [
            "docker",
            "buildx",
            "imagetools",
            "create",
            "--tag",
            alias,
            staging,
        ],
        check=True,
    )
    promoted = inspect_digest(alias)
    if promoted != digest:
        raise RuntimeError(f"{alias} promotion did not resolve to {digest}")


def main() -> int:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--image", required=True)
    argument_parser.add_argument("--version", required=True)
    argument_parser.add_argument("--staging-tag", required=True)
    argument_parser.add_argument("--digest", required=True)
    arguments = argument_parser.parse_args()
    promote(
        arguments.image,
        arguments.version,
        arguments.staging_tag,
        arguments.digest,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
