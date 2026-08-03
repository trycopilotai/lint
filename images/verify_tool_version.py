#!/usr/bin/env python3
"""Verify the formatter identity embedded in a built image."""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LANGUAGES_PATH = ROOT / "languages.json"
IDENTITY_PATH = "/lint-tool-version"
MAX_IDENTITY_BYTES = 256
TARGET_TO_TOOL = {
    "prettier": "prettier",
    "buildifier": "buildifier",
    "black": "black",
    "requirements": "requirements",
    "shfmt": "shfmt",
    "clang": "clang-format",
    "java": "google-java-format",
    "go": "go",
    "rust": "rustfmt",
    "kotlin": "ktlint",
    "taplo": "taplo",
    "xml": "libxml2",
    "swift": "swift-format",
    "csharp": "csharpier",
    "julia": "juliaformatter",
}
SEMVER_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
CONTAINER_ID_PATTERN = re.compile(r"[0-9a-f]{64}")
RECORD_PATTERN = re.compile(r"([a-z][a-z0-9-]*)=([^\n]+)\n")


def load_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def expected_identity(target: str) -> tuple[str, str]:
    tool = TARGET_TO_TOOL.get(target)
    if tool is None:
        raise ValueError(f"unknown image target: {target}")
    manifest = load_object(LANGUAGES_PATH)
    tools = manifest.get("tools")
    if not isinstance(tools, dict):
        raise ValueError("language manifest is missing tools")
    identity = tools.get(tool)
    if not isinstance(identity, str):
        raise ValueError(f"language manifest is missing tool identity: {tool}")
    if target == "requirements":
        if SHA256_PATTERN.fullmatch(identity) is None:
            raise ValueError("requirements identity must be an exact SHA-256")
    elif SEMVER_PATTERN.fullmatch(identity) is None:
        raise ValueError(f"tool identity must be an exact semantic version: {tool}")
    return tool, identity


def parse_identity(payload: bytes) -> tuple[str, str]:
    if len(payload) > MAX_IDENTITY_BYTES:
        raise ValueError("image tool identity exceeds the size limit")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("image tool identity is not UTF-8") from error
    match = RECORD_PATTERN.fullmatch(text)
    if match is None:
        raise ValueError("image tool identity has an invalid record grammar")
    return match.group(1), match.group(2)


def extract_identity_archive(payload: bytes) -> bytes:
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
            members = archive.getmembers()
            if len(members) != 1:
                raise ValueError("image tool identity archive must contain one member")
            member = members[0]
            if not member.isfile():
                raise ValueError("image tool identity archive member is not a file")
            if member.name != IDENTITY_PATH.lstrip("/"):
                raise ValueError(
                    "image tool identity archive member has the wrong path"
                )
            if member.size > MAX_IDENTITY_BYTES:
                raise ValueError("image tool identity exceeds the size limit")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError("image tool identity archive member cannot be read")
            return extracted.read(MAX_IDENTITY_BYTES + 1)
    except tarfile.TarError as error:
        raise ValueError("image tool identity archive is invalid") from error


def docker_identity(image: str) -> bytes:
    created = subprocess.run(
        ["docker", "create", image],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        container_id = created.stdout.decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise ValueError("docker returned a non-ASCII container id") from error
    if CONTAINER_ID_PATTERN.fullmatch(container_id) is None:
        raise ValueError("docker returned an invalid container id")
    try:
        copied = subprocess.run(
            ["docker", "cp", f"{container_id}:{IDENTITY_PATH}", "-"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return extract_identity_archive(copied.stdout)
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container_id],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )


def verify(target: str, image: str) -> dict[str, str]:
    expected_tool, expected_value = expected_identity(target)
    actual_tool, actual_value = parse_identity(docker_identity(image))
    if actual_tool != expected_tool:
        raise ValueError(
            f"tool identity differs: expected {expected_tool}, got {actual_tool}"
        )
    if actual_value != expected_value:
        raise ValueError(
            f"tool version differs: expected {expected_value}, got {actual_value}"
        )
    return {
        "image": image,
        "target": target,
        "tool": actual_tool,
        "identity": actual_value,
    }


def main() -> int:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument(
        "--target", required=True, choices=sorted(TARGET_TO_TOOL)
    )
    argument_parser.add_argument("--image", required=True)
    arguments = argument_parser.parse_args()
    print(json.dumps(verify(arguments.target, arguments.image), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
