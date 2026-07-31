#!/usr/bin/env python3
"""Read-only-by-default, multi-language formatting."""

from __future__ import annotations

import argparse
import base64
import dataclasses
import fnmatch
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "languages.json"
CONTAINER_MARKER = Path("/app/.lint-container")
EXIT_CLEAN = 0
EXIT_FORMATTING = 1
EXIT_SELECTION = 2
EXIT_INTERNAL = 3
PRUNED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".venv",
        "__pycache__",
        "node_modules",
        "vendor",
    }
)


class SelectionError(Exception):
    """The requested file selection is invalid."""


class FormatterError(Exception):
    """A formatter could not safely produce output."""


class EngineError(FormatterError):
    """A required formatter engine is unavailable."""


@dataclasses.dataclass(frozen=True)
class Language:
    id: str
    family: str
    extensions: tuple[str, ...]
    filenames: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class Finding:
    path: str
    language: str
    status: str
    message: str | None = None

    def as_dict(self) -> dict[str, str]:
        result = {
            "path": self.path,
            "language": self.language,
            "status": self.status,
        }
        if self.message is not None:
            result["message"] = self.message
        return result


@dataclasses.dataclass(frozen=True)
class FormatResult:
    path: Path
    relative_path: str
    language: Language
    original: bytes
    formatted: bytes
    mode: int

    @property
    def changed(self) -> bool:
        return self.original != self.formatted


def load_manifest() -> dict[str, Any]:
    with MANIFEST_PATH.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise FormatterError("language manifest must contain an object")
    return value


def load_languages() -> tuple[Language, ...]:
    manifest = load_manifest()
    raw_languages = manifest.get("languages")
    if not isinstance(raw_languages, list):
        raise FormatterError("language manifest is missing languages")

    languages: list[Language] = []
    for item in raw_languages:
        if not isinstance(item, dict):
            raise FormatterError("language entries must be objects")
        language_id = item.get("id")
        family = item.get("family")
        extensions = item.get("extensions")
        filenames = item.get("filenames")
        if not isinstance(language_id, str):
            raise FormatterError("language id must be a string")
        if not isinstance(family, str):
            raise FormatterError("language family must be a string")
        if not isinstance(extensions, list):
            raise FormatterError("language extensions must be a list")
        if not isinstance(filenames, list):
            raise FormatterError("language filenames must be a list")
        languages.append(
            Language(
                id=language_id,
                family=family,
                extensions=tuple(str(value) for value in extensions),
                filenames=tuple(str(value) for value in filenames),
            )
        )
    return tuple(languages)


def tool_versions() -> dict[str, str]:
    manifest = load_manifest()
    raw_tools = manifest.get("tools")
    if not isinstance(raw_tools, dict):
        raise FormatterError("language manifest is missing tools")
    versions: dict[str, str] = {}
    for key, value in raw_tools.items():
        if not isinstance(key, str):
            raise FormatterError("tool names must be strings")
        if not isinstance(value, str):
            raise FormatterError("tool versions must be strings")
        versions[key] = value
    return versions


def limits() -> dict[str, int]:
    manifest = load_manifest()
    raw_limits = manifest.get("limits")
    if not isinstance(raw_limits, dict):
        raise FormatterError("language manifest is missing limits")
    parsed: dict[str, int] = {}
    for key, value in raw_limits.items():
        if not isinstance(key, str):
            raise FormatterError("limit names must be strings")
        if not isinstance(value, int):
            raise FormatterError("limit values must be integers")
        parsed[key] = value
    return parsed


def detect_language(path: Path, languages: Sequence[Language]) -> Language | None:
    name = path.name
    for language in languages:
        for pattern in language.filenames:
            if fnmatch.fnmatchcase(name, pattern):
                return language
    suffix = path.suffix.lower()
    for language in languages:
        if suffix in language.extensions:
            return language
    return None


def run_git(cwd: Path, arguments: Sequence[str]) -> bytes:
    command = ["git", "-C", str(cwd)]
    command.extend(arguments)
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise SelectionError(detail)
    return completed.stdout


