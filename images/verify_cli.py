#!/usr/bin/env python3
"""Verify local and Docker CLI transitions against committed output."""

from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import lint

sys.path.insert(0, str(ROOT / "images"))
FIXTURES = {
    "markdown": ROOT / "fixtures" / "prettier" / "needs.md",
    "html": ROOT / "fixtures" / "html" / "needs.html",
    "yaml": ROOT / "fixtures" / "yaml" / "needs.yaml",
    "json": ROOT / "fixtures" / "json" / "needs.json",
    "javascript": ROOT / "fixtures" / "javascript" / "needs.js",
    "typescript": ROOT / "fixtures" / "typescript" / "needs.ts",
    "tsx": ROOT / "fixtures" / "tsx" / "needs.tsx",
    "css": ROOT / "fixtures" / "css" / "needs.css",
    "scss": ROOT / "fixtures" / "scss" / "needs.scss",
    "less": ROOT / "fixtures" / "less" / "needs.less",
    "bazel": ROOT / "fixtures" / "buildifier" / "BUILD",
    "python": ROOT / "fixtures" / "black" / "needs.py",
    "requirements": ROOT / "fixtures" / "requirements" / "requirements.txt",
    "shell": ROOT / "fixtures" / "shfmt" / "needs.sh",
    "c": ROOT / "fixtures" / "clang" / "needs.c",
    "cpp": ROOT / "fixtures" / "cpp" / "needs.cpp",
    "objective-c": ROOT / "fixtures" / "objective-c" / "needs.m",
    "objective-cpp": ROOT / "fixtures" / "objective-cpp" / "needs.mm",
    "java": ROOT / "fixtures" / "java" / "Needs.java",
    "go": ROOT / "fixtures" / "go" / "needs.go",
    "rust": ROOT / "fixtures" / "rust" / "needs.rs",
    "kotlin": ROOT / "fixtures" / "kotlin" / "Needs.kt",
    "toml": ROOT / "fixtures" / "taplo" / "needs.toml",
    "xml": ROOT / "fixtures" / "xml" / "needs.xml",
    "plist": ROOT / "fixtures" / "plist" / "needs.plist",
    "swift": ROOT / "fixtures" / "swift" / "needs.swift",
    "csharp": ROOT / "fixtures" / "csharp" / "needs.cs",
    "julia": ROOT / "fixtures" / "julia" / "needs.jl",
}

EXPECTED = {
    "markdown": ROOT / "fixtures" / "prettier" / "expected.md",
    "html": ROOT / "fixtures" / "html" / "expected.html",
    "yaml": ROOT / "fixtures" / "yaml" / "expected.yaml",
    "json": ROOT / "fixtures" / "json" / "expected.json",
    "javascript": ROOT / "fixtures" / "javascript" / "expected.js",
    "typescript": ROOT / "fixtures" / "typescript" / "expected.ts",
    "tsx": ROOT / "fixtures" / "tsx" / "expected.tsx",
    "css": ROOT / "fixtures" / "css" / "expected.css",
    "scss": ROOT / "fixtures" / "scss" / "expected.scss",
    "less": ROOT / "fixtures" / "less" / "expected.less",
    "bazel": ROOT / "fixtures" / "buildifier" / "expected.bzl",
    "python": ROOT / "fixtures" / "black" / "expected.py",
    "requirements": ROOT / "fixtures" / "requirements" / "expected.txt",
    "shell": ROOT / "fixtures" / "shfmt" / "expected.sh",
    "c": ROOT / "fixtures" / "clang" / "expected.c",
    "cpp": ROOT / "fixtures" / "cpp" / "expected.cpp",
    "objective-c": ROOT / "fixtures" / "objective-c" / "expected.m",
    "objective-cpp": ROOT / "fixtures" / "objective-cpp" / "expected.mm",
    "java": ROOT / "fixtures" / "java" / "Expected.java",
    "go": ROOT / "fixtures" / "go" / "expected.go",
    "rust": ROOT / "fixtures" / "rust" / "expected.rs",
    "kotlin": ROOT / "fixtures" / "kotlin" / "Expected.kt",
    "toml": ROOT / "fixtures" / "taplo" / "expected.toml",
    "xml": ROOT / "fixtures" / "xml" / "expected.xml",
    "plist": ROOT / "fixtures" / "plist" / "expected.plist",
    "swift": ROOT / "fixtures" / "swift" / "expected.swift",
    "csharp": ROOT / "fixtures" / "csharp" / "expected.cs",
    "julia": ROOT / "fixtures" / "julia" / "expected.jl",
}

