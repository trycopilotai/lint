#!/usr/bin/env python3
"""Verify repository invariants that unit tests do not cover."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import re
import struct
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


def load_demo_generator():
    path = ROOT / "scripts" / "generate_demo.py"
    specification = importlib.util.spec_from_file_location(
        "demo_generator",
        path,
    )
    if specification is None:
        raise RuntimeError("could not create demo generator specification")
    if specification.loader is None:
        raise RuntimeError("demo generator specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
        if data["version"] != "0.1.4":
            raise ValueError(f"wrong plugin version: {manifest}")


def verify_demo() -> None:
    manifest_path = ROOT / "evidence" / "demo-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_paths = {
        "output": "evidence/demo-transcript.txt",
        "skill": "skills/lint/SKILL.md",
        "invocation_script": "scripts/demo.sh",
        "generator": "scripts/generate_demo.py",
        "demo": "assets/demo.svg",
    }
    for key, relative in expected_paths.items():
        path_key = f"{key}_path"
        hash_key = f"{key}_sha256"
        if manifest.get(path_key) != relative:
            raise ValueError(f"demo manifest has wrong {path_key}")
        if manifest.get(hash_key) != sha256(ROOT / relative):
            raise ValueError(f"demo manifest has stale {hash_key}")

    transcript = (ROOT / expected_paths["output"]).read_text(encoding="utf-8")
    generator = load_demo_generator()
    rendered = generator.render_svg(transcript)
    actual = (ROOT / expected_paths["demo"]).read_text(encoding="utf-8")
    if rendered != actual:
        raise ValueError("demo SVG does not derive from the transcript")

    preview = ROOT / "assets" / "social-preview.png"
    payload = preview.read_bytes()
    if payload[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("social preview must be a PNG")
    width, height = struct.unpack(">II", payload[16:24])
    if (width, height) != (1280, 640):
        raise ValueError("social preview must be 1280 by 640")


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
        if "github.event.repository.private" in text:
            raise ValueError(f"workflow is incorrectly gated by visibility: {path}")


def verify_release_surfaces() -> None:
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    if "private release candidate" in release.lower():
        raise ValueError("release metadata contains private-candidate wording")
    if release.count("uses: aquasecurity/trivy-action@") != 2:
        raise ValueError("release must scan both image platforms")
    for platform in ("linux/amd64", "linux/arm64"):
        marker = f"TRIVY_PLATFORM: {platform}"
        if release.count(marker) != 1:
            raise ValueError(f"release scan is missing {platform}")
    if "ignore-unfixed: true" in release:
        raise ValueError("release scan must not ignore unfixed findings")

    publishing = (ROOT / "docs" / "publishing.md").read_text(encoding="utf-8")
    normalized_publishing = " ".join(
        publishing.replace("**", "").replace("`", "").split()
    )
    required = (
        "Never make this repository object public directly",
        "Push only refs/heads/main and refs/tags/v0.1.4",
        "delete every Actions workflow run",
        "Social preview",
        "Private vulnerability reporting",
        "Report a vulnerability",
        "Change visibility",
        "anonymous pull",
        "Publish release",
    )
    for phrase in required:
        if phrase not in normalized_publishing:
            raise ValueError(f"publishing procedure is missing: {phrase}")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    archive_url = (
        "https://github.com/trycopilotai/lint/" "archive/refs/tags/v0.1.4.tar.gz"
    )
    if readme.count(archive_url) != 2:
        raise ValueError("public skill installs must use the release archive")
    if "gh api repos/trycopilotai/lint/tarball" in readme:
        raise ValueError("public skill installs must not require GitHub login")

    issue_surfaces = (
        ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml",
        ROOT / ".github" / "ISSUE_TEMPLATE" / "formatter.yml",
        ROOT / ".github" / "ISSUE_TEMPLATE" / "supply-chain.yml",
        ROOT / ".github" / "labels.yml",
    )
    for path in issue_surfaces:
        if not path.is_file():
            raise ValueError(f"issue launch surface is missing: {path}")

    labels = (ROOT / ".github" / "labels.yml").read_text(encoding="utf-8")
    for label in ("formatter", "supply-chain", "good first issue"):
        if f"name: {label}" not in labels:
            raise ValueError(f"label definition is missing: {label}")


def main() -> int:
    files = tracked_files()
    verify_python_style(files)
    verify_normal_names(files)
    verify_manifests()
    verify_demo()
    verifier = load_image_verifier()
    verifier.validate_coverage()
    verifier.validate_sources()
    verify_actions(files)
    verify_release_surfaces()
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
