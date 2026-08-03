#!/usr/bin/env python3
"""Enforce one platform's compressed registry-layer budget."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
from pathlib import Path
from typing import Any


DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
PLATFORMS = ("linux/amd64", "linux/arm64")


def platform_manifest_digest(index: Any, platform: str) -> str:
    """Select exactly one image manifest for an OS/architecture pair."""
    if not isinstance(index, dict):
        raise ValueError("registry index must be an object")
    parts = platform.split("/")
    if len(parts) != 2:
        raise ValueError(f"invalid platform: {platform}")
    operating_system, architecture = parts
    manifests = index.get("manifests")
    if not isinstance(manifests, list):
        raise ValueError("registry index is missing manifests")

    matches: list[str] = []
    for descriptor in manifests:
        if not isinstance(descriptor, dict):
            raise ValueError("registry manifest descriptor must be an object")
        descriptor_platform = descriptor.get("platform")
        if not isinstance(descriptor_platform, dict):
            continue
        if descriptor_platform.get("os") != operating_system:
            continue
        if descriptor_platform.get("architecture") != architecture:
            continue
        digest = descriptor.get("digest")
        if not isinstance(digest, str):
            raise ValueError("platform manifest is missing a digest")
        if DIGEST_PATTERN.fullmatch(digest) is None:
            raise ValueError("platform manifest has an invalid digest")
        matches.append(digest)
    if len(matches) != 1:
        raise ValueError(f"platform {platform} found {len(matches)} image manifests")
    return matches[0]


def compressed_layer_bytes(manifest: Any) -> int:
    """Sum the compressed layer descriptor sizes in an OCI manifest."""
    if not isinstance(manifest, dict):
        raise ValueError("platform manifest must be an object")
    layers = manifest.get("layers")
    if not isinstance(layers, list):
        raise ValueError("platform manifest is missing layers")
    total = 0
    for layer in layers:
        if not isinstance(layer, dict):
            raise ValueError("registry layer must be an object")
        size = layer.get("size")
        if type(size) is not int or size < 0:
            raise ValueError("registry layer size must be a nonnegative integer")
        total += size
    return total


def require_budget(size: int, budget_mib: int) -> None:
    """Reject a compressed layer set above its hard byte budget."""
    maximum = budget_mib * 1024 * 1024
    if size > maximum:
        raise ValueError(f"image is {size} compressed bytes; budget is {maximum}")


def inspect_raw(reference: str) -> Any:
    """Read one raw registry index or manifest with Docker Buildx."""
    completed = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", "--raw", reference],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip()
        raise ValueError(f"could not inspect {reference}: {detail}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ValueError(f"registry returned invalid JSON for {reference}") from error


def verify(
    image: str,
    digest: str,
    platform: str,
    budget_mib: int,
) -> dict[str, Any]:
    """Verify one platform under a multi-platform image digest."""
    if image == "":
        raise ValueError("image must not be empty")
    if DIGEST_PATTERN.fullmatch(digest) is None:
        raise ValueError("image digest is invalid")
    index = inspect_raw(f"{image}@{digest}")
    platform_digest = platform_manifest_digest(index, platform)
    manifest = inspect_raw(f"{image}@{platform_digest}")
    size = compressed_layer_bytes(manifest)
    require_budget(size, budget_mib)
    return {
        "budget_bytes": budget_mib * 1024 * 1024,
        "compressed_layer_bytes": size,
        "digest": digest,
        "image": image,
        "platform": platform,
        "platform_digest": platform_digest,
        "status": "ok",
    }


def read_oci_layout_file(layout: Path, name: str) -> bytes:
    """Read one required regular file from an OCI directory or tar."""

    if layout.is_dir():
        path = layout / name
        if not path.is_file():
            raise ValueError(f"OCI layout is missing {name}")
        return path.read_bytes()
    if not layout.is_file():
        raise ValueError(f"OCI layout does not exist: {layout}")
    with tarfile.open(layout, "r:*") as handle:
        try:
            member = handle.getmember(name)
        except KeyError as error:
            raise ValueError(f"OCI layout is missing {name}") from error
        if not member.isfile():
            raise ValueError(f"OCI layout member is not a file: {name}")
        extracted = handle.extractfile(member)
        if extracted is None:
            raise ValueError(f"OCI layout member could not be read: {name}")
        return extracted.read()


def read_oci_layout_json(layout: Path, name: str) -> Any:
    payload = read_oci_layout_file(layout, name)
    try:
        return json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError(f"OCI layout contains invalid JSON: {name}") from error


def verify_oci_layout(
    layout: Path,
    platform: str,
    budget_mib: int,
) -> dict[str, Any]:
    """Verify compressed layers before an image reaches a registry."""

    index = read_oci_layout_json(layout, "index.json")
    platform_digest = platform_manifest_digest(index, platform)
    algorithm, digest = platform_digest.split(":", 1)
    blob_name = f"blobs/{algorithm}/{digest}"
    manifest_payload = read_oci_layout_file(layout, blob_name)
    actual_digest = hashlib.sha256(manifest_payload).hexdigest()
    if actual_digest != digest:
        raise ValueError(
            f"OCI manifest digest mismatch: expected {digest}, found {actual_digest}"
        )
    try:
        manifest = json.loads(manifest_payload)
    except json.JSONDecodeError as error:
        raise ValueError("OCI platform manifest contains invalid JSON") from error
    size = compressed_layer_bytes(manifest)
    require_budget(size, budget_mib)
    return {
        "budget_bytes": budget_mib * 1024 * 1024,
        "compressed_layer_bytes": size,
        "layout": str(layout),
        "platform": platform,
        "platform_digest": platform_digest,
        "status": "ok",
    }


def main() -> int:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--image")
    argument_parser.add_argument("--digest")
    argument_parser.add_argument("--oci-layout", type=Path)
    argument_parser.add_argument(
        "--platform",
        required=True,
        choices=PLATFORMS,
    )
    argument_parser.add_argument("--budget-mib", required=True, type=int)
    arguments = argument_parser.parse_args()
    if arguments.oci_layout is not None:
        if arguments.image is not None or arguments.digest is not None:
            raise ValueError("--oci-layout cannot be combined with registry inputs")
        result = verify_oci_layout(
            layout=arguments.oci_layout,
            platform=arguments.platform,
            budget_mib=arguments.budget_mib,
        )
    else:
        if arguments.image is None or arguments.digest is None:
            raise ValueError("registry verification requires --image and --digest")
        result = verify(
            image=arguments.image,
            digest=arguments.digest,
            platform=arguments.platform,
            budget_mib=arguments.budget_mib,
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
