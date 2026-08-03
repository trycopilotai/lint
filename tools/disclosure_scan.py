#!/usr/bin/env python3
"""Scan repository names, content, and history for disclosure shapes."""

from __future__ import annotations

import argparse
import dataclasses
import os
import re
import subprocess
from pathlib import Path
from typing import Iterable, Sequence


PRUNED_DIRECTORIES = frozenset({".git", ".venv", "__pycache__", "node_modules"})
RESERVED_INFIX = "." + "g" + "p" + "t"


@dataclasses.dataclass(frozen=True)
class Finding:
    rule: str
    location: str

    def render(self) -> str:
        return f"{self.rule}: {self.location}"


def content_patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    return (
        (
            "private_key",
            re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE[ ]KEY-----"),
        ),
        (
            "local_absolute_path",
            re.compile(r"(?<![A-Za-z0-9])/(?:Users|home)/[A-Za-z0-9._-]+/"),
        ),
        (
            "github_token",
            re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
        ),
        (
            "openai_key",
            re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}"),
        ),
        (
            "aws_access_key",
            re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        ),
        (
            "json_web_token",
            re.compile(
                r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\." r"[A-Za-z0-9_-]{12,}\b"
            ),
        ),
    )


def content_findings(payload: bytes, location: str) -> list[Finding]:
    if b"\x00" in payload:
        return []
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return []

    findings: list[Finding] = []
    for rule, pattern in content_patterns():
        if pattern.search(text) is not None:
            findings.append(Finding(rule=rule, location=location))
    return findings


def path_findings(path: str, location: str) -> list[Finding]:
    findings: list[Finding] = []
    if RESERVED_INFIX in path:
        findings.append(Finding(rule="reserved_name", location=location))
    return findings


def walked_files(root: Path) -> Iterable[Path]:
    for directory, directory_names, file_names in os.walk(
        root,
        followlinks=False,
    ):
        retained: list[str] = []
        for name in directory_names:
            if name in PRUNED_DIRECTORIES:
                continue
            retained.append(name)
        directory_names[:] = retained
        for name in file_names:
            path = Path(directory) / name
            if path.is_symlink():
                continue
            if not path.is_file():
                continue
            yield path


def scan_tree(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in walked_files(root):
        relative = path.relative_to(root).as_posix()
        findings.extend(path_findings(relative, relative))
        findings.extend(content_findings(path.read_bytes(), relative))
    return sorted(findings, key=lambda finding: (finding.location, finding.rule))


def git_output(
    root: Path,
    arguments: Sequence[str],
    input_payload: bytes | None = None,
) -> bytes:
    command = ["git", "-C", str(root)]
    command.extend(arguments)
    completed = subprocess.run(
        command,
        check=True,
        input=input_payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout


def scan_history(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    objects = git_output(root, ["rev-list", "--objects", "--all"])
    object_ids: list[str] = []
    paths: dict[str, str] = {}
    for line in objects.splitlines():
        fields = line.split(b" ", 1)
        object_id = fields[0].decode("ascii")
        object_ids.append(object_id)
        if len(fields) == 2:
            path = fields[1].decode("utf-8", errors="surrogateescape")
            paths[object_id] = path
            location = f"history:{object_id}:{path}"
            findings.extend(path_findings(path, location))

    identifiers = ("\n".join(object_ids) + "\n").encode("ascii")
    checked = git_output(
        root,
        ["cat-file", "--batch-check=%(objectname) %(objecttype)"],
        input_payload=identifiers,
    )
    blob_ids: list[str] = []
    for line in checked.splitlines():
        object_id_bytes, object_type = line.split(b" ", 1)
        if object_type != b"blob":
            continue
        blob_ids.append(object_id_bytes.decode("ascii"))

    blob_request = ("\n".join(blob_ids) + "\n").encode("ascii")
    batch = git_output(
        root,
        ["cat-file", "--batch"],
        input_payload=blob_request,
    )
    offset = 0
    for expected_id in blob_ids:
        header_end = batch.index(b"\n", offset)
        header = batch[offset:header_end].split(b" ")
        object_id = header[0].decode("ascii")
        object_type = header[1]
        size = int(header[2])
        if object_id != expected_id:
            raise RuntimeError("Git batch returned objects out of order")
        if object_type != b"blob":
            raise RuntimeError("Git batch returned a non-blob object")
        payload_start = header_end + 1
        payload_end = payload_start + size
        payload = batch[payload_start:payload_end]
        offset = payload_end + 1
        location = f"history:{object_id}"
        path = paths.get(object_id)
        if path is not None:
            location = f"history:{object_id}:{path}"
        findings.extend(content_findings(payload, location))

    log = git_output(root, ["log", "--all", "--format=%H%x00%B%x00"])
    fields = log.split(b"\x00")
    for index in range(0, len(fields) - 1, 2):
        commit_id = fields[index].decode("ascii").strip()
        if commit_id == "":
            continue
        message = fields[index + 1]
        findings.extend(content_findings(message, f"commit:{commit_id}"))
    return sorted(findings, key=lambda finding: (finding.location, finding.rule))


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(prog="disclosure-scan")
    argument_parser.add_argument("--root", default=".")
    argument_parser.add_argument("--history", action="store_true")
    return argument_parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    root = Path(arguments.root).resolve()
    findings = scan_tree(root)
    if arguments.history:
        findings.extend(scan_history(root))
    if findings:
        for finding in findings:
            print(finding.render())
        return 1
    print("no disclosure findings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