MALFORMED = {
    "markdown": ROOT / "fixtures" / "prettier" / "malformed.md",
    "html": ROOT / "fixtures" / "html" / "malformed.html",
    "yaml": ROOT / "fixtures" / "yaml" / "malformed.yaml",
    "json": ROOT / "fixtures" / "json" / "malformed.json",
    "javascript": ROOT / "fixtures" / "javascript" / "malformed.js",
    "typescript": ROOT / "fixtures" / "typescript" / "malformed.ts",
    "tsx": ROOT / "fixtures" / "tsx" / "malformed.tsx",
    "css": ROOT / "fixtures" / "css" / "malformed.css",
    "scss": ROOT / "fixtures" / "scss" / "malformed.scss",
    "less": ROOT / "fixtures" / "less" / "malformed.less",
    "bazel": ROOT / "fixtures" / "buildifier" / "malformed.bzl",
    "python": ROOT / "fixtures" / "black" / "malformed.txt",
    "requirements": ROOT / "fixtures" / "requirements" / "malformed.txt",
    "shell": ROOT / "fixtures" / "shfmt" / "malformed.sh",
    "c": ROOT / "fixtures" / "clang" / "malformed.c",
    "cpp": ROOT / "fixtures" / "cpp" / "malformed.cpp",
    "objective-c": ROOT / "fixtures" / "objective-c" / "malformed.m",
    "objective-cpp": ROOT / "fixtures" / "objective-cpp" / "malformed.mm",
    "java": ROOT / "fixtures" / "java" / "Malformed.java",
    "go": ROOT / "fixtures" / "go" / "malformed.go",
    "rust": ROOT / "fixtures" / "rust" / "malformed.rs",
    "kotlin": ROOT / "fixtures" / "kotlin" / "Malformed.kt",
    "toml": ROOT / "fixtures" / "taplo" / "malformed.toml",
    "xml": ROOT / "fixtures" / "xml" / "malformed.xml",
    "plist": ROOT / "fixtures" / "plist" / "malformed.plist",
    "swift": ROOT / "fixtures" / "swift" / "malformed.swift",
    "csharp": ROOT / "fixtures" / "csharp" / "malformed.cs",
    "julia": ROOT / "fixtures" / "julia" / "malformed.jl",
}

OVERSIZED_MALFORMED_LANGUAGES = frozenset(
    {
        "markdown",
        "requirements",
        "c",
        "cpp",
        "objective-c",
        "objective-cpp",
    }
)


@dataclasses.dataclass(frozen=True)
class CliResult:
    """One public CLI invocation and its parsed response."""

    returncode: int
    response: dict[str, Any]


Runner = Callable[[Path, str, str, bool], CliResult]


def parse_cli_result(returncode: int, stdout: str, stderr: str) -> CliResult:
    """Parse the public CLI's single JSON response."""
    payload = stdout.strip()
    if payload == "":
        payload = stderr.strip()
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError(f"CLI did not emit one JSON object: {payload}") from error
    if not isinstance(value, dict):
        raise ValueError("CLI response must be one JSON object")
    return CliResult(returncode=returncode, response=value)


def run_cli(
    directory: Path,
    filename: str,
    language_id: str,
    write: bool,
    backend: str,
) -> CliResult:
    """Run the public CLI through one selected backend."""
    command = [
        sys.executable,
        str(ROOT / "lint.py"),
        "--json",
        "--cwd",
        str(directory),
        "--language",
        language_id,
    ]
    if backend == "docker":
        command.append("--docker")
    elif backend != "local":
        raise ValueError(f"unknown backend: {backend}")
    if write:
        command.append("--write")
    else:
        command.append("--read-only")
    command.append(filename)
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return parse_cli_result(
        completed.returncode,
        completed.stdout,
        completed.stderr,
    )