def git_root(cwd: Path) -> Path | None:
    completed = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if completed.returncode != 0:
        return None
    return Path(completed.stdout.strip()).resolve()


def decode_nul_paths(output: bytes) -> list[str]:
    paths: list[str] = []
    for item in output.split(b"\0"):
        if item == b"":
            continue
        paths.append(item.decode("utf-8", errors="surrogateescape"))
    return paths


def git_paths(cwd: Path, modified: bool) -> list[Path]:
    cwd = cwd.resolve()
    repository = git_root(cwd)
    if repository is None:
        if modified:
            raise SelectionError("--modified requires a Git working tree")
        return walked_paths(cwd)

    prefix = cwd.relative_to(repository).as_posix()
    pathspec = "."
    if prefix != ".":
        pathspec = prefix

    names: list[str] = []
    if modified:
        commands = (
            ["diff", "--name-only", "-z", "--diff-filter=ACMRTUXB", "--", pathspec],
            [
                "diff",
                "--cached",
                "--name-only",
                "-z",
                "--diff-filter=ACMRTUXB",
                "--",
                pathspec,
            ],
        )
        for command in commands:
            names.extend(decode_nul_paths(run_git(repository, command)))
    else:
        names.extend(
            decode_nul_paths(
                run_git(
                    repository,
                    [
                        "ls-files",
                        "-z",
                        "--cached",
                        "--others",
                        "--exclude-standard",
                        "--",
                        pathspec,
                    ],
                )
            )
        )

    selected: list[Path] = []
    for name in sorted(set(names)):
        candidate = repository / name
        if not candidate.exists():
            continue
        try:
            candidate.relative_to(cwd)
        except ValueError:
            continue
        selected.append(candidate)
    return selected


def walked_paths(cwd: Path) -> list[Path]:
    selected: list[Path] = []
    for directory, directory_names, file_names in os.walk(
        cwd,
        followlinks=False,
    ):
        filtered_names: list[str] = []
        for name in directory_names:
            path = Path(directory) / name
            if name in PRUNED_DIRECTORIES:
                continue
            if path.is_symlink():
                continue
            filtered_names.append(name)
        directory_names[:] = filtered_names
        for name in file_names:
            path = Path(directory) / name
            if path.is_symlink():
                continue
            selected.append(path)
    selected.sort()
    return selected


def validate_explicit_path(cwd: Path, raw_path: str) -> Path:
    if "\x00" in raw_path:
        raise SelectionError("paths must not contain NUL bytes")
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = cwd / candidate
    lexical = Path(os.path.abspath(candidate))
    try:
        relative = lexical.relative_to(cwd)
    except ValueError as error:
        raise SelectionError(f"path escapes --cwd: {raw_path}") from error
    current = cwd
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise SelectionError(f"symbolic links are not accepted: {raw_path}")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as error:
        raise SelectionError(f"path does not exist: {raw_path}") from error
    try:
        resolved.relative_to(cwd)
    except ValueError as error:
        raise SelectionError(f"path escapes --cwd: {raw_path}") from error
    return resolved


def expand_explicit_path(cwd: Path, raw_path: str) -> list[Path]:
    path = validate_explicit_path(cwd, raw_path)
    if path.is_file():
        return [path]
    if path.is_dir():
        return walked_paths(path)
    raise SelectionError(f"path is not a regular file or directory: {raw_path}")


def read_files_from0(source: str) -> list[str]:
    if source == "-":
        payload = sys.stdin.buffer.read()
    else:
        payload = Path(source).read_bytes()
    return decode_nul_paths(payload)


def select_paths(
    cwd: Path,
    explicit_paths: Sequence[str],
    files_from0: str | None,
    modified: bool,
) -> list[Path]:
    raw_paths = list(explicit_paths)
    if files_from0 is not None:
        raw_paths.extend(read_files_from0(files_from0))
    if raw_paths:
        paths: list[Path] = []
        for raw_path in raw_paths:
            paths.extend(expand_explicit_path(cwd, raw_path))
        return sorted(set(paths))
    return git_paths(cwd, modified=modified)


