#!/usr/bin/env python3
"""Translate typed composite-action inputs into the lint CLI."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path


READ_MODES = frozenset({"read-only", "readonly", "check"})
WRITE_MODES = frozenset({"write", "apply", "fix"})


def boolean_input(name: str, default: str = "false") -> bool:
    value = os.environ.get(name, default).strip().lower()
    if value in {"true", "1", "yes"}:
        return True
    if value in {"false", "0", "no", ""}:
        return False
    raise ValueError(f"{name} must be true or false")


def command() -> list[str]:
    action_path = Path(os.environ["GITHUB_ACTION_PATH"])
    arguments = [
        sys.executable,
        str(action_path / "lint.py"),
        "--cwd",
        os.environ.get("LINT_INPUT_CWD", "."),
    ]

    mode = os.environ.get("LINT_INPUT_MODE", "read-only")
    if mode in READ_MODES:
        arguments.append("--read-only")
    elif mode in WRITE_MODES:
        arguments.append("--write")
    else:
        raise ValueError(f"unsupported mode: {mode}")

    paths = os.environ.get("LINT_INPUT_PATHS", "")
    files_from0 = os.environ.get("LINT_INPUT_FILES_FROM0", "")
    modified = boolean_input("LINT_INPUT_MODIFIED")
    if paths != "":
        arguments.extend(shlex.split(paths))
    elif files_from0 != "":
        arguments.extend(["--files-from0", files_from0])
    elif modified:
        arguments.append("--modified")
    else:
        arguments.append("--all")

    if boolean_input("LINT_INPUT_DOCKER", default="true"):
        arguments.append("--docker")

    languages = os.environ.get("LINT_INPUT_LANGUAGES", "")
    for language in languages.split(","):
        language = language.strip()
        if language == "":
            continue
        arguments.extend(["--language", language])
    return arguments


def main() -> int:
    try:
        completed = subprocess.run(command(), check=False)
    except (KeyError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
