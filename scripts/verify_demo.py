#!/usr/bin/env python3
"""Build and verify the reproducible demo evidence manifest."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "evidence" / "demo-manifest.json"
TRANSCRIPT_PATH = ROOT / "evidence" / "demo-transcript.txt"
PROTOCOL_PATH = "skills/lint/SKILL.md"
OUTPUT_PATH = "evidence/demo-transcript.txt"
SOURCE_PATHS = (
    "languages.json",
    "lint.py",
    "scripts/demo.sh",
    "scripts/generate_demo.py",
    PROTOCOL_PATH,
)
ARTIFACT_PATHS = (
    "assets/demo.svg",
    OUTPUT_PATH,
)
RAW_RESULTS = ("read-only", "write", "final")
ALLOWED_EVIDENCE_DELTA = frozenset(
    {
        "assets/demo.svg",
        "evidence/demo-manifest.json",
        OUTPUT_PATH,
    }
)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def git(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(ROOT), *arguments],
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def git_text(*arguments: str) -> str:
    return git(*arguments).stdout.decode("utf-8").strip()


def is_git_root() -> bool:
    completed = git("rev-parse", "--show-toplevel", check=False)
    if completed.returncode != 0:
        return False
    reported = Path(completed.stdout.decode("utf-8").strip()).resolve()
    return reported == ROOT.resolve()


def commit_bytes(commit: str, path: str) -> bytes:
    return git("show", f"{commit}:{path}").stdout


def raw_result_bytes(transcript: str, name: str) -> bytes:
    marker = f"$ cat demo-work/{name}.json\n"
    start = transcript.find(marker)
    if start < 0:
        raise ValueError(f"demo transcript is missing {name}.json")
    payload = transcript[start + len(marker) :]
    _, end = json.JSONDecoder().raw_decode(payload)
    return payload[:end].encode("utf-8")


def file_hashes(paths: tuple[str, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in paths:
        result[path] = sha256((ROOT / path).read_bytes())
    return result


def load_demo_generator():
    path = ROOT / "scripts" / "generate_demo.py"
    specification = importlib.util.spec_from_file_location(
        "verified_demo_generator",
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


def safe_extract(payload: bytes, destination: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe demo archive path: {member.name}")
            if member.isdev():
                raise ValueError(f"unsupported demo archive entry: {member.name}")
            if member.issym() or member.islnk():
                target = PurePosixPath(member.linkname)
                if target.is_absolute() or ".." in target.parts:
                    raise ValueError(f"unsafe demo archive link: {member.name}")
        archive.extractall(destination)


def copy_current_source(destination: Path) -> None:
    ignored = shutil.ignore_patterns(
        ".git",
        ".lint-work-*",
        "__pycache__",
        "demo-work",
        "node_modules",
    )
    shutil.copytree(
        ROOT,
        destination,
        dirs_exist_ok=True,
        ignore=ignored,
        symlinks=True,
    )


def run_demo(input_commit: str | None, use_git: bool) -> bytes:
    with tempfile.TemporaryDirectory(prefix="lint-demo-verify-") as directory:
        checkout = Path(directory) / "lint"
        checkout.mkdir()
        if use_git:
            if input_commit is None:
                raise ValueError("Git demo verification requires an input commit")
            archive = git("archive", "--format=tar", input_commit).stdout
            safe_extract(archive, checkout)
        else:
            copy_current_source(checkout)
        completed = subprocess.run(
            ["./scripts/demo.sh", "demo-work"],
            cwd=checkout,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise ValueError(f"demo replay failed: {detail}")
        return completed.stdout


def build_manifest(
    input_commit: str,
    agent: str,
    agent_version: str,
    invocation_date: str,
) -> dict[str, Any]:
    input_tree = git_text("rev-parse", f"{input_commit}^{{tree}}")
    source_hashes: dict[str, str] = {}
    for path in SOURCE_PATHS:
        source_hashes[path] = sha256(commit_bytes(input_commit, path))
    transcript = TRANSCRIPT_PATH.read_text(encoding="utf-8")
    raw_hashes: dict[str, str] = {}
    for name in RAW_RESULTS:
        raw_hashes[name] = sha256(raw_result_bytes(transcript, name))
    artifact_hashes = file_hashes(ARTIFACT_PATHS)
    return {
        "schema_version": 2,
        "agent": agent,
        "agent_version": agent_version,
        "input_commit": input_commit,
        "input_tree": input_tree,
        "invocation": "./scripts/demo.sh demo-work",
        "invocation_date": invocation_date,
        "transcript_edited": False,
        "output_path": OUTPUT_PATH,
        "output_sha256": artifact_hashes[OUTPUT_PATH],
        "protocol_path": PROTOCOL_PATH,
        "protocol_sha256": source_hashes[PROTOCOL_PATH],
        "source_sha256": source_hashes,
        "artifact_sha256": artifact_hashes,
        "raw_result_sha256": raw_hashes,
    }


def load_manifest() -> dict[str, Any]:
    value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("demo manifest must contain an object")
    return value


def require_hash_map(
    manifest: dict[str, Any],
    key: str,
    expected_paths: tuple[str, ...],
) -> dict[str, str]:
    value = manifest.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"demo manifest {key} must be an object")
    if set(value) != set(expected_paths):
        raise ValueError(f"demo manifest {key} paths differ")
    result: dict[str, str] = {}
    for path, digest in value.items():
        if not isinstance(path, str):
            raise ValueError(f"demo manifest {key} path must be a string")
        if not isinstance(digest, str):
            raise ValueError(f"demo manifest {key} digest must be a string")
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError(f"demo manifest {key} digest is invalid")
        result[path] = digest
    return result


def changed_paths(input_commit: str) -> set[str]:
    committed = git_text("diff", "--name-only", f"{input_commit}..HEAD")
    working = git_text("diff", "--name-only", input_commit)
    result: set[str] = set()
    for output in (committed, working):
        for path in output.splitlines():
            if path != "":
                result.add(path)
    return result


def verify_static_fields(manifest: dict[str, Any]) -> str:
    if manifest.get("schema_version") != 2:
        raise ValueError("demo manifest schema_version must be 2")
    for key in ("agent", "agent_version"):
        value = manifest.get(key)
        if not isinstance(value, str) or value.strip() == "":
            raise ValueError(f"demo manifest {key} must be a non-empty string")
    invocation_date = manifest.get("invocation_date")
    if not isinstance(invocation_date, str):
        raise ValueError("demo manifest invocation_date must be a string")
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", invocation_date) is None:
        raise ValueError("demo manifest invocation_date must be an ISO date")
    input_commit = manifest.get("input_commit")
    if not isinstance(input_commit, str):
        raise ValueError("demo manifest input_commit must be a string")
    if re.fullmatch(r"[0-9a-f]{40}", input_commit) is None:
        raise ValueError("demo manifest input_commit must be a full commit id")
    input_tree = manifest.get("input_tree")
    if not isinstance(input_tree, str):
        raise ValueError("demo manifest input_tree must be a string")
    if re.fullmatch(r"[0-9a-f]{40}", input_tree) is None:
        raise ValueError("demo manifest input_tree must be a full tree id")
    if manifest.get("invocation") != "./scripts/demo.sh demo-work":
        raise ValueError("demo invocation is not canonical")
    if manifest.get("transcript_edited") is not False:
        raise ValueError("demo transcript must be raw command output")
    if manifest.get("output_path") != OUTPUT_PATH:
        raise ValueError("demo output path is not canonical")
    if manifest.get("protocol_path") != PROTOCOL_PATH:
        raise ValueError("demo protocol path is not canonical")
    return input_commit


def verify_manifest(manifest: dict[str, Any]) -> None:
    input_commit = verify_static_fields(manifest)
    source_hashes = require_hash_map(manifest, "source_sha256", SOURCE_PATHS)
    artifact_hashes = require_hash_map(manifest, "artifact_sha256", ARTIFACT_PATHS)
    raw_hashes = require_hash_map(manifest, "raw_result_sha256", RAW_RESULTS)
    use_git = is_git_root()

    if use_git:
        ancestry = git("merge-base", "--is-ancestor", input_commit, "HEAD", check=False)
        if ancestry.returncode != 0:
            raise ValueError("demo input commit is not reachable from HEAD")
        expected_tree = git_text("rev-parse", f"{input_commit}^{{tree}}")
        if manifest.get("input_tree") != expected_tree:
            raise ValueError("demo input tree does not match the input commit")
        for path, expected in source_hashes.items():
            committed = commit_bytes(input_commit, path)
            if sha256(committed) != expected:
                raise ValueError(f"demo source hash differs at input commit: {path}")
            if (ROOT / path).read_bytes() != committed:
                raise ValueError(f"demo source changed after capture: {path}")
        unexpected = changed_paths(input_commit) - ALLOWED_EVIDENCE_DELTA
        if unexpected:
            joined = ", ".join(sorted(unexpected))
            raise ValueError(f"demo evidence commit contains unrelated paths: {joined}")
    else:
        for path, expected in source_hashes.items():
            if sha256((ROOT / path).read_bytes()) != expected:
                raise ValueError(f"demo source hash differs: {path}")

    for path, expected in artifact_hashes.items():
        if sha256((ROOT / path).read_bytes()) != expected:
            raise ValueError(f"demo artifact hash differs: {path}")
    if manifest.get("output_sha256") != artifact_hashes[OUTPUT_PATH]:
        raise ValueError("demo output hash differs from the artifact record")
    if manifest.get("protocol_sha256") != source_hashes[PROTOCOL_PATH]:
        raise ValueError("demo protocol hash differs from the source record")

    transcript_bytes = TRANSCRIPT_PATH.read_bytes()
    transcript = transcript_bytes.decode("utf-8")
    previous = -1
    for name in RAW_RESULTS:
        expected = raw_hashes[name]
        marker = f"$ cat demo-work/{name}.json\n"
        position = transcript.find(marker)
        if position <= previous:
            raise ValueError("demo raw results are out of order")
        previous = position
        if sha256(raw_result_bytes(transcript, name)) != expected:
            raise ValueError(f"demo raw result hash differs: {name}")

    replayed = run_demo(input_commit, use_git)
    if replayed != transcript_bytes:
        raise ValueError("demo transcript differs from a clean replay")
    generator = load_demo_generator()
    rendered = generator.render_svg(transcript).encode("utf-8")
    if rendered != (ROOT / "assets" / "demo.svg").read_bytes():
        raise ValueError("demo SVG does not derive from the transcript")


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--write", action="store_true")
    argument_parser.add_argument("--input-commit")
    argument_parser.add_argument("--agent")
    argument_parser.add_argument("--agent-version")
    argument_parser.add_argument("--invocation-date")
    return argument_parser


def required_argument(name: str, value: str | None) -> str:
    if value is None or value.strip() == "":
        raise ValueError(f"--{name} is required with --write")
    return value


def main() -> int:
    arguments = parser().parse_args()
    if arguments.write:
        input_commit = arguments.input_commit
        if input_commit is None:
            input_commit = git_text("rev-parse", "HEAD")
        manifest = build_manifest(
            input_commit=input_commit,
            agent=required_argument("agent", arguments.agent),
            agent_version=required_argument("agent-version", arguments.agent_version),
            invocation_date=required_argument(
                "invocation-date",
                arguments.invocation_date,
            ),
        )
        MANIFEST_PATH.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    verify_manifest(load_manifest())
    print("demo evidence: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
