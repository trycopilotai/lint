#!/usr/bin/env python3
"""Create a deterministic manifest for a release archive and images."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


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


def image_digests(directory: Path) -> dict[str, str]:
    digests: dict[str, str] = {}
    for path in sorted(directory.glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"{path} must contain an object")
        image = value.get("image")
        digest = value.get("digest")
        if not isinstance(image, str):
            raise ValueError(f"{path} is missing image")
        if not isinstance(digest, str):
            raise ValueError(f"{path} is missing digest")
        digests[image] = digest
    return digests


def main() -> int:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--version", required=True)
    argument_parser.add_argument("--archive", required=True, type=Path)
    argument_parser.add_argument(
        "--digests",
        required=True,
        type=Path,
    )
    argument_parser.add_argument("--output", required=True, type=Path)
    arguments = argument_parser.parse_args()

    tools = json.loads((ROOT / "languages.json").read_text(encoding="utf-8"))["tools"]
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "release": arguments.version,
        "source": {
            "commit": commit(),
            "archive": arguments.archive.name,
            "sha256": sha256(arguments.archive),
        },
        "tools": tools,
        "images": image_digests(arguments.digests),
    }
    arguments.output.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
