#!/usr/bin/env python3
"""Validate image coverage, pins, and optional local sizes."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "images" / "matrix.json"
SOURCES_PATH = ROOT / "images" / "sources.json"
LANGUAGES_PATH = ROOT / "languages.json"
DOCKERFILE_PATH = ROOT / "images" / "Dockerfile"
FORBIDDEN_EXECUTABLE_NAMES = frozenset(
    {
        "apk",
        "apt",
        "apt-cache",
        "apt-get",
        "aptitude",
        "ar",
        "as",
        "ash",
        "bash",
        "brew",
        "bundle",
        "bundler",
        "busybox",
        "c++",
        "c89",
        "c99",
        "cargo",
        "cc",
        "clang",
        "clang++",
        "cl",
        "cl.exe",
        "cmake",
        "cpp",
        "csc",
        "csh",
        "dash",
        "dnf",
        "dpkg",
        "dpkg-deb",
        "fish",
        "g++",
        "gcc",
        "gem",
        "gfortran",
        "go",
        "icc",
        "ifort",
        "javac",
        "kotlin",
        "kotlinc",
        "ksh",
        "ld",
        "lld",
        "make",
        "microdnf",
        "mksh",
        "msbuild",
        "ninja",
        "nm",
        "npm",
        "npx",
        "objcopy",
        "objdump",
        "pacman",
        "pip",
        "pip3",
        "pipx",
        "pnpm",
        "poetry",
        "port",
        "ranlib",
        "rpm",
        "rustc",
        "rustup",
        "sh",
        "strip",
        "swift",
        "swiftc",
        "tcsh",
        "uv",
        "yarn",
        "yum",
        "zsh",
        "zypper",
    }
)


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
    for option in ("--no-config", "--no-editorconfig"):
        if option not in dockerfile:
            raise ValueError(f"Prettier image is missing locked option: {option}")
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


class ByteCounter:
    def __init__(self) -> None:
        self.count = 0

    def write(self, payload: bytes) -> int:
        self.count += len(payload)
        return len(payload)

    def flush(self) -> None:
        return None


def compressed_image_size(image: str) -> int:
    inspect = subprocess.run(
        ["docker", "image", "inspect", image],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if inspect.returncode != 0:
        detail = inspect.stderr.strip()
        raise ValueError(f"image is unavailable: {image}: {detail}")

    saved = subprocess.Popen(
        ["docker", "image", "save", image],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if saved.stdout is None:
        raise RuntimeError("docker image save did not expose standard output")
    counter = ByteCounter()
    with gzip.GzipFile(
        filename="",
        mode="wb",
        fileobj=counter,
        mtime=0,
    ) as compressed:
        while True:
            block = saved.stdout.read(1024 * 1024)
            if block == b"":
                break
            compressed.write(block)
    standard_error = b""
    if saved.stderr is not None:
        standard_error = saved.stderr.read()
    return_code = saved.wait()
    if return_code != 0:
        detail = standard_error.decode("utf-8", errors="replace").strip()
        raise ValueError(f"could not save image {image}: {detail}")
    return counter.count


def verify_image_budget(image: str, budget_mib: int) -> int:
    size = compressed_image_size(image)
    maximum = budget_mib * 1024 * 1024
    if size > maximum:
        raise ValueError(f"{image} is {size} compressed bytes; budget is {maximum}")
    return size


def forbidden_executable(path: str) -> bool:
    name = PurePosixPath(path).name
    return name in FORBIDDEN_EXECUTABLE_NAMES


def verify_image_contents(image: str) -> int:
    created = subprocess.run(
        ["docker", "container", "create", image],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    container = created.stdout.strip()
    descriptor, archive_name = tempfile.mkstemp(
        prefix="lint-image-",
        suffix=".tar",
    )
    os.close(descriptor)
    archive = Path(archive_name)
    try:
        subprocess.run(
            [
                "docker",
                "container",
                "export",
                "--output",
                str(archive),
                container,
            ],
            check=True,
        )
        paths: set[str] = set()
        forbidden_paths: list[str] = []
        with tarfile.open(archive, "r") as handle:
            for member in handle:
                path = member.name.removeprefix("./")
                paths.add(path)
                executable = member.isfile() and member.mode & 0o111 != 0
                executable = executable or member.issym() or member.islnk()
                if executable and forbidden_executable(path):
                    forbidden_paths.append(path)
        if forbidden_paths:
            values = ", ".join(sorted(forbidden_paths))
            raise ValueError(f"{image} contains forbidden tools: {values}")
        return len(paths)
    finally:
        subprocess.run(
            ["docker", "container", "rm", container],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        archive.unlink(missing_ok=True)


def local_sizes(prefix: str) -> dict[str, int]:
    sizes: dict[str, int] = {}
    version = load_object(MATRIX_PATH)["version"]
    for row in image_rows():
        budget = row["budget_mib"]
        for language in row["languages"]:
            image = f"{prefix}-{language}:{version}"
            sizes[image] = verify_image_budget(image, budget)
            verify_image_contents(image)
    return sizes


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument(
        "--local-prefix",
        help="validate every locally loaded PREFIX-language image",
    )
    argument_parser.add_argument("--image")
    argument_parser.add_argument("--budget-mib", type=int)
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
    if arguments.image is not None:
        if arguments.budget_mib is None:
            raise ValueError("--image requires --budget-mib")
        response["compressed_bytes"] = verify_image_budget(
            arguments.image,
            arguments.budget_mib,
        )
        response["filesystem_entries"] = verify_image_contents(arguments.image)
    elif arguments.budget_mib is not None:
        raise ValueError("--budget-mib requires --image")
    if arguments.local_prefix is not None:
        response["compressed_bytes"] = local_sizes(arguments.local_prefix)
    print(json.dumps(response, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