def require_cli_result(
    result: CliResult,
    returncode: int,
    mode: str,
    status: str,
    backend: str,
) -> None:
    """Require one expected public CLI state."""
    if result.returncode != returncode:
        raise ValueError(
            f"CLI returned {result.returncode}, expected {returncode}: "
            f"{result.response}"
        )
    expected = {
        "backend": backend,
        "mode": mode,
        "status": status,
    }
    actual = {key: result.response.get(key) for key in expected}
    if actual != expected:
        raise ValueError(f"CLI response {actual} did not match {expected}")


def require_path_state(path: Path, payload: bytes, mode: int) -> None:
    """Require exact content and permission preservation."""
    if path.read_bytes() != payload:
        raise ValueError(f"unexpected content for {path}")
    actual_mode = stat.S_IMODE(path.stat().st_mode)
    if actual_mode != mode:
        raise ValueError(f"mode for {path} changed from {mode:o} to {actual_mode:o}")


def verify_cli_transitions(
    path: Path,
    language_id: str,
    expected: bytes,
    backend: str,
    runner: Runner = run_cli,
) -> None:
    """Require immutable read, transactional write, and clean reread."""
    original = path.read_bytes()
    original_mode = stat.S_IMODE(path.stat().st_mode)

    def invoke(write: bool) -> CliResult:
        if runner is run_cli:
            return run_cli(path.parent, path.name, language_id, write, backend)
        return runner(path.parent, path.name, language_id, write)

    first_read = invoke(False)
    require_cli_result(
        first_read,
        1,
        "read-only",
        "needs_formatting",
        backend,
    )
    require_path_state(path, original, original_mode)

    write = invoke(True)
    require_cli_result(write, 0, "write", "changed", backend)
    require_path_state(path, expected, original_mode)

    second_read = invoke(False)
    require_cli_result(second_read, 0, "read-only", "clean", backend)
    require_path_state(path, expected, original_mode)


def verify_malformed_input(
    path: Path,
    language_id: str,
    backend: str,
    runner: Runner = run_cli,
) -> None:
    """Require malformed input to fail without changing source."""
    original = path.read_bytes()
    original_mode = stat.S_IMODE(path.stat().st_mode)
    if runner is run_cli:
        result = run_cli(path.parent, path.name, language_id, False, backend)
    else:
        result = runner(path.parent, path.name, language_id, False)
    if result.returncode != 1:
        raise ValueError(
            f"malformed CLI returned {result.returncode}, expected 1: "
            f"{result.response}"
        )
    if result.response.get("status") != "formatter_error":
        raise ValueError(
            f"malformed CLI did not report formatter_error: {result.response}"
        )
    require_path_state(path, original, original_mode)


def malformed_payload(language_id: str, path: Path) -> bytes:
    """Load one syntax failure or construct a bounded-input failure."""
    payload = path.read_bytes()
    if language_id not in OVERSIZED_MALFORMED_LANGUAGES:
        return payload
    maximum = lint.limits()["max_file_bytes"]
    if payload == b"":
        raise ValueError(f"malformed boundary fixture is empty: {path}")
    repeats = (maximum // len(payload)) + 1
    return (payload * repeats)[: maximum + 1]


def load_matrix() -> list[dict[str, Any]]:
    """Load the image matrix rows."""
    with (ROOT / "images" / "matrix.json").open(encoding="utf-8") as handle:
        value = json.load(handle)
    rows = value.get("images")
    if not isinstance(rows, list):
        raise ValueError("image matrix is missing images")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("image matrix rows must be objects")
    return rows


def target_for_language(language_id: str) -> str:
    """Resolve one public alias to its image target."""
    matches: list[str] = []
    for row in load_matrix():
        languages = row.get("languages")
        target = row.get("target")
        if not isinstance(languages, list):
            raise ValueError("image languages must be a list")
        if not isinstance(target, str):
            raise ValueError("image target must be a string")
        if language_id in languages:
            matches.append(target)
    if len(matches) != 1:
        raise ValueError(f"language {language_id} maps to {len(matches)} image targets")
    return matches[0]


def public_image_for(language_id: str) -> str:
    """Return the exact image reference used by lint.py."""
    for language in lint.load_languages():
        if language.id == language_id:
            return lint.docker_image(language)
    raise ValueError(f"unknown language: {language_id}")


def inspect_architecture(image: str, expected: str) -> None:
    """Require the built image architecture selected by the job."""
    completed = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{.Architecture}}"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    actual = completed.stdout.strip()
    if actual != expected:
        raise ValueError(f"image architecture is {actual}, expected {expected}")


