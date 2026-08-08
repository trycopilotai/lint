#!/usr/bin/env python3
"""Read-only-by-default, multi-language formatting."""

from __future__ import annotations

import argparse
import base64
import dataclasses
import fnmatch
import json
import ntpath
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "languages.json"
IMAGE_MATRIX_PATH = ROOT / "images" / "matrix.json"
CONTAINER_MARKER = Path("/app/.lint-container")
REQUIREMENTS_WORKER = "--internal-requirements-format"
REQUIREMENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
REQUIREMENT_SEPARATOR_PATTERN = re.compile(r"[-_.]+")
RELEASE_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
RELEASE_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
IMAGE_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
IMAGE_PREFIX = "ghcr.io/trycopilotai/lint-"
EXIT_CLEAN = 0
EXIT_FORMATTING = 1
EXIT_SELECTION = 2
EXIT_INTERNAL = 3
FORMATTER_CONFIGURATION_NAMES = {
    "prettier": (
        ".editorconfig",
        ".prettierrc",
        ".prettierrc.cjs",
        ".prettierrc.cts",
        ".prettierrc.js",
        ".prettierrc.json",
        ".prettierrc.json5",
        ".prettierrc.mjs",
        ".prettierrc.mts",
        ".prettierrc.toml",
        ".prettierrc.ts",
        ".prettierrc.yaml",
        ".prettierrc.yml",
        "package.json",
        "prettier.config.cjs",
        "prettier.config.cts",
        "prettier.config.js",
        "prettier.config.mjs",
        "prettier.config.mts",
        "prettier.config.ts",
    ),
    "black": ("pyproject.toml",),
    "shfmt": (".editorconfig",),
    "clang": (".clang-format", "_clang-format"),
    "rust": (".rustfmt.toml", "rustfmt.toml"),
    "kotlin": (".editorconfig",),
    "toml": (".taplo.toml", "taplo.toml"),
    "swift": (".swift-format",),
    "csharp": (".csharpierrc", ".csharpierrc.json", ".editorconfig"),
    "julia": (".JuliaFormatter.toml",),
}
EXECUTABLE_PRETTIER_CONFIGURATION_NAMES = frozenset(
    {
        ".prettierrc.cjs",
        ".prettierrc.cts",
        ".prettierrc.js",
        ".prettierrc.mjs",
        ".prettierrc.mts",
        ".prettierrc.ts",
        "prettier.config.cjs",
        "prettier.config.cts",
        "prettier.config.js",
        "prettier.config.mjs",
        "prettier.config.mts",
        "prettier.config.ts",
    }
)
BLACK_SELECTION_OPTIONS = frozenset(
    {
        "exclude",
        "extend-exclude",
        "force-exclude",
        "include",
    }
)
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
class VersionProbe:
    command: tuple[str, ...]
    expected: tuple[str, ...]
    description: str
    grammar: str


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


def image_version() -> str:
    with IMAGE_MATRIX_PATH.open("r", encoding="utf-8") as handle:
        matrix = json.load(handle)
    if not isinstance(matrix, dict):
        raise FormatterError("image matrix must contain an object")
    version = matrix.get("version")
    if not isinstance(version, str):
        raise FormatterError("image matrix is missing version")
    return version


def reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise SelectionError(f"release manifest repeats key: {key}")
        value[key] = item
    return value


def require_exact_keys(
    value: dict[str, Any],
    expected: frozenset[str],
    label: str,
) -> None:
    actual = frozenset(value)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    raise SelectionError(f"{label} keys differ: missing={missing} extra={extra}")


def load_image_manifest(path: Path) -> dict[str, str]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(
                handle,
                object_pairs_hook=reject_duplicate_json_keys,
            )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SelectionError(f"could not read image manifest: {error}") from error
    if not isinstance(value, dict):
        raise SelectionError("release manifest must contain an object")
    require_exact_keys(
        value,
        frozenset({"schema_version", "release", "source", "tools", "images"}),
        "release manifest",
    )
    schema_version = value["schema_version"]
    if type(schema_version) is not int or schema_version != 1:
        raise SelectionError("release manifest has the wrong schema version")

    release = value["release"]
    expected_release = image_version()
    if release != expected_release:
        raise SelectionError("release manifest has the wrong release version")

    source = value["source"]
    if not isinstance(source, dict):
        raise SelectionError("release manifest source must contain an object")
    require_exact_keys(
        source,
        frozenset({"archive", "commit", "sha256"}),
        "release manifest source",
    )
    expected_archive = f"lint-{expected_release}.tar.gz"
    if source["archive"] != expected_archive:
        raise SelectionError("release manifest has the wrong archive name")
    commit = source["commit"]
    if not isinstance(commit, str):
        raise SelectionError("release manifest has an invalid source commit")
    if RELEASE_COMMIT_PATTERN.fullmatch(commit) is None:
        raise SelectionError("release manifest has an invalid source commit")
    source_sha256 = source["sha256"]
    if not isinstance(source_sha256, str):
        raise SelectionError("release manifest has an invalid source digest")
    if RELEASE_SHA256_PATTERN.fullmatch(source_sha256) is None:
        raise SelectionError("release manifest has an invalid source digest")

    tools = value["tools"]
    if tools != tool_versions():
        raise SelectionError("release manifest has the wrong tool versions")

    raw_images = value["images"]
    if not isinstance(raw_images, dict):
        raise SelectionError("release manifest images must contain an object")
    languages = load_languages()
    expected_images = {
        f"{IMAGE_PREFIX}{language.id}": language.id for language in languages
    }
    actual_images = frozenset(raw_images)
    expected_names = frozenset(expected_images)
    if actual_images != expected_names:
        missing = sorted(expected_names - actual_images)
        extra = sorted(actual_images - expected_names)
        raise SelectionError(
            "release manifest image coverage differs: "
            f"missing={missing} extra={extra}"
        )
    references: dict[str, str] = {}
    for image in sorted(expected_images):
        digest = raw_images[image]
        if not isinstance(digest, str):
            raise SelectionError(f"{image} has an invalid digest")
        if IMAGE_DIGEST_PATTERN.fullmatch(digest) is None:
            raise SelectionError(f"{image} has an invalid digest")
        references[expected_images[image]] = f"{image}@{digest}"
    return references


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


