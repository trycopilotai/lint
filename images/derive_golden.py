#!/usr/bin/env python3
"""Derive candidate golden bytes from exact AMD64 formatter images."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import verify_cli


ROOT = Path(__file__).resolve().parents[1]


def build_image(target: str) -> str:
    image = f"lint-{target}:golden"
    completed = subprocess.run(
        [
            "docker",
            "buildx",
            "build",
            "--platform",
            "linux/amd64",
            "--load",
            "--file",
            "images/Dockerfile",
            "--target",
            target,
            "--tag",
            image,
            ".",
        ],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace")
        raise ValueError(f"could not build {target}: {detail}")
    return image


def run_image(
    image: str, directory: Path, filename: str
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
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
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def derive(language_id: str, image: str) -> dict[str, object]:
    fixture = verify_cli.FIXTURES[language_id]
    malformed = verify_cli.MALFORMED[language_id]
    with tempfile.TemporaryDirectory(
        prefix=".lint-work-derive-golden-",
        dir=ROOT,
    ) as directory:
        root = Path(directory).resolve()
        os.chmod(root, 0o777)
        path = root / fixture.name
        shutil.copyfile(fixture, path)
        os.chmod(path, 0o666)
        first = run_image(image, root, path.name)
        if first.returncode != 0:
            raise ValueError(first.stderr.decode("utf-8", errors="replace"))
        output = path.read_bytes()
        second = run_image(image, root, path.name)
        if second.returncode != 0 or path.read_bytes() != output:
            raise ValueError(f"unstable formatter output: {language_id}")

        malformed_root = root / "malformed"
        malformed_root.mkdir()
        malformed_path = malformed_root / fixture.name
        malformed_path.write_bytes(verify_cli.malformed_payload(language_id, malformed))
        os.chmod(malformed_path, 0o666)
        malformed_relative = malformed_path.relative_to(root).as_posix()
        malformed_result = run_image(image, root, malformed_relative)
        oversized = verify_cli.OVERSIZED_MALFORMED_LANGUAGES
        if language_id not in oversized and malformed_result.returncode == 0:
            raise ValueError(f"{language_id} image accepted malformed fixture")
        return {
            "expected_base64": base64.b64encode(output).decode("ascii"),
            "language": language_id,
            "malformed_image_returncode": malformed_result.returncode,
        }


def main() -> int:
    for row in verify_cli.load_matrix():
        target = row["target"]
        image = build_image(target)
        for language_id in row["languages"]:
            print(json.dumps(derive(language_id, image), sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
