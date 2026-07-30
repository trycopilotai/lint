#!/usr/bin/env python3
"""Verify repository invariants that unit tests do not cover."""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_image_verifier():
    path = ROOT / "images" / "verify_images.py"
    specification = importlib.util.spec_from_file_location(
        "image_verifier",
        path,
    )
    if specification is None:
        raise RuntimeError("could not create image verifier specification")
    if specification.loader is None:
        raise RuntimeError("image verifier specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def tracked_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    )
    files: list[Path] = []
    for value in completed.stdout.split(b"\0"):
        if value == b"":
            continue
        relative = value.decode("utf-8")
        path = ROOT / relative
        if path.is_file():
            files.append(path)
    return files


def verify_python_style(files: list[Path]) -> None:
    for path in files:
        if path.suffix != ".py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.IfExp):
                raise ValueError(f"ternary expression: {path}:{node.lineno}")


def verify_normal_names(files: list[Path]) -> None:
    reserved = "." + "g" + "p" + "t"
    reserved_bytes = reserved.encode("ascii")
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        if reserved in relative:
            raise ValueError(f"reserved filename infix: {relative}")
        if reserved_bytes in path.read_bytes():
            raise ValueError(f"reserved reference infix: {relative}")


def verify_manifests() -> None:
    versions = json.loads((ROOT / "languages.json").read_text(encoding="utf-8"))[
        "tools"
    ]
    expected = {
        "prettier": "3.7.4",
        "black": "24.10.0",
        "clang-format": "18",
        "google-java-format": "1.35.0",
        "buildifier": "8.2.1",
        "ktlint": "1.3.0",
        "shfmt": "3.13.1",
        "go": "1.26.5",
        "rust": "1.97.1",
        "taplo": "0.10.0",
        "libxml2": "2.15.3",
        "swift-format": "603.0.0",
        "csharpier": "1.3.0",
        "julia": "1.12.6",
        "juliaformatter": "2.12.3",
        "node": "24.18.0",
        "python": "3.13.14",
    }
    if versions != expected:
        raise ValueError("tool version manifest differs from the release set")

    skill = ROOT / "skill"
    if not skill.is_symlink():
        raise ValueError("skill must be a symbolic link")
    if skill.resolve() != (ROOT / "skills" / "lint").resolve():
        raise ValueError("skill symbolic link has the wrong target")

    for manifest in (
        ROOT / ".claude-plugin" / "plugin.json",
        ROOT / ".codex-plugin" / "plugin.json",
    ):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data["version"] != "0.1.0":
            raise ValueError(f"wrong plugin version: {manifest}")


def verify_actions(files: list[Path]) -> None:
    workflows = [
        path for path in files if path.parent == ROOT / ".github" / "workflows"
    ]
    if not workflows:
        raise ValueError("GitHub Actions workflows are missing")
    action_reference = re.compile(r"uses:\s+[^@\s]+@([^\s#]+)")
    full_sha = re.compile(r"[0-9a-f]{40}")
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        for match in action_reference.finditer(text):
            reference = match.group(1)
            if full_sha.fullmatch(reference) is None:
                raise ValueError(
                    f"action is not pinned to a commit: {path}: {reference}"
                )
        if re.search(r"^\s*pull_request_target\s*:", text, re.MULTILINE):
            raise ValueError(f"pull_request_target is not allowed: {path}")


def main() -> int:
    files = tracked_files()
    verify_python_style(files)
    verify_normal_names(files)
    verify_manifests()
    verifier = load_image_verifier()
    verifier.validate_coverage()
    verifier.validate_sources()
    verify_actions(files)
    print(
        json.dumps(
            {
                "status": "ok",
                "tracked_files": len(files),
                "languages": 26,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