def path_uses_symbolic_link(cwd: Path, candidate: Path) -> bool:
    """Return whether a lexical path is or traverses a link."""

    lexical_cwd = Path(os.path.abspath(cwd))
    lexical = Path(os.path.abspath(candidate))
    try:
        relative = lexical.relative_to(lexical_cwd)
    except ValueError:
        return False
    current = lexical_cwd
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


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
        try:
            candidate.relative_to(cwd)
        except ValueError:
            continue
        if not os.path.lexists(candidate):
            continue
        if path_uses_symbolic_link(cwd, candidate):
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


def select_path(cwd: Path, candidate: Path, display_path: str) -> Path:
    """Return one contained regular path without following links."""

    return validate_selected_path(cwd, candidate, display_path)


def validate_selected_path(cwd: Path, candidate: Path, display_path: str) -> Path:
    lexical_cwd = Path(os.path.abspath(cwd))
    lexical = Path(os.path.abspath(candidate))
    try:
        relative = lexical.relative_to(lexical_cwd)
    except ValueError as error:
        raise SelectionError(f"path escapes --cwd: {display_path}") from error
    current = lexical_cwd
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise SelectionError(f"symbolic links are not accepted: {display_path}")
    try:
        resolved = lexical.resolve(strict=True)
    except FileNotFoundError as error:
        raise SelectionError(f"path does not exist: {display_path}") from error
    try:
        resolved.relative_to(cwd.resolve())
    except ValueError as error:
        raise SelectionError(f"path escapes --cwd: {display_path}") from error
    return resolved


def validate_explicit_path(cwd: Path, raw_path: str) -> Path:
    if "\x00" in raw_path:
        raise SelectionError("paths must not contain NUL bytes")
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = cwd / candidate
    return validate_selected_path(cwd, candidate, raw_path)


def expand_explicit_path(cwd: Path, raw_path: str) -> list[Path]:
    path = validate_explicit_path(cwd, raw_path)
    if path.is_file():
        return [path]
    if path.is_dir():
        selected: list[Path] = []
        for candidate in walked_paths(path):
            display_path = os.path.relpath(candidate, cwd)
            selected.append(validate_selected_path(cwd, candidate, display_path))
        return selected
    raise SelectionError(f"path is not a regular file or directory: {raw_path}")


def read_files_from0(source: str) -> list[str]:
    try:
        if source == "-":
            payload = sys.stdin.buffer.read()
        else:
            payload = Path(source).read_bytes()
    except OSError as error:
        raise SelectionError(f"--files-from0 cannot be read: {source}") from error
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


def npx_command() -> list[str]:
    if os.name != "nt":
        return ["npx"]

    shim = shutil.which("npx.cmd")
    node = shutil.which("node.exe")
    if shim is None or node is None:
        raise EngineError(
            "safe npx entry point is unavailable; "
            "install Node.js with npm or use --docker"
        )
    if not ntpath.isabs(shim) or not ntpath.isabs(node):
        raise EngineError(
            "safe npx entry point is unavailable; "
            "install Node.js with npm or use --docker"
        )
    entrypoint = ntpath.join(
        ntpath.dirname(shim),
        "node_modules",
        "npm",
        "bin",
        "npx-cli.js",
    )
    if not os.path.isfile(entrypoint):
        raise EngineError(
            "safe npx entry point is unavailable; "
            "install Node.js with npm or use --docker"
        )
    return [node, entrypoint]