def command_for(language: Language, path: Path) -> list[str]:
    versions = tool_versions()
    family = language.family
    if family == "prettier":
        executable = [
            "npx",
            "-y",
            f"prettier@{versions['prettier']}",
        ]
        if CONTAINER_MARKER.is_file():
            executable = ["prettier"]
        executable.extend(
            [
                "--write",
                "--no-config",
                "--no-editorconfig",
                "--ignore-path",
                str(path.parent / ".lint-empty-ignore"),
                "--print-width",
                "60",
                "--prose-wrap",
                "always",
                "--trailing-comma",
                "none",
                str(path),
            ]
        )
        return executable
    if family == "buildifier":
        executable = [
            "npx",
            "-y",
            f"@bazel/buildifier@{versions['buildifier']}",
        ]
        if CONTAINER_MARKER.is_file():
            executable = ["buildifier"]
        executable.extend(["-mode=fix", str(path)])
        return executable
    if family == "black":
        return [
            "black",
            "--quiet",
            "--line-length",
            "88",
            str(path),
        ]
    if family == "shfmt":
        return ["shfmt", "-w", "-i", "2", "-ci", str(path)]
    if family == "clang":
        return ["clang-format", "-i", str(path)]
    if family == "java":
        return ["google-java-format", "--replace", str(path)]
    if family == "go":
        return ["gofmt", "-w", str(path)]
    if family == "rust":
        return ["rustfmt", str(path)]
    if family == "kotlin":
        return ["ktlint", "--format", str(path)]
    if family == "toml":
        return ["taplo", "format", str(path)]
    if family == "xml":
        return ["xmllint", "--format", "--output", str(path), str(path)]
    if family == "swift":
        return ["swift-format", "format", "--in-place", str(path)]
    if family == "csharp":
        return ["csharpier", "format", "--no-cache", str(path)]
    if family == "julia":
        expression = "using JuliaFormatter; " "format_file(ARGS[1], overwrite=true)"
        return [
            "julia",
            "--startup-file=no",
            "-e",
            expression,
            "--",
            str(path),
        ]
    raise FormatterError(f"unsupported formatter family: {family}")


def version_command(language: Language) -> tuple[list[str], str] | None:
    versions = tool_versions()
    family = language.family
    if family in {"prettier", "buildifier", "requirements"}:
        return None
    if family == "black":
        return ["black", "--version"], versions["black"]
    if family == "shfmt":
        return ["shfmt", "--version"], versions["shfmt"]
    if family == "clang":
        return ["clang-format", "--version"], "version 18"
    if family == "java":
        return ["google-java-format", "--version"], versions["google-java-format"]
    if family == "go":
        return ["go", "version"], f"go{versions['go']}"
    if family == "rust":
        return ["rustc", "--version"], f"rustc {versions['rust']}"
    if family == "kotlin":
        return ["ktlint", "--version"], versions["ktlint"]
    if family == "toml":
        return ["taplo", "--version"], versions["taplo"]
    if family == "xml":
        return ["xmllint", "--version"], "21503"
    if family == "swift":
        return ["swift-format", "--version"], versions["swift-format"]
    if family == "csharp":
        return ["csharpier", "--version"], versions["csharpier"]
    if family == "julia":
        return ["julia", "--version"], versions["julia"]
    raise FormatterError(f"unsupported formatter family: {family}")


