#!/usr/bin/env python3
"""Confirm that one formatter image changes and stabilizes a fixture."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = {
    "black": ROOT / "fixtures" / "black" / "needs.py",
    "buildifier": ROOT / "fixtures" / "buildifier" / "BUILD",
    "clang": ROOT / "fixtures" / "clang" / "needs.c",
    "csharp": ROOT / "fixtures" / "csharp" / "needs.cs",
    "go": ROOT / "fixtures" / "go" / "needs.go",
    "java": ROOT / "fixtures" / "java" / "Needs.java",
    "julia": ROOT / "fixtures" / "julia" / "needs.jl",
    "kotlin": ROOT / "fixtures" / "kotlin" / "Needs.kt",
    "prettier": ROOT / "fixtures" / "prettier" / "needs.md",
    "requirements": ROOT / "fixtures" / "requirements" / "requirements.txt",
    "rust": ROOT / "fixtures" / "rust" / "needs.rs",
    "shfmt": ROOT / "fixtures" / "shfmt" / "needs.sh",
    "swift": ROOT / "fixtures" / "swift" / "needs.swift",
    "taplo": ROOT / "fixtures" / "taplo" / "needs.toml",
    "xml": ROOT / "fixtures" / "xml" / "needs.xml",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_image(image: str, directory: Path, filename: str) -> None:
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "128",
        "--memory",
        "512m",
        "--cpus",
        "2",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "--user",
        "65532:65532",
        "--mount",
        f"type=bind,src={directory},dst=/work",
        image,
        f"/work/{filename}",
    ]
    subprocess.run(command, check=True)


def main() -> int:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--target", required=True)
    argument_parser.add_argument("--image", required=True)
    arguments = argument_parser.parse_args()

    fixture = FIXTURES.get(arguments.target)
    if fixture is None:
        raise ValueError(f"no smoke fixture for target: {arguments.target}")
    with tempfile.TemporaryDirectory(
        prefix=".lint-work-image-smoke-",
        dir=ROOT,
    ) as directory:
        root = Path(directory).resolve()
        os.chmod(root, 0o777)
        path = root / fixture.name
        shutil.copyfile(fixture, path)
        os.chmod(path, 0o666)
        before = digest(path)
        run_image(arguments.image, root, path.name)
        after = digest(path)
        if before == after:
            raise ValueError("formatter did not change its fixture")
        run_image(arguments.image, root, path.name)
        stable = digest(path)
        if after != stable:
            raise ValueError("formatter output did not stabilize")
    print(f"{arguments.target}: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