def command_for(language: Language, path: Path) -> list[str]:
    versions = tool_versions()
    family = language.family
    if family == "requirements":
        return [
            sys.executable,
            str(ROOT / "lint.py"),
            REQUIREMENTS_WORKER,
            str(path),
        ]
    if family == "prettier":
        if CONTAINER_MARKER.is_file():
            executable = ["prettier"]
        else:
            executable = npx_command()
            executable.extend(
                [
                    "-y",
                    f"prettier@{versions['prettier']}",
                ]
            )
        executable.extend(
            [
                "--write",
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
        if CONTAINER_MARKER.is_file():
            executable = ["buildifier"]
        else:
            executable = npx_command()
            executable.extend(
                [
                    "-y",
                    f"@bazel/buildifier@{versions['buildifier']}",
                ]
            )
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
        return ["rustup", "run", versions["rust"], "rustfmt", str(path)]
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
        expression = (
            "using JuliaFormatter; "
            "format_file(ARGS[1], overwrite=true, throw_on_error=true)"
        )
        return [
            "julia",
            "--startup-file=no",
            "-e",
            expression,
            "--",
            str(path),
        ]
    raise FormatterError(f"unsupported formatter family: {family}")


def formatter_install_command(language: Language) -> str:
    """Return the pinned installation command for one formatter."""
    versions = tool_versions()
    family = language.family
    if family == "prettier":
        return f"npx -y prettier@{versions['prettier']} --version"
    if family == "buildifier":
        return f"npx -y @bazel/buildifier@{versions['buildifier']} --version"
    if family == "black":
        return f"pipx install --force black=={versions['black']}"
    if family == "shfmt":
        return f"go install mvdan.cc/sh/v3/cmd/shfmt@v{versions['shfmt']}"
    if family == "clang":
        return "pipx install --force clang-format==18.1.8"
    if family == "java":
        return f"docker pull {docker_image(language)}"
    if family == "go":
        return f"docker pull {docker_image(language)}"
    if family == "rust":
        return f"rustup toolchain install {versions['rust']} --component rustfmt"
    if family == "kotlin":
        return f"docker pull {docker_image(language)}"
    if family == "toml":
        return f"cargo install taplo-cli --version {versions['taplo']} --locked"
    if family == "xml":
        return f"docker pull {docker_image(language)}"
    if family == "swift":
        return f"docker pull {docker_image(language)}"
    if family == "csharp":
        return (
            "dotnet tool install --global csharpier "
            f"--version {versions['csharpier']}"
        )
    if family == "julia":
        return f"docker pull {docker_image(language)}"
    raise FormatterError(f"unsupported formatter family: {family}")


def formatter_install_hint(language: Language) -> str:
    """Return a formatter-specific recovery hint."""
    command = formatter_install_command(language)
    if command.startswith("docker pull "):
        return f"run `{command}` and retry with --docker"
    return f"use --docker or run `{command}`"


def npx_engine_failure(returncode: int, detail: str) -> bool:
    """Identify dependency resolution failures separately from bad input."""
    if returncode == 127:
        return True
    lowered = detail.lower()
    markers = (
        "command not found",
        "could not determine executable",
    )
    if any(marker in lowered for marker in markers):
        return True

    npm_engine_codes = frozenset(
        {
            "E403",
            "E404",
            "EACCES",
            "EAI_AGAIN",
            "ECONNREFUSED",
            "ECONNRESET",
            "EHOSTUNREACH",
            "ENETUNREACH",
            "ENOSPC",
            "ENOTFOUND",
            "EPERM",
            "ETIMEDOUT",
        }
    )
    for line in detail.splitlines():
        normalized = line.strip().lower()
        code_match = re.match(
            r"^npm (?:error|err!) code ([a-z0-9_]+)(?:\s|$)",
            normalized,
        )
        if code_match is not None:
            if code_match.group(1).upper() in npm_engine_codes:
                return True
        if re.match(r"^npm (?:error|err!) network(?:\s|$)", normalized):
            return True
    return False


def libxml_numeric_version(version: str) -> str:
    parts = version.split(".")
    if len(parts) != 3:
        raise FormatterError(f"invalid libxml2 version: {version}")
    try:
        major, minor, patch = (int(part) for part in parts)
    except ValueError as error:
        raise FormatterError(f"invalid libxml2 version: {version}") from error
    return str(major * 10000 + minor * 100 + patch)


def version_command(language: Language) -> VersionProbe | None:
    versions = tool_versions()
    family = language.family
    if family in {"prettier", "buildifier", "requirements"}:
        return None
    if family == "black":
        return VersionProbe(
            command=("black", "--version"),
            expected=(versions["black"],),
            description=versions["black"],
            grammar=(
                r"black, (?P<version>[0-9]+\.[0-9]+\.[0-9]+)"
                r" \(compiled: (?:yes|no)\)"
                r"\r?\nPython \([^)]+\) [0-9]+\.[0-9]+\.[0-9]+"
            ),
        )
    if family == "shfmt":
        return VersionProbe(
            command=("shfmt", "--version"),
            expected=(versions["shfmt"],),
            description=versions["shfmt"],
            grammar=r"v(?P<version>[0-9]+\.[0-9]+\.[0-9]+)",
        )
    if family == "clang":
        return VersionProbe(
            command=("clang-format", "--version"),
            expected=(versions["clang-format"],),
            description=versions["clang-format"],
            grammar=(
                r"(?:Homebrew )?clang-format version "
                r"(?P<version>[0-9]+\.[0-9]+\.[0-9]+)"
            ),
        )
    if family == "java":
        return VersionProbe(
            command=("google-java-format", "--version"),
            expected=(versions["google-java-format"],),
            description=versions["google-java-format"],
            grammar=(
                r"google-java-format: Version " r"(?P<version>[0-9]+\.[0-9]+\.[0-9]+)"
            ),
        )
    if family == "go":
        gofmt = shutil.which("gofmt")
        if gofmt is None:
            raise EngineError(
                "required formatter is not installed: gofmt; "
                f"{formatter_install_hint(language)}"
            )
        return VersionProbe(
            command=("go", "version", gofmt),
            expected=(versions["go"],),
            description=versions["go"],
            grammar=(rf"{re.escape(gofmt)}: go" r"(?P<version>[0-9]+\.[0-9]+\.[0-9]+)"),
        )
    if family == "rust":
        return VersionProbe(
            command=("rustup", "run", versions["rust"], "rustfmt", "--version"),
            expected=(versions["rustfmt"],),
            description=(f"rustfmt {versions['rustfmt']} from Rust {versions['rust']}"),
            grammar=(
                r"rustfmt (?P<version>[0-9]+\.[0-9]+\.[0-9]+)"
                r"(?:-stable \([0-9a-f]{10} [0-9]{4}-[0-9]{2}-[0-9]{2}\))?"
            ),
        )
    if family == "kotlin":
        return VersionProbe(
            command=("ktlint", "--version"),
            expected=(versions["ktlint"],),
            description=versions["ktlint"],
            grammar=(r"ktlint version " r"(?P<version>[0-9]+\.[0-9]+\.[0-9]+)"),
        )
    if family == "toml":
        return VersionProbe(
            command=("taplo", "--version"),
            expected=(versions["taplo"],),
            description=versions["taplo"],
            grammar=(r"taplo " r"(?P<version>[0-9]+\.[0-9]+\.[0-9]+)"),
        )
    if family == "xml":
        # xmllint reports LIBXML_VERSION, the packed integer 21503,
        # rather than the released 2.15.3, so the pin is normalized
        # to the same integer and still compared exactly. Its banner
        # writes every compiled-in feature name followed by a space,
        # so the second line always ends in one.
        return VersionProbe(
            command=("xmllint", "--version"),
            expected=(libxml_numeric_version(versions["libxml2"]),),
            description=versions["libxml2"],
            grammar=(
                r"(?:[A-Za-z0-9_./-]*/)?xmllint: using libxml version "
                r"(?P<version>[0-9]+)"
                r"(?:\r?\n   compiled with: (?:[A-Z][A-Za-z0-9]* )+)?"
            ),
        )
    if family == "swift":
        return VersionProbe(
            command=("swift-format", "--version"),
            expected=(versions["swift-format"],),
            description=versions["swift-format"],
            grammar=r"(?P<version>[0-9]+\.[0-9]+\.[0-9]+)",
        )
    if family == "csharp":
        return VersionProbe(
            command=("csharpier", "--version"),
            expected=(versions["csharpier"],),
            description=versions["csharpier"],
            grammar=r"(?P<version>[0-9]+\.[0-9]+\.[0-9]+)",
        )
    if family == "julia":
        expression = (
            "using JuliaFormatter; "
            'print(VERSION, "\\n", Base.pkgversion(JuliaFormatter))'
        )
        return VersionProbe(
            command=(
                "julia",
                "--startup-file=no",
                "-e",
                expression,
            ),
            expected=(versions["julia"], versions["juliaformatter"]),
            description=(
                f"Julia {versions['julia']} and "
                f"JuliaFormatter {versions['juliaformatter']}"
            ),
            grammar=(
                r"(?P<version>[0-9]+\.[0-9]+\.[0-9]+)\r?\n"
                r"(?P<secondary>[0-9]+\.[0-9]+\.[0-9]+)"
            ),
        )
    raise FormatterError(f"unsupported formatter family: {family}")


def version_output_matches(probe: VersionProbe, output: str) -> bool:
    normalized = output
    if normalized.endswith("\r\n"):
        normalized = normalized[:-2]
    elif normalized.endswith("\n"):
        normalized = normalized[:-1]
    matched = re.fullmatch(probe.grammar, normalized)
    if matched is None:
        return False
    reported = [matched.group("version")]
    secondary = matched.groupdict().get("secondary")
    if secondary is not None:
        reported.append(secondary)
    return tuple(reported) == probe.expected


def verify_formatter_version(language: Language) -> None:
    probe = version_command(language)
    if probe is None:
        return
    timeout_seconds = 10
    if language.family == "julia":
        timeout_seconds = 30
    try:
        completed = subprocess.run(
            probe.command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as error:
        raise EngineError(
            f"{probe.command[0]} is not installed; {formatter_install_hint(language)}"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise FormatterError(
            f"{probe.command[0]} version check exceeded {timeout_seconds} seconds"
        ) from error
    output = completed.stdout.decode(
        "utf-8", errors="replace"
    ) + completed.stderr.decode("utf-8", errors="replace")
    if completed.returncode != 0 or not version_output_matches(probe, output):
        found = output.strip()
        if found == "":
            found = f"exit {completed.returncode}"
        raise EngineError(
            f"{probe.command[0]} must match {probe.description}; found {found}; "
            f"{formatter_install_hint(language)}"
        )


HOST_INSTALL_ALLOWLIST = frozenset(
    {
        "prettier",
        "buildifier",
        "black",
        "clang",
    }
)
INSTALL_TIMEOUT_SECONDS = 600


@dataclasses.dataclass(frozen=True)
class MissingTool:
    """A host formatter family that failed availability checks."""

    family: str
    language_ids: tuple[str, ...]
    install_argv: tuple[str, ...]
    allowlisted: bool
    message: str


class HostToolError(EngineError):
    """Host tools are missing after install policy was applied."""

    def __init__(
        self,
        message: str,
        recovery: dict[str, Any],
    ) -> None:
        super().__init__(message)
        self.recovery = recovery


def resolve_install_policy(
    env: dict[str, str] | None = None,
    *,
    stdin_isatty: bool | None = None,
) -> str:
    """Return never, prompt, or always for host tool installation."""
    if env is None:
        env = dict(os.environ)
    raw = env.get("LINT_INSTALL")
    if raw is not None:
        value = raw.strip().lower()
        if value in {"never", "prompt", "always"}:
            return value
        raise SelectionError(
            "LINT_INSTALL must be never, prompt, or always; "
            f"got {raw!r}"
        )
    ci = env.get("CI", "").strip().lower()
    actions = env.get("GITHUB_ACTIONS", "").strip().lower()
    if ci in {"1", "true", "yes"} or actions in {"1", "true", "yes"}:
        return "never"
    if stdin_isatty is None:
        stdin_isatty = sys.stdin.isatty()
    if stdin_isatty:
        return "prompt"
    return "never"


def formatter_install_argv(language: Language) -> list[str]:
    """Return argv for installing one language's host formatter."""
    versions = tool_versions()
    family = language.family
    if family == "prettier":
        return [
            *npx_command(),
            "-y",
            f"prettier@{versions['prettier']}",
            "--version",
        ]
    if family == "buildifier":
        return [
            *npx_command(),
            "-y",
            f"@bazel/buildifier@{versions['buildifier']}",
            "--version",
        ]
    if family == "black":
        return [
            "pipx",
            "install",
            "--force",
            f"black=={versions['black']}",
        ]
    if family == "clang":
        return [
            "pipx",
            "install",
            "--force",
            "clang-format==18.1.8",
        ]
    if family == "shfmt":
        return [
            "go",
            "install",
            f"mvdan.cc/sh/v3/cmd/shfmt@v{versions['shfmt']}",
        ]
    if family == "java":
        return ["docker", "pull", docker_image(language)]
    if family == "go":
        return ["docker", "pull", docker_image(language)]
    if family == "rust":
        return [
            "rustup",
            "toolchain",
            "install",
            versions["rust"],
            "--component",
            "rustfmt",
        ]
    if family == "kotlin":
        return ["docker", "pull", docker_image(language)]
    if family == "toml":
        return [
            "cargo",
            "install",
            "taplo-cli",
            "--version",
            versions["taplo"],
            "--locked",
        ]
    if family == "xml":
        return ["docker", "pull", docker_image(language)]
    if family == "swift":
        return ["docker", "pull", docker_image(language)]
    if family == "csharp":
        return [
            "dotnet",
            "tool",
            "install",
            "--global",
            "csharpier",
            "--version",
            versions["csharpier"],
        ]
    if family == "julia":
        return ["docker", "pull", docker_image(language)]
    if family == "requirements":
        return []
    raise FormatterError(f"unsupported formatter family: {family}")


def host_install_allowlisted(
    family: str,
    install_argv: Sequence[str],
) -> bool:
    """Return whether auto-install is permitted for this family."""
    if family not in HOST_INSTALL_ALLOWLIST:
        return False
    if not install_argv:
        return False
    if install_argv[0] == "docker":
        return False
    return True


def recovery_payload(
    missing: Sequence[MissingTool],
    policy: str,
) -> dict[str, Any]:
    """Build structured recovery metadata for JSON errors."""
    entries: list[dict[str, Any]] = []
    for tool in missing:
        entries.append(
            {
                "family": tool.family,
                "language_ids": list(tool.language_ids),
                "install_argv": list(tool.install_argv),
                "allowlisted": tool.allowlisted,
            }
        )
    return {
        "install_policy": policy,
        "missing": entries,
    }


def probe_missing_tools(
    languages: Sequence[Language],
) -> list[MissingTool]:
    """Return host tools that fail version/availability probes."""
    by_family: dict[str, list[Language]] = {}
    for language in languages:
        by_family.setdefault(language.family, []).append(language)
    missing: list[MissingTool] = []
    for family in sorted(by_family):
        group = by_family[family]
        language = group[0]
        try:
            verify_formatter_version(language)
        except EngineError as error:
            argv = formatter_install_argv(language)
            allowlisted = host_install_allowlisted(family, argv)
            missing.append(
                MissingTool(
                    family=family,
                    language_ids=tuple(item.id for item in group),
                    install_argv=tuple(argv),
                    allowlisted=allowlisted,
                    message=str(error),
                )
            )
    return missing


def run_allowlisted_install(
    argv: Sequence[str],
    *,
    timeout_seconds: int = INSTALL_TIMEOUT_SECONDS,
    runner: Any = None,
) -> None:
    """Run one pinned install argv for an allowlisted family."""
    if runner is None:
        runner = subprocess.run
    try:
        completed = runner(
            list(argv),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as error:
        raise EngineError(
            f"install launcher is not installed: {argv[0]}"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise EngineError(
            f"install timed out after {timeout_seconds}s: "
            f"{' '.join(argv)}"
        ) from error
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()
        if detail == "":
            detail = (completed.stdout or "").strip()
        if detail == "":
            detail = f"exit {completed.returncode}"
        raise EngineError(
            f"install failed ({' '.join(argv)}): {detail}"
        )


def apply_host_install_policy(
    missing: Sequence[MissingTool],
    policy: str,
    *,
    input_func: Any = None,
    runner: Any = None,
) -> list[MissingTool]:
    """Install allowlisted missing tools per policy; return unresolved."""
    if input_func is None:
        input_func = input
    unresolved: list[MissingTool] = []
    for tool in missing:
        if not tool.allowlisted:
            unresolved.append(tool)
            continue
        if policy == "never":
            unresolved.append(tool)
            continue
        if policy == "prompt":
            prompt = (
                f"{tool.family} is not ready on this host.\n"
                f"Install with: {' '.join(tool.install_argv)}\n"
                "Proceed? [y/N] "
            )
            answer = str(input_func(prompt)).strip().lower()
            if answer not in {"y", "yes"}:
                unresolved.append(tool)
                continue
        run_allowlisted_install(tool.install_argv, runner=runner)
    return unresolved


def ensure_host_formatters(
    languages: Sequence[Language],
    *,
    policy: str | None = None,
    input_func: Any = None,
    runner: Any = None,
) -> list[MissingTool]:
    """Probe and optionally install host tools; raise if still missing."""
    if not languages:
        return []
    if policy is None:
        policy = resolve_install_policy()
    missing = probe_missing_tools(languages)
    if not missing:
        return []
    apply_host_install_policy(
        missing,
        policy,
        input_func=input_func,
        runner=runner,
    )
    families_to_recheck = {
        tool.family for tool in missing if tool.allowlisted
    }
    recheck_languages = [
        language
        for language in languages
        if language.family in families_to_recheck
    ]
    non_allowlisted = [
        tool for tool in missing if not tool.allowlisted
    ]
    still_after: list[MissingTool] = []
    if recheck_languages:
        still_after = probe_missing_tools(recheck_languages)
    still = non_allowlisted + still_after
    seen: set[str] = set()
    deduped: list[MissingTool] = []
    for tool in still:
        if tool.family in seen:
            continue
        seen.add(tool.family)
        deduped.append(tool)
    if deduped:
        lines = [tool.message for tool in deduped]
        raise HostToolError(
            "; ".join(lines),
            recovery_payload(deduped, policy),
        )
    return []


def doctor_host_formatters(
    languages: Sequence[Language],
) -> dict[str, Any]:
    """Return a doctor report without installing anything."""
    missing = probe_missing_tools(languages)
    status = "ok"
    if missing:
        status = "missing"
    message = "all probed host formatters are available"
    if missing:
        message = (
            f"{len(missing)} host formatter families need attention"
        )
    return {
        "schema_version": 2,
        "status": status,
        "mode": "doctor",
        "backend": "local",
        "recovery": recovery_payload(
            missing,
            resolve_install_policy(),
        ),
        "message": message,
    }


def languages_for_doctor(
    requested_languages: frozenset[str],
) -> list[Language]:
    """Select languages to probe for doctor/ensure."""
    known = load_languages()
    if requested_languages:
        unknown = requested_languages - frozenset(
            language.id for language in known
        )
        if unknown:
            values = ", ".join(sorted(unknown))
            raise SelectionError(f"unknown language: {values}")
        return [
            language
            for language in known
            if language.id in requested_languages
        ]
    # One representative per family so doctor stays small.
    seen_families: set[str] = set()
    selected: list[Language] = []
    for language in known:
        if language.family in seen_families:
            continue
        if language.family == "requirements":
            continue
        seen_families.add(language.family)
        selected.append(language)
    return selected



def requirement_records(section: list[str]) -> list[str]:
    records: list[str] = []
    current: list[str] = []
    for line in section:
        current.append(line)
        if line.endswith("\\"):
            continue
        records.append("\n".join(current))
        current = []
    if current:
        records.append("\n".join(current))
    return records


def canonical_requirement_name(record: str) -> str:
    """Return the normalized distribution name used for sorting."""

    first_line = record.splitlines()[0].strip()
    match = REQUIREMENT_NAME_PATTERN.match(first_line)
    if match is None:
        return first_line.casefold()
    return REQUIREMENT_SEPARATOR_PATTERN.sub("-", match.group(0)).casefold()


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
        for record in requirement_records(section):
            if record.lstrip().startswith(("#", "-", "http://", "https://")):
                comments.append(record)
            else:
                requirements.append(record)
        requirements.sort(key=canonical_requirement_name)
        output_sections.append("\n".join(comments + requirements))
    if not output_sections:
        return b""
    return ("\n\n".join(output_sections) + "\n").encode("utf-8")


def configuration_contains_key(value: Any, names: frozenset[str]) -> bool:
    """Return whether a nested configuration contains a named key."""

    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str) and key.casefold() in names:
                return True
            if configuration_contains_key(nested, names):
                return True
        return False
    if isinstance(value, list):
        for nested in value:
            if configuration_contains_key(nested, names):
                return True
    return False


def decode_configuration(path: Path, payload: bytes) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise FormatterError(
            f"formatter configuration is not UTF-8: {path.name}"
        ) from error


def validate_prettier_configuration(path: Path, payload: bytes) -> None:
    if path.name in EXECUTABLE_PRETTIER_CONFIGURATION_NAMES:
        raise FormatterError(
            f"executable Prettier configuration is not supported: {path.name}"
        )
    text = decode_configuration(path, payload)
    plugin_names = frozenset({"plugin", "plugins", "pluginsearchdirs"})
    parsed: Any | None = None
    if path.name in {"package.json", ".prettierrc.json"}:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            raise FormatterError(
                f"invalid Prettier JSON configuration: {path.name}"
            ) from error
    if path.name == ".prettierrc.toml":
        try:
            parsed = tomllib.loads(text)
        except tomllib.TOMLDecodeError as error:
            raise FormatterError(
                f"invalid Prettier TOML configuration: {path.name}"
            ) from error
    if parsed is not None:
        if configuration_contains_key(parsed, plugin_names):
            raise FormatterError(
                f"project Prettier plugins are not supported: {path.name}"
            )
        return
    plugin_pattern = re.compile(
        r"(?im)^\s*[\"']?(?:plugin|plugins|pluginSearchDirs)[\"']?\s*[:=]"
    )
    if plugin_pattern.search(text) is not None:
        raise FormatterError(f"project Prettier plugins are not supported: {path.name}")


def validate_black_configuration(path: Path, payload: bytes) -> None:
    text = decode_configuration(path, payload)
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise FormatterError(
            f"invalid Black TOML configuration: {path.name}"
        ) from error
    tool = parsed.get("tool")
    if not isinstance(tool, dict):
        return
    black = tool.get("black")
    if not isinstance(black, dict):
        return
    for name in black:
        normalized_name = name.replace("--", "").replace("-", "_")
        if normalized_name == "python_cell_magics":
            raise FormatterError("Black python-cell-magics option is not supported")
    for name in BLACK_SELECTION_OPTIONS:
        if name in black:
            raise FormatterError(f"Black selection option is not supported: {name}")


def validate_formatter_configuration(
    path: Path,
    language: Language,
    payload: bytes,
) -> None:
    if language.family == "prettier":
        validate_prettier_configuration(path, payload)
    if language.family == "black":
        validate_black_configuration(path, payload)


def formatter_configuration_paths(
    cwd: Path,
    path: Path,
    language: Language,
) -> list[Path]:
    """Return bounded native configuration files for one formatter."""

    names = FORMATTER_CONFIGURATION_NAMES.get(language.family)
    if names is None:
        return []
    resolved_cwd = cwd.resolve()
    relative_parent = path.parent.relative_to(resolved_cwd)
    directories = [resolved_cwd]
    current = resolved_cwd
    for part in relative_parent.parts:
        current = current / part
        directories.append(current)

    configurations: list[Path] = []
    for directory in directories:
        for name in names:
            candidate = directory / name
            if not os.path.lexists(candidate):
                continue
            display_path = os.path.relpath(candidate, resolved_cwd)
            try:
                checked = select_path(resolved_cwd, candidate, display_path)
            except SelectionError as error:
                raise FormatterError(
                    f"symbolic formatter configuration is not accepted: "
                    f"{display_path}"
                ) from error
            if not checked.is_file():
                raise FormatterError(
                    f"formatter configuration is not a regular file: " f"{display_path}"
                )
            configurations.append(checked)
    return configurations


def copy_formatter_configuration(
    cwd: Path,
    path: Path,
    language: Language,
    mirror_root: Path,
    maximum_bytes: int,
) -> None:
    """Copy native formatter configuration into the temporary mirror."""

    resolved_cwd = cwd.resolve()
    for configuration in formatter_configuration_paths(
        resolved_cwd,
        path,
        language,
    ):
        relative = configuration.relative_to(resolved_cwd)
        payload = configuration.read_bytes()
        if len(payload) > maximum_bytes:
            raise FormatterError(
                f"{relative}: formatter configuration exceeds " f"{maximum_bytes} bytes"
            )
        validate_formatter_configuration(configuration, language, payload)
        mirror_path = mirror_root / relative
        mirror_path.parent.mkdir(parents=True, exist_ok=True)
        if mirror_path.exists():
            if mirror_path.read_bytes() != payload:
                raise FormatterError(
                    f"formatter configuration changed during selection: " f"{relative}"
                )
            continue
        mirror_path.write_bytes(payload)


def run_formatter(
    language: Language,
    path: Path,
    cwd: Path,
    timeout_seconds: int,
) -> None:
    if language.family == "prettier":
        (path.parent / ".lint-empty-ignore").touch()
    command = command_for(language, path)
    uses_npx = language.family in {"prettier", "buildifier"}
    if CONTAINER_MARKER.is_file():
        uses_npx = False
    pass_count = 1
    if language.family == "kotlin":
        pass_count = 2
    started_at = time.monotonic()
    for _ in range(pass_count):
        remaining_seconds = timeout_seconds - (time.monotonic() - started_at)
        if remaining_seconds <= 0:
            raise FormatterError(
                f"{language.id} formatter exceeded {timeout_seconds} seconds"
            )
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=remaining_seconds,
            )
        except FileNotFoundError as error:
            executable = command[0]
            raise EngineError(
                f"{executable} is not installed; {formatter_install_hint(language)}"
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
            if uses_npx:
                if npx_engine_failure(completed.returncode, detail):
                    raise EngineError(f"{detail}; {formatter_install_hint(language)}")
            raise FormatterError(detail)


def docker_image(language: Language) -> str:
    return f"{IMAGE_PREFIX}{language.id}:{image_version()}"


def docker_user() -> str:
    """Return a writable non-root bind-mount identity on each host."""
    try:
        user_id = os.getuid()
        group_id = os.getgid()
    except AttributeError:
        return "65532:65532"
    if user_id == 0 or group_id == 0:
        return "65532:65532"
    return f"{user_id}:{group_id}"


def run_docker_formatter(
    language: Language,
    mirror_root: Path,
    relative_path: Path,
    timeout_seconds: int,
    image_reference: str | None = None,
) -> None:
    image = docker_image(language)
    if image_reference is not None:
        image = image_reference
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
        "--workdir",
        "/work",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "--user",
        docker_user(),
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
    language_overrides: dict[Path, str] | None = None,
    image_references: dict[str, str] | None = None,
) -> tuple[list[FormatResult], list[Finding]]:
    resolved_cwd = cwd.resolve()
    known_languages = load_languages()
    languages_by_id = {language.id: language for language in known_languages}
    language_ids = frozenset(language.id for language in known_languages)
    unknown = requested_languages - language_ids
    resolved_overrides: dict[Path, str] = {}
    if language_overrides is not None:
        for path, language_id in language_overrides.items():
            resolved_overrides[path.resolve()] = language_id
        unknown = unknown | (frozenset(resolved_overrides.values()) - language_ids)
    if unknown:
        values = ", ".join(sorted(unknown))
        raise SelectionError(f"unknown language: {values}")

    selected: list[tuple[Path, Language]] = []
    findings: list[Finding] = []
    for path in paths:
        display_path = os.path.relpath(path, cwd)
        checked = select_path(cwd, path, display_path)
        path = checked
        override = resolved_overrides.get(path)
        if override is None:
            language = detect_language(path, known_languages)
        else:
            language = languages_by_id[override]
        relative_path = path.relative_to(resolved_cwd).as_posix()
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
        if not path.is_file():
            raise SelectionError(f"not a regular file: {relative_path}")
        selected.append((path, language))

    cwd = resolved_cwd

    if not use_docker and selected:
        unique_languages: list[Language] = []
        seen_families: set[str] = set()
        for _path, language in selected:
            if language.family in seen_families:
                continue
            seen_families.add(language.family)
            unique_languages.append(language)
        ensure_host_formatters(unique_languages)

    request_limits = limits()
    maximum_bytes = request_limits["max_file_bytes"]
    timeout_seconds = request_limits["timeout_seconds_per_file"]
    results: list[FormatResult] = []
    verified_families: set[str] = set()
    workspace = tempfile.mkdtemp(prefix="lint-work-")
    mirror_root = Path(workspace)
    (mirror_root / ".lint-empty-ignore").write_bytes(b"")
    try:
        for path, language in selected:
            relative = path.relative_to(cwd)
            payload = path.read_bytes()
            if len(payload) > maximum_bytes:
                raise FormatterError(f"{relative}: exceeds {maximum_bytes} bytes")
            copy_formatter_configuration(
                cwd,
                path,
                language,
                mirror_root,
                maximum_bytes,
            )
            mirror_path = mirror_root / relative
            mirror_path.parent.mkdir(parents=True, exist_ok=True)
            mirror_path.write_bytes(payload)
            source_mode = stat.S_IMODE(path.stat().st_mode)
            mirror_mode = source_mode | stat.S_IWUSR
            os.chmod(mirror_path, mirror_mode)
            if use_docker:
                image_reference = None
                if image_references is not None:
                    image_reference = image_references[language.id]
                if image_reference is None:
                    run_docker_formatter(
                        language,
                        mirror_root,
                        relative,
                        timeout_seconds,
                    )
                else:
                    run_docker_formatter(
                        language,
                        mirror_root,
                        relative,
                        timeout_seconds,
                        image_reference,
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
                status = "changed"
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
    if changed:
        if write:
            status = "changed"
        else:
            status = "needs_formatting"
    mode = "read-only"
    if write:
        mode = "write"
    # Read-only runs rewrite nothing, so reporting them as
    # "changed" tells a reader that files moved when they did
    # not. The count is the same number either way; only the
    # name distinguishes what already happened from what would.
    summary: dict[str, int] = {"selected": len(results)}
    if write:
        summary["changed"] = changed
    else:
        summary["would_change"] = changed
    summary["skipped"] = len(skipped)
    return {
        "schema_version": 2,
        "policy": "default",
        "mode": mode,
        "backend": backend,
        "status": status,
        "files": [finding.as_dict() for finding in findings],
        "summary": summary,
    }


def lint_files(
    cwd: Path,
    paths: Sequence[Path],
    requested_languages: frozenset[str],
    write: bool,
    use_docker: bool,
    image_references: dict[str, str] | None = None,
) -> dict[str, Any]:
    results, skipped = prepare_results(
        cwd=cwd,
        paths=paths,
        requested_languages=requested_languages,
        use_docker=use_docker,
        image_references=image_references,
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
        help="use versioned per-language container images",
    )
    argument_parser.add_argument(
        "--image-manifest",
        type=Path,
        metavar="PATH",
        help="use exact Docker digests from a release manifest",
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


def parse_arguments(arguments: Sequence[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    return parser().parse_args(arguments)


def validate_selection_arguments(parsed: argparse.Namespace) -> None:
    """Reject combinations that select files through two routes."""
    explicit_selection = bool(parsed.paths)
    files_selection = parsed.files_from0 is not None
    default_selection = parsed.all or parsed.modified
    if explicit_selection and files_selection:
        raise SelectionError("paths cannot be combined with --files-from0")
    if (explicit_selection or files_selection) and default_selection:
        raise SelectionError(
            "explicit selection cannot be combined with --all or --modified"
        )
    if parsed.image_manifest is not None and not parsed.docker:
        raise SelectionError("--image-manifest requires --docker")
    if parsed.image_manifest is not None and parsed.list_languages:
        raise SelectionError(
            "--image-manifest cannot be combined with --list-languages"
        )


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
            message = finding.get("message")
            if not isinstance(path, str):
                continue
            if not isinstance(status, str):
                continue
            if not isinstance(language, str):
                continue
            display_status = status.replace("_", " ")
            line = f"{path}: {display_status} ({language})"
            if isinstance(message, str):
                line = f"{line}: {message}"
            lines.append(line)
    summary = response.get("summary")
    if isinstance(summary, dict):
        selected = summary.get("selected")
        skipped = summary.get("skipped")
        mode = response.get("mode")
        backend = response.get("backend")
        status = response.get("status")
        display_status = str(status).replace("_", " ")
        if "changed" in summary:
            count = summary.get("changed")
            count_label = "changed"
        else:
            count = summary.get("would_change")
            count_label = "would change"
        lines.append(
            f"{display_status}: {selected} selected, {count} {count_label}, "
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
    if argv is None:
        raw_arguments = sys.argv[1:]
    else:
        raw_arguments = list(argv)
    if len(raw_arguments) == 2 and raw_arguments[0] == REQUIREMENTS_WORKER:
        path = Path(raw_arguments[1])
        try:
            path.write_bytes(requirements_output(path.read_bytes()))
            return EXIT_CLEAN
        except (FormatterError, OSError) as error:
            print(str(error), file=sys.stderr)
            return EXIT_FORMATTING
    command = "lint"
    if raw_arguments and raw_arguments[0] in {"doctor", "ensure"}:
        command = raw_arguments[0]
        raw_arguments = raw_arguments[1:]
    arguments = parse_arguments(raw_arguments)
    try:
        validate_selection_arguments(arguments)
        if arguments.list_languages:
            print(json.dumps(load_manifest(), sort_keys=True, indent=2))
            return EXIT_CLEAN
        image_references = None
        if arguments.image_manifest is not None:
            image_references = load_image_manifest(arguments.image_manifest)
        try:
            cwd = Path(arguments.cwd).resolve(strict=True)
        except OSError as error:
            raise SelectionError(
                f"--cwd is not a directory: {arguments.cwd}"
            ) from error
        if not cwd.is_dir():
            raise SelectionError(f"--cwd is not a directory: {cwd}")
        requested = frozenset(arguments.language)
        if command == "doctor":
            languages = languages_for_doctor(requested)
            response = doctor_host_formatters(languages)
            print_response(response, arguments.json)
            if response["status"] != "ok":
                return EXIT_INTERNAL
            return EXIT_CLEAN
        if command == "ensure":
            policy = resolve_install_policy()
            if policy == "never":
                raise SelectionError(
                    "ensure is blocked while LINT_INSTALL=never; "
                    "set LINT_INSTALL=prompt or LINT_INSTALL=always"
                )
            languages = languages_for_doctor(requested)
            ensure_host_formatters(languages, policy=policy)
            response = doctor_host_formatters(languages)
            response["mode"] = "ensure"
            print_response(response, arguments.json)
            if response["status"] != "ok":
                return EXIT_INTERNAL
            return EXIT_CLEAN
        paths = select_paths(
            cwd=cwd,
            explicit_paths=arguments.paths,
            files_from0=arguments.files_from0,
            modified=arguments.modified,
        )
        response = lint_files(
            cwd=cwd,
            paths=paths,
            requested_languages=requested,
            write=arguments.write,
            use_docker=arguments.docker,
            image_references=image_references,
        )
        print_response(response, arguments.json)
        if response["status"] == "needs_formatting":
            return EXIT_FORMATTING
        return EXIT_CLEAN
    except SelectionError as error:
        response = {
            "schema_version": 2,
            "status": "selection_error",
            "message": str(error),
        }
        print_response(response, arguments.json, error=True)
        return EXIT_SELECTION
    except HostToolError as error:
        response = {
            "schema_version": 2,
            "status": "engine_error",
            "message": str(error),
            "recovery": error.recovery,
        }
        print_response(response, arguments.json, error=True)
        return EXIT_INTERNAL
    except EngineError as error:
        response = {
            "schema_version": 2,
            "status": "engine_error",
            "message": str(error),
        }
        print_response(response, arguments.json, error=True)
        return EXIT_INTERNAL
    except FormatterError as error:
        response = {
            "schema_version": 2,
            "status": "formatter_error",
            "message": str(error),
        }
        print_response(response, arguments.json, error=True)
        return EXIT_FORMATTING
    except (OSError, ValueError, KeyError) as error:
        response = {
            "schema_version": 2,
            "status": "internal_error",
            "message": str(error),
        }
        print_response(response, arguments.json, error=True)
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