def existing_image_id(image: str) -> str | None:
    """Return an existing local tag target without pulling it."""
    completed = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def restore_image_tag(image: str, previous_id: str | None) -> None:
    """Remove the temporary public tag and restore any prior target."""
    subprocess.run(
        ["docker", "image", "rm", image],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    if previous_id is not None:
        subprocess.run(
            ["docker", "image", "tag", previous_id, image],
            check=True,
        )


def verify(
    target: str,
    language_id: str,
    backend: str,
    image: str | None,
    architecture: str | None,
) -> None:
    """Verify one emitted language alias through the real public CLI."""
    expected_target = target_for_language(language_id)
    if target != expected_target:
        raise ValueError(
            f"language {language_id} uses target {expected_target}, not {target}"
        )
    fixture = FIXTURES.get(language_id)
    if fixture is None:
        raise ValueError(f"no fixture for language: {language_id}")
    if not fixture.is_file():
        raise ValueError(f"fixture does not exist: {fixture}")
    expected_path = EXPECTED.get(language_id)
    if expected_path is None or not expected_path.is_file():
        raise ValueError(f"expected fixture does not exist: {expected_path}")
    malformed = MALFORMED.get(language_id)
    if malformed is None or not malformed.is_file():
        raise ValueError(f"malformed fixture does not exist: {malformed}")
    expected = expected_path.read_bytes()
    if fixture.read_bytes() == expected:
        raise ValueError(f"fixture is already clean: {fixture}")

    public_image: str | None = None
    previous_id: str | None = None
    if backend == "docker":
        if image is None or architecture is None:
            raise ValueError("Docker verification requires image and architecture")
        inspect_architecture(image, architecture)
        public_image = public_image_for(language_id)
        previous_id = existing_image_id(public_image)
        subprocess.run(
            ["docker", "image", "tag", image, public_image],
            check=True,
        )
    elif backend != "local":
        raise ValueError(f"unknown backend: {backend}")
    try:
        with tempfile.TemporaryDirectory(
            prefix=".lint-work-cli-public-",
            dir=ROOT,
        ) as directory:
            root = Path(directory).resolve()
            path = root / fixture.name
            shutil.copyfile(fixture, path)
            path.chmod(0o640)
            verify_cli_transitions(path, language_id, expected, backend)
            malformed_root = root / "malformed"
            malformed_root.mkdir()
            malformed_path = malformed_root / fixture.name
            malformed_path.write_bytes(malformed_payload(language_id, malformed))
            malformed_path.chmod(0o640)
            verify_malformed_input(malformed_path, language_id, backend)
    finally:
        if public_image is not None:
            restore_image_tag(public_image, previous_id)


def main() -> int:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--target", required=True)
    argument_parser.add_argument("--language", required=True)
    argument_parser.add_argument(
        "--backend",
        choices=("local", "docker"),
        required=True,
    )
    argument_parser.add_argument("--image")
    argument_parser.add_argument(
        "--architecture",
        choices=("amd64", "arm64"),
    )
    arguments = argument_parser.parse_args()
    verify(
        target=arguments.target,
        language_id=arguments.language,
        backend=arguments.backend,
        image=arguments.image,
        architecture=arguments.architecture,
    )
    result = {
        "backend": arguments.backend,
        "language": arguments.language,
        "status": "ok",
        "target": arguments.target,
    }
    if arguments.architecture is not None:
        result["architecture"] = arguments.architecture
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