def verify_formatter_version(language: Language) -> None:
    request = version_command(language)
    if request is None:
        return
    command, expected = request
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
    except FileNotFoundError as error:
        raise EngineError(
            f"{command[0]} is not installed; use --docker or install "
            "the pinned version from languages.json"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise FormatterError(
            f"{command[0]} version check exceeded 10 seconds"
        ) from error
    output = completed.stdout.decode(
        "utf-8", errors="replace"
    ) + completed.stderr.decode("utf-8", errors="replace")
    if completed.returncode != 0 or expected not in output:
        found = output.strip()
        if found == "":
            found = f"exit {completed.returncode}"
        raise EngineError(f"{command[0]} must match {expected}; found {found}")


def requirements_output(payload: bytes) -> bytes:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise FormatterError("requirements file is not UTF-8") from error

    sections: list[list[str]] = [[]]
    for line in text.splitlines():
        if line.strip() == "":
            if sections[-1]:
                sections.append([])
            continue
        sections[-1].append(line.rstrip())

    output_sections: list[str] = []
    for section in sections:
        if not section:
            continue
        comments: list[str] = []
        requirements: list[str] = []
        for line in section:
            if line.lstrip().startswith(("#", "-", "http://", "https://")):
                comments.append(line)
            else:
                requirements.append(line)
        requirements.sort(key=str.casefold)
        output_sections.append("\n".join(comments + requirements))
    if not output_sections:
        return b""
    return ("\n\n".join(output_sections) + "\n").encode("utf-8")


def run_formatter(
    language: Language,
    path: Path,
    cwd: Path,
    timeout_seconds: int,
) -> None:
    if language.family == "requirements":
        path.write_bytes(requirements_output(path.read_bytes()))
        return
    if language.family == "prettier":
        (path.parent / ".lint-empty-ignore").touch()
    command = command_for(language, path)
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as error:
        executable = command[0]
        raise EngineError(
            f"{executable} is not installed; use --docker or install "
            "the pinned version from languages.json"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise FormatterError(
            f"{language.id} formatter exceeded {timeout_seconds} seconds"
        ) from error
    if completed.returncode != 0:
        standard_error = completed.stderr.decode(
            "utf-8",
            errors="replace",
        ).strip()
        standard_output = completed.stdout.decode(
            "utf-8",
            errors="replace",
        ).strip()
        detail = standard_error
        if detail == "":
            detail = standard_output
        if detail == "":
            detail = f"formatter exited {completed.returncode}"
        raise FormatterError(detail)


def docker_image(language: Language) -> str:
    return f"ghcr.io/trycopilotai/lint-{language.id}:0.1.0"


def run_docker_formatter(
    language: Language,
    mirror_root: Path,
    relative_path: Path,
    timeout_seconds: int,
) -> None:
    image = docker_image(language)
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
        f"type=bind,src={mirror_root},dst=/work",
        image,
        f"/work/{relative_path.as_posix()}",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as error:
        raise EngineError("docker is not installed") from error
    except subprocess.TimeoutExpired as error:
        raise FormatterError(
            f"{language.id} container exceeded {timeout_seconds} seconds"
        ) from error
    if completed.returncode != 0:
        detail = completed.stderr.decode(
            "utf-8",
            errors="replace",
        ).strip()
        if detail == "":
            detail = completed.stdout.decode(
                "utf-8",
                errors="replace",
            ).strip()
        raise FormatterError(f"{image}: {detail}")


def prepare_results(
    cwd: Path,
    paths: Sequence[Path],
    requested_languages: frozenset[str],
    use_docker: bool,
) -> tuple[list[FormatResult], list[Finding]]:
    known_languages = load_languages()
    language_ids = frozenset(language.id for language in known_languages)
    unknown = requested_languages - language_ids
    if unknown:
        values = ", ".join(sorted(unknown))
        raise SelectionError(f"unknown language: {values}")

    selected: list[tuple[Path, Language]] = []
    findings: list[Finding] = []
    for path in paths:
        language = detect_language(path, known_languages)
        relative_path = path.relative_to(cwd).as_posix()
        if language is None:
            findings.append(
                Finding(
                    path=relative_path,
                    language="unknown",
                    status="skipped",
                    message="unsupported file type",
                )
            )
            continue
        if requested_languages and language.id not in requested_languages:
            continue
        if path.is_symlink():
            raise SelectionError(f"symbolic links are not accepted: {relative_path}")
        if not path.is_file():
            raise SelectionError(f"not a regular file: {relative_path}")
        selected.append((path, language))

    request_limits = limits()
    maximum_bytes = request_limits["max_file_bytes"]
    timeout_seconds = request_limits["timeout_seconds_per_file"]
    results: list[FormatResult] = []
    verified_families: set[str] = set()
    workspace = tempfile.mkdtemp(prefix="lint-work-")
    mirror_root = Path(workspace)
    os.chmod(mirror_root, 0o777)
    try:
        for path, language in selected:
            relative = path.relative_to(cwd)
            payload = path.read_bytes()
            if len(payload) > maximum_bytes:
                raise FormatterError(f"{relative}: exceeds {maximum_bytes} bytes")
            mirror_path = mirror_root / relative
            mirror_path.parent.mkdir(parents=True, exist_ok=True)
            current = mirror_path.parent
            while current != mirror_root:
                os.chmod(current, 0o777)
                current = current.parent
            mirror_path.write_bytes(payload)
            source_mode = stat.S_IMODE(path.stat().st_mode)
            mirror_mode = source_mode | 0o666
            os.chmod(mirror_path, mirror_mode)
            if use_docker:
                run_docker_formatter(
                    language,
                    mirror_root,
                    relative,
                    timeout_seconds,
                )
            else:
                if language.family not in verified_families:
                    verify_formatter_version(language)
                    verified_families.add(language.family)
                run_formatter(
                    language,
                    mirror_path,
                    mirror_root,
                    timeout_seconds,
                )
            results.append(
                FormatResult(
                    path=path,
                    relative_path=relative.as_posix(),
                    language=language,
                    original=payload,
                    formatted=mirror_path.read_bytes(),
                    mode=source_mode,
                )
            )
    finally:
        shutil.rmtree(mirror_root)
    return results, findings


def atomic_write(path: Path, payload: bytes, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def apply_results(results: Sequence[FormatResult]) -> None:
    written: list[FormatResult] = []
    try:
        for result in results:
            if not result.changed:
                continue
            atomic_write(result.path, result.formatted, result.mode)
            written.append(result)
    except OSError as error:
        rollback_errors: list[str] = []
        for result in reversed(written):
            try:
                atomic_write(result.path, result.original, result.mode)
            except OSError as rollback_error:
                rollback_errors.append(str(rollback_error))
        detail = str(error)
        if rollback_errors:
            detail = f"{detail}; rollback failures: {'; '.join(rollback_errors)}"
        raise FormatterError(detail) from error


def response_for(
    results: Sequence[FormatResult],
    skipped: Sequence[Finding],
    write: bool,
    backend: str,
) -> dict[str, Any]:
    findings = list(skipped)
    changed = 0
    for result in results:
        status = "clean"
        if result.changed:
            changed += 1
            if write:
                status = "written"
            else:
                status = "needs_formatting"
        findings.append(
            Finding(
                path=result.relative_path,
                language=result.language.id,
                status=status,
            )
        )
    findings.sort(key=lambda finding: finding.path)
    status = "clean"
    if changed and not write:
        status = "needs_formatting"
    mode = "read-only"
    if write:
        mode = "write"
    return {
        "schema_version": 1,
        "policy": "default",
        "mode": mode,
        "backend": backend,
        "status": status,
        "files": [finding.as_dict() for finding in findings],
        "summary": {
            "selected": len(results),
            "changed": changed,
            "skipped": len(skipped),
        },
    }


def lint_files(
    cwd: Path,
    paths: Sequence[Path],
    requested_languages: frozenset[str],
    write: bool,
    use_docker: bool,
) -> dict[str, Any]:
    results, skipped = prepare_results(
        cwd=cwd,
        paths=paths,
        requested_languages=requested_languages,
        use_docker=use_docker,
    )
    if write:
        apply_results(results)
    backend = "local"
    if use_docker:
        backend = "docker"
    return response_for(results, skipped, write=write, backend=backend)


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(
        prog="lint.py",
        description="Format supported files without writing by default.",
    )
    mode = argument_parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--read-only",
        "--readonly",
        "-ro",
        dest="write",
        action="store_false",
        help="report formatting changes without changing source files",
    )
    mode.add_argument(
        "--write",
        "--apply",
        "-w",
        dest="write",
        action="store_true",
        help="apply every formatting result after all formatters succeed",
    )
    argument_parser.set_defaults(write=False)
    selection = argument_parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--all",
        action="store_true",
        help="select tracked and nonignored untracked files (default)",
    )
    selection.add_argument(
        "--modified",
        action="store_true",
        help="select modified Git-tracked files",
    )
    argument_parser.add_argument(
        "--cwd",
        default=".",
        help="working directory (default: current directory)",
    )
    argument_parser.add_argument(
        "--files-from0",
        metavar="FILE",
        help="read NUL-delimited paths from FILE, or standard input with -",
    )
    argument_parser.add_argument(
        "--language",
        action="append",
        default=[],
        help="limit formatting to a language id; repeat as needed",
    )
    argument_parser.add_argument(
        "--docker",
        "-d",
        action="store_true",
        help="use pinned per-language container images",
    )
    argument_parser.add_argument(
        "--list-languages",
        action="store_true",
        help="print the language manifest and exit",
    )
    argument_parser.add_argument(
        "--json",
        action="store_true",
        help="print stable machine-readable JSON",
    )
    argument_parser.add_argument("paths", nargs="*")
    return argument_parser


def human_response(response: dict[str, Any]) -> str:
    lines: list[str] = []
    files = response.get("files")
    if isinstance(files, list):
        for finding in files:
            if not isinstance(finding, dict):
                continue
            path = finding.get("path")
            status = finding.get("status")
            language = finding.get("language")
            if not isinstance(path, str):
                continue
            if not isinstance(status, str):
                continue
            if not isinstance(language, str):
                continue
            display_status = status.replace("_", " ")
            lines.append(f"{path}: {display_status} ({language})")
    summary = response.get("summary")
    if isinstance(summary, dict):
        selected = summary.get("selected")
        changed = summary.get("changed")
        skipped = summary.get("skipped")
        mode = response.get("mode")
        backend = response.get("backend")
        status = response.get("status")
        display_status = str(status).replace("_", " ")
        lines.append(
            f"{display_status}: {selected} selected, {changed} changed, "
            f"{skipped} skipped; {mode}; {backend}"
        )
    message = response.get("message")
    if isinstance(message, str):
        status = response.get("status")
        display_status = str(status).replace("_", " ")
        lines.append(f"{display_status}: {message}")
    return "\n".join(lines)


def print_response(
    response: dict[str, Any],
    json_output: bool,
    error: bool = False,
) -> None:
    stream = sys.stdout
    if error:
        stream = sys.stderr
    if json_output:
        print(json.dumps(response, sort_keys=True, indent=2), file=stream)
        return
    print(human_response(response), file=stream)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    if arguments.list_languages:
        print(json.dumps(load_manifest(), sort_keys=True, indent=2))
        return EXIT_CLEAN

    try:
        cwd = Path(arguments.cwd).resolve(strict=True)
        if not cwd.is_dir():
            raise SelectionError(f"--cwd is not a directory: {cwd}")
        if arguments.paths and (arguments.all or arguments.modified):
            raise SelectionError(
                "explicit paths cannot be combined with --all or --modified"
            )
        if arguments.files_from0 is not None and (arguments.all or arguments.modified):
            raise SelectionError(
                "--files-from0 cannot be combined with --all or --modified"
            )
        paths = select_paths(
            cwd=cwd,
            explicit_paths=arguments.paths,
            files_from0=arguments.files_from0,
            modified=arguments.modified,
        )
        response = lint_files(
            cwd=cwd,
            paths=paths,
            requested_languages=frozenset(arguments.language),
            write=arguments.write,
            use_docker=arguments.docker,
        )
        print_response(response, arguments.json)
        if response["status"] == "needs_formatting":
            return EXIT_FORMATTING
        return EXIT_CLEAN
    except SelectionError as error:
        response = {
            "schema_version": 1,
            "status": "selection_error",
            "message": str(error),
        }
        print_response(response, arguments.json, error=True)
        return EXIT_SELECTION
    except EngineError as error:
        response = {
            "schema_version": 1,
            "status": "engine_error",
            "message": str(error),
        }
        print_response(response, arguments.json, error=True)
        return EXIT_INTERNAL
    except FormatterError as error:
        response = {
            "schema_version": 1,
            "status": "formatter_error",
            "message": str(error),
        }
        print_response(response, arguments.json, error=True)
        return EXIT_FORMATTING
    except (OSError, ValueError, KeyError) as error:
        response = {
            "schema_version": 1,
            "status": "internal_error",
            "message": str(error),
        }
        print_response(response, arguments.json, error=True)
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
