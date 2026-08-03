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
ATTESTATION_REFERENCE_TYPE = "attestation-manifest"
IMAGE_MANIFEST_MEDIA_TYPES = frozenset(
    {
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
    }
)
INDEX_MEDIA_TYPES = frozenset(
    {
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.index.v1+json",
    }
)
MAXIMUM_INDEX_DEPTH = 4
UNKNOWN_PLATFORM = "unknown"


def platform_parts(platform: str) -> tuple[str, str]:
    """Split one platform selector into its OS and architecture."""
    parts = platform.split("/")
    if len(parts) != 2:
        raise ValueError(f"invalid platform: {platform}")
    return parts[0], parts[1]


def platform_manifest_digest(index: Any, platform: str) -> str:
    """Select exactly one image manifest for an OS/architecture pair."""
    if not isinstance(index, dict):
        raise ValueError("registry index must be an object")
    operating_system, architecture = platform_parts(platform)
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


def read_oci_layout_blob(layout: Path, digest: str) -> bytes:
    """Read one layout blob and confirm it hashes to its own digest."""

    algorithm, encoded = digest.split(":", 1)
    payload = read_oci_layout_file(layout, f"blobs/{algorithm}/{encoded}")
    actual = hashlib.sha256(payload).hexdigest()
    if actual != encoded:
        raise ValueError(
            f"OCI blob digest mismatch: expected {encoded}, found {actual}"
        )
    return payload


def read_oci_layout_blob_json(layout: Path, digest: str, description: str) -> Any:
    """Parse one digest-verified layout blob as a JSON object."""

    payload = read_oci_layout_blob(layout, digest)
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError(f"OCI {description} contains invalid JSON") from error
    if not isinstance(document, dict):
        raise ValueError(f"OCI {description} must be an object")
    return document


def is_attestation_descriptor(descriptor: dict[str, Any]) -> bool:
    """Identify a Buildx attestation manifest descriptor."""

    annotations = descriptor.get("annotations")
    if isinstance(annotations, dict):
        reference_type = annotations.get("vnd.docker.reference.type")
        if reference_type == ATTESTATION_REFERENCE_TYPE:
            return True
    descriptor_platform = descriptor.get("platform")
    if not isinstance(descriptor_platform, dict):
        return False
    if descriptor_platform.get("os") != UNKNOWN_PLATFORM:
        return False
    return descriptor_platform.get("architecture") == UNKNOWN_PLATFORM


def descriptor_digest(descriptor: dict[str, Any]) -> str:
    """Return one descriptor's sha256 digest after validating it."""

    digest = descriptor.get("digest")
    if not isinstance(digest, str):
        raise ValueError("platform manifest is missing a digest")
    if DIGEST_PATTERN.fullmatch(digest) is None:
        raise ValueError("platform manifest has an invalid digest")
    return digest


def layout_manifest_candidates(
    layout: Path,
    index: Any,
    platform: str,
    depth: int = 0,
) -> list[str]:
    """Collect a layout's image manifests that may hold a platform.

    A single-platform Buildx export writes index.json with one
    descriptor and no platform on it. When the build carries a
    provenance attestation that descriptor is an image index holding
    the image manifest beside an attestation manifest that declares
    the unknown platform; without one it is the image manifest
    itself. Descend through indexes, whose blobs are digest-verified
    like any other, and treat a descriptor as a candidate when it is
    an image manifest, is not an attestation, and either declares the
    requested platform or declares none at all. A candidate that
    declares none is accepted only once its config blob names the
    platform.
    """

    if depth > MAXIMUM_INDEX_DEPTH:
        raise ValueError("OCI layout nests image indexes too deeply")
    if not isinstance(index, dict):
        raise ValueError("registry index must be an object")
    operating_system, architecture = platform_parts(platform)
    manifests = index.get("manifests")
    if not isinstance(manifests, list):
        raise ValueError("registry index is missing manifests")

    candidates: list[str] = []
    for descriptor in manifests:
        if not isinstance(descriptor, dict):
            raise ValueError("registry manifest descriptor must be an object")
        if is_attestation_descriptor(descriptor):
            continue
        descriptor_platform = descriptor.get("platform")
        if isinstance(descriptor_platform, dict):
            if descriptor_platform.get("os") != operating_system:
                continue
            if descriptor_platform.get("architecture") != architecture:
                continue
        media_type = descriptor.get("mediaType")
        if media_type in INDEX_MEDIA_TYPES:
            nested = read_oci_layout_blob_json(
                layout,
                descriptor_digest(descriptor),
                "image index",
            )
            candidates.extend(
                layout_manifest_candidates(
                    layout,
                    nested,
                    platform,
                    depth + 1,
                )
            )
            continue
        if media_type is not None and media_type not in IMAGE_MANIFEST_MEDIA_TYPES:
            continue
        candidates.append(descriptor_digest(descriptor))
    return candidates


def config_platform(layout: Path, manifest: Any) -> tuple[str, str]:
    """Read the OS and architecture an image config declares."""

    if not isinstance(manifest, dict):
        raise ValueError("platform manifest must be an object")
    config = manifest.get("config")
    if not isinstance(config, dict):
        raise ValueError("platform manifest is missing a config")
    digest = config.get("digest")
    if not isinstance(digest, str):
        raise ValueError("platform config is missing a digest")
    if DIGEST_PATTERN.fullmatch(digest) is None:
        raise ValueError("platform config has an invalid digest")
    document = read_oci_layout_blob_json(layout, digest, "image config")
    operating_system = document.get("os")
    architecture = document.get("architecture")
    if not isinstance(operating_system, str):
        raise ValueError("image config is missing its os")
    if not isinstance(architecture, str):
        raise ValueError("image config is missing its architecture")
    return operating_system, architecture


def layout_platform_manifest(
    layout: Path,
    index: Any,
    platform: str,
) -> tuple[str, Any]:
    """Select and load the one image manifest built for a platform."""

    candidates = layout_manifest_candidates(layout, index, platform)
    if len(candidates) != 1:
        raise ValueError(f"platform {platform} found {len(candidates)} image manifests")
    platform_digest = candidates[0]
    manifest = read_oci_layout_blob_json(
        layout,
        platform_digest,
        "platform manifest",
    )
    found = config_platform(layout, manifest)
    if found != platform_parts(platform):
        found_platform = f"{found[0]}/{found[1]}"
        raise ValueError(f"platform {platform} image config declares {found_platform}")
    return platform_digest, manifest


def verify_oci_layout(
    layout: Path,
    platform: str,
    budget_mib: int,
) -> dict[str, Any]:
    """Verify compressed layers before an image reaches a registry."""

    index = read_oci_layout_json(layout, "index.json")
    platform_digest, manifest = layout_platform_manifest(layout, index, platform)
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
