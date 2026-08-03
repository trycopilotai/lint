#!/usr/bin/env python3
"""Validate image coverage, pins, and optional local sizes."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "images" / "matrix.json"
SOURCES_PATH = ROOT / "images" / "sources.json"
LANGUAGES_PATH = ROOT / "languages.json"
DOCKERFILE_PATH = ROOT / "images" / "Dockerfile"
REQUIRED_LICENSE_PATHS = frozenset(
    {
        "licenses/lint/LICENSE",
        "licenses/lint/THIRD_PARTY_NOTICES.md",
    }
)
INVENTORY_ROOT = ROOT / "images" / "inventories"
INVENTORY_SCHEMA_VERSION = 1
INVENTORY_KEYS = frozenset({"schema_version", "target", "architecture", "entries"})
ENTRY_KEYS = frozenset({"path", "type", "mode", "link_target", "sha256", "role"})
ENTRY_TYPES = frozenset(
    {
        "block",
        "character",
        "directory",
        "fifo",
        "hardlink",
        "regular",
        "symlink",
    }
)
ARCHITECTURES = frozenset({"amd64", "arm64"})
CANONICAL_INVENTORY_ARCHITECTURES = frozenset({"amd64"})
ENTRY_ROLES = frozenset({"entrypoint", "formatter", "license", "runtime"})


@dataclass(frozen=True)
class PathRoleRule:
    """One reviewed exact-path or subtree role assignment."""

    role: str
    exact: tuple[str, ...] = ()
    prefixes: tuple[str, ...] = ()


LICENSE_RULE = PathRoleRule(role="license", prefixes=("licenses",))
TOOL_VERSION_RULE = PathRoleRule(
    role="formatter",
    exact=("lint-tool-version",),
)
BASE_RUNTIME_PREFIXES = (
    "bin",
    "boot",
    "dev",
    "etc",
    "home",
    "lib",
    "lib64",
    "proc",
    "root",
    "run",
    "sbin",
    "sys",
    "tmp",
    "usr",
    "var",
)
PATH_ROLE_POLICY: dict[str, tuple[PathRoleRule, ...]] = {
    "prettier": (
        LICENSE_RULE,
        TOOL_VERSION_RULE,
        PathRoleRule(role="entrypoint", exact=("usr/local/bin/node",)),
        PathRoleRule(
            role="formatter",
            prefixes=("usr/local/lib/node_modules/prettier",),
        ),
        PathRoleRule(
            role="runtime",
            prefixes=("lib", "usr"),
        ),
    ),
    "buildifier": (
        LICENSE_RULE,
        TOOL_VERSION_RULE,
        PathRoleRule(role="entrypoint", exact=("buildifier",)),
    ),
    "black": (
        LICENSE_RULE,
        TOOL_VERSION_RULE,
        PathRoleRule(role="entrypoint", exact=("usr/local/bin/python3",)),
        PathRoleRule(
            role="formatter",
            exact=("usr/local/bin/black", "usr/local/bin/blackd"),
            prefixes=("usr/local/lib/python3.13/site-packages",),
        ),
        PathRoleRule(
            role="runtime",
            prefixes=("lib", "usr"),
        ),
    ),
    "requirements": (
        LICENSE_RULE,
        TOOL_VERSION_RULE,
        PathRoleRule(
            role="entrypoint",
            exact=("requirements-format", "usr/local/bin/python3"),
        ),
        PathRoleRule(
            role="runtime",
            prefixes=("lib", "usr"),
        ),
    ),
    "shfmt": (
        LICENSE_RULE,
        TOOL_VERSION_RULE,
        PathRoleRule(role="entrypoint", exact=("shfmt",)),
    ),
    "clang": (
        LICENSE_RULE,
        TOOL_VERSION_RULE,
        PathRoleRule(
            role="entrypoint",
            exact=("clang_format/data/bin/clang-format",),
        ),
        PathRoleRule(
            role="formatter",
            prefixes=("clang_format", "clang_format.libs"),
        ),
        PathRoleRule(role="runtime", prefixes=("lib",)),
    ),
    "java": (
        LICENSE_RULE,
        TOOL_VERSION_RULE,
        PathRoleRule(
            role="entrypoint",
            exact=("usr/local/bin/google-java-format",),
        ),
        PathRoleRule(
            role="runtime",
            exact=(".",),
            prefixes=BASE_RUNTIME_PREFIXES,
        ),
    ),
    "go": (
        LICENSE_RULE,
        TOOL_VERSION_RULE,
        PathRoleRule(role="entrypoint", exact=("gofmt",)),
    ),
    "rust": (
        LICENSE_RULE,
        TOOL_VERSION_RULE,
        PathRoleRule(role="entrypoint", exact=("bin/rustfmt",)),
        PathRoleRule(role="formatter", prefixes=("lib/librustc_driver",)),
        PathRoleRule(role="runtime", prefixes=("bin", "lib", "usr")),
    ),
    "kotlin": (
        LICENSE_RULE,
        TOOL_VERSION_RULE,
        PathRoleRule(role="entrypoint", exact=("kotlin-format",)),
        PathRoleRule(role="formatter", exact=("ktlint.jar",)),
        PathRoleRule(
            role="runtime",
            prefixes=("lib", "opt", "usr"),
        ),
    ),
    "taplo": (
        LICENSE_RULE,
        TOOL_VERSION_RULE,
        PathRoleRule(role="entrypoint", exact=("taplo",)),
    ),
    "xml": (
        LICENSE_RULE,
        TOOL_VERSION_RULE,
        PathRoleRule(role="entrypoint", exact=("xml-format",)),
        PathRoleRule(
            role="formatter",
            exact=("opt",),
            prefixes=("opt/libxml2",),
        ),
        PathRoleRule(role="runtime", prefixes=("lib",)),
    ),
    "swift": (
        LICENSE_RULE,
        TOOL_VERSION_RULE,
        PathRoleRule(role="entrypoint", exact=("usr/bin/swift-format",)),
        PathRoleRule(
            role="runtime",
            exact=(".",),
            prefixes=BASE_RUNTIME_PREFIXES,
        ),
    ),
    "csharp": (
        LICENSE_RULE,
        TOOL_VERSION_RULE,
        PathRoleRule(role="entrypoint", exact=("usr/share/dotnet/dotnet",)),
        PathRoleRule(role="formatter", prefixes=("tools",)),
        PathRoleRule(
            role="runtime",
            prefixes=("lib", "usr"),
        ),
    ),
    "julia": (
        LICENSE_RULE,
        TOOL_VERSION_RULE,
        PathRoleRule(
            role="entrypoint",
            exact=("julia-format.jl", "usr/local/julia/bin/julia"),
        ),
        PathRoleRule(role="formatter", prefixes=("opt",)),
        PathRoleRule(
            role="runtime",
            prefixes=("lib", "lib64", "usr"),
        ),
    ),
}


def load_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def image_rows() -> list[dict[str, Any]]:
    matrix = load_object(MATRIX_PATH)
    rows = matrix.get("images")
    if not isinstance(rows, list):
        raise ValueError("image matrix is missing images")
    parsed: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("image rows must be objects")
        parsed.append(row)
    return parsed


def validate_coverage() -> None:
    manifest = load_object(LANGUAGES_PATH)
    language_rows = manifest.get("languages")
    if not isinstance(language_rows, list):
        raise ValueError("language manifest is missing languages")
    expected = {
        row["id"]
        for row in language_rows
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }

    found: list[str] = []
    targets: list[str] = []
    for row in image_rows():
        languages = row.get("languages")
        target = row.get("target")
        budget = row.get("budget_mib")
        if not isinstance(languages, list):
            raise ValueError("image languages must be a list")
        if not isinstance(target, str):
            raise ValueError("image target must be a string")
        if not isinstance(budget, int):
            raise ValueError("image budget must be an integer")
        found.extend(str(language) for language in languages)
        targets.append(target)

    if len(found) != len(set(found)):
        raise ValueError("a language appears in more than one image row")
    if set(found) != expected:
        missing = sorted(expected - set(found))
        extra = sorted(set(found) - expected)
        raise ValueError(f"image coverage differs: missing={missing} extra={extra}")

    target_set = set(targets)
    policy_targets = set(PATH_ROLE_POLICY)
    if target_set != policy_targets:
        missing = sorted(target_set - policy_targets)
        extra = sorted(policy_targets - target_set)
        raise ValueError(f"image role policy differs: missing={missing} extra={extra}")
    for target, rules in PATH_ROLE_POLICY.items():
        if not rules:
            raise ValueError(f"image target has no path-role rules: {target}")
        for rule in rules:
            if rule.role not in ENTRY_ROLES:
                raise ValueError(f"image target has an invalid path role: {target}")

    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    for target in targets:
        pattern = rf"\bAS\s+{re.escape(target)}\b"
        if re.search(pattern, dockerfile) is None:
            raise ValueError(f"Dockerfile target is missing: {target}")


def language_count() -> int:
    manifest = load_object(LANGUAGES_PATH)
    languages = manifest.get("languages")
    if not isinstance(languages, list):
        raise ValueError("language manifest is missing languages")
    return len(languages)


def validate_sources() -> None:
    sources = load_object(SOURCES_PATH)
    base_images = sources.get("base_images")
    downloads = sources.get("downloads")
    if not isinstance(base_images, dict):
        raise ValueError("sources are missing base_images")
    if not isinstance(downloads, dict):
        raise ValueError("sources are missing downloads")
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    for option in ('"--print-width", "60"', '"--prose-wrap", "always"'):
        if option not in dockerfile:
            raise ValueError(f"Prettier image is missing locked option: {option}")
    if '"--trailing-comma", "none"' not in dockerfile:
        raise ValueError("Prettier image is missing its trailing-comma lock")
    if '"--ignore-path", "/work/.lint-empty-ignore"' not in dockerfile:
        raise ValueError("Prettier image is missing its controlled ignore path")
    for blocked in ("--no-config", "--no-editorconfig", "--ignore-unknown"):
        if blocked in dockerfile:
            raise ValueError(f"Prettier image blocks native policy: {blocked}")
    black_lock = '"--line-length", "88"'
    if black_lock not in dockerfile:
        raise ValueError("Black image is missing its line-length lock")
    if '"--config", "/dev/null"' in dockerfile:
        raise ValueError("Black image blocks native configuration")
    for item in base_images.values():
        if not isinstance(item, dict):
            raise ValueError("base image entries must be objects")
        digest = item.get("digest")
        if not isinstance(digest, str):
            raise ValueError("base image digest must be a string")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
            raise ValueError(f"invalid base image digest: {digest}")
        if digest not in dockerfile:
            raise ValueError(f"base image digest is unused: {digest}")
    for item in downloads.values():
        if not isinstance(item, dict):
            raise ValueError("download entries must be objects")
        checksum = item.get("sha256")
        if not isinstance(checksum, str):
            raise ValueError("download checksum must be a string")
        if re.fullmatch(r"[0-9a-f]{64}", checksum) is None:
            raise ValueError(f"invalid download checksum: {checksum}")
        if checksum not in dockerfile:
            raise ValueError(f"download checksum is unused: {checksum}")


class ByteCounter:
    def __init__(self) -> None:
        self.count = 0

    def write(self, payload: bytes) -> int:
        self.count += len(payload)
        return len(payload)

    def flush(self) -> None:
        return None


def compressed_image_size(image: str) -> int:
    inspect = subprocess.run(
        ["docker", "image", "inspect", image],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if inspect.returncode != 0:
        detail = inspect.stderr.strip()
        raise ValueError(f"image is unavailable: {image}: {detail}")

    saved = subprocess.Popen(
        ["docker", "image", "save", image],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if saved.stdout is None:
        raise RuntimeError("docker image save did not expose standard output")
    counter = ByteCounter()
    with gzip.GzipFile(
        filename="",
        mode="wb",
        fileobj=counter,
        mtime=0,
    ) as compressed:
        while True:
            block = saved.stdout.read(1024 * 1024)
            if block == b"":
                break
            compressed.write(block)
    standard_error = b""
    if saved.stderr is not None:
        standard_error = saved.stderr.read()
    return_code = saved.wait()
    if return_code != 0:
        detail = standard_error.decode("utf-8", errors="replace").strip()
        raise ValueError(f"could not save image {image}: {detail}")
    return counter.count


def verify_image_budget(image: str, budget_mib: int) -> int:
    size = compressed_image_size(image)
    maximum = budget_mib * 1024 * 1024
    if size > maximum:
        raise ValueError(f"{image} is {size} compressed bytes; budget is {maximum}")
    return size


def inventory_path(
    target: str,
    architecture: str,
    root: Path | None = None,
) -> Path:
    """Return the reviewed manifest path for one final image."""
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", target) is None:
        raise ValueError(f"invalid image target: {target}")
    if architecture not in ARCHITECTURES:
        raise ValueError(f"unsupported image architecture: {architecture}")
    inventory_root = INVENTORY_ROOT
    if root is not None:
        inventory_root = root
    return inventory_root / f"{target}-{architecture}.json"


def normalized_archive_path(value: str) -> str:
    """Return a canonical relative path from one tar member."""
    normalized = value
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.rstrip("/")
    if normalized == "":
        normalized = "."
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe image archive path: {value}")
    return path.as_posix()


def path_matches_prefix(path: str, prefix: str) -> bool:
    """Return whether a canonical path is in one reviewed subtree."""
    return path == prefix or path.startswith(prefix + "/")


def classify_path_role(target: str, path: str) -> str:
    """Assign one role through the ordered, target-specific policy."""
    policy = PATH_ROLE_POLICY.get(target)
    if policy is None:
        raise ValueError(f"image target has no path-role policy: {target}")
    for rule in policy:
        if rule.role not in ENTRY_ROLES:
            raise ValueError(f"image target has an invalid path role: {target}")
        if path in rule.exact:
            return rule.role
        for prefix in rule.prefixes:
            if path_matches_prefix(path, prefix):
                return rule.role
    raise ValueError(f"image path has no reviewed role for {target}: {path}")


def archive_member_type(member: tarfile.TarInfo) -> str:
    """Return the strict inventory type for one tar member."""
    if member.isfile():
        return "regular"
    if member.isdir():
        return "directory"
    if member.issym():
        return "symlink"
    if member.islnk():
        return "hardlink"
    if member.ischr():
        return "character"
    if member.isblk():
        return "block"
    if member.isfifo():
        return "fifo"
    raise ValueError(f"unsupported image archive entry type: {member.name}")


def regular_file_sha256(
    handle: tarfile.TarFile,
    member: tarfile.TarInfo,
) -> str:
    """Hash one regular file directly from the exported archive."""
    source = handle.extractfile(member)
    if source is None:
        raise ValueError(f"could not read image archive entry: {member.name}")
    digest = hashlib.sha256()
    with source:
        while True:
            payload = source.read(1024 * 1024)
            if payload == b"":
                break
            digest.update(payload)
    return digest.hexdigest()


def inventory_entry(
    handle: tarfile.TarFile,
    member: tarfile.TarInfo,
    target: str,
) -> dict[str, Any]:
    """Build one path-, metadata-, and content-bound inventory entry."""
    entry_type = archive_member_type(member)
    link_target: str | None = None
    sha256: str | None = None
    if entry_type in {"symlink", "hardlink"}:
        link_target = member.linkname
    if entry_type == "regular":
        sha256 = regular_file_sha256(handle, member)
    path = normalized_archive_path(member.name)
    return {
        "path": path,
        "type": entry_type,
        "mode": f"{member.mode & 0o7777:04o}",
        "link_target": link_target,
        "sha256": sha256,
        "role": classify_path_role(target, path),
    }


def validate_inventory(value: Any) -> dict[str, Any]:
    """Require the complete strict schema and canonical entry order."""
    if not isinstance(value, dict):
        raise ValueError("image inventory must contain an object")
    if set(value) != INVENTORY_KEYS:
        raise ValueError("image inventory has unexpected top-level fields")
    schema_version = value.get("schema_version")
    if type(schema_version) is not int:
        raise ValueError("image inventory schema version must be an integer")
    if schema_version != INVENTORY_SCHEMA_VERSION:
        raise ValueError("image inventory has the wrong schema version")
    target = value.get("target")
    if not isinstance(target, str):
        raise ValueError("image inventory target must be a string")
    architecture = value.get("architecture")
    if not isinstance(architecture, str):
        raise ValueError("image inventory architecture must be a string")
    inventory_path(target, architecture)
    entries = value.get("entries")
    if not isinstance(entries, list):
        raise ValueError("image inventory entries must be a list")

    paths: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
            raise ValueError("image inventory entry has unexpected fields")
        path = entry.get("path")
        if not isinstance(path, str):
            raise ValueError("image inventory path must be a string")
        if normalized_archive_path(path) != path:
            raise ValueError(f"image inventory path is not canonical: {path}")
        if (
            target == "black"
            and path_matches_prefix(
                path,
                "usr/local/lib/python3.13/site-packages",
            )
            and path.endswith(".pyc")
        ):
            raise ValueError(
                f"black inventory contains generated package bytecode: {path}"
            )
        entry_type = entry.get("type")
        if entry_type not in ENTRY_TYPES:
            raise ValueError(f"image inventory entry has invalid type: {path}")
        mode = entry.get("mode")
        if not isinstance(mode, str) or re.fullmatch(r"[0-7]{4}", mode) is None:
            raise ValueError(f"image inventory entry has invalid mode: {path}")
        link_target = entry.get("link_target")
        sha256 = entry.get("sha256")
        role = entry.get("role")
        if role not in ENTRY_ROLES:
            raise ValueError(f"image inventory entry has invalid role: {path}")
        reviewed_role = classify_path_role(target, path)
        if role != reviewed_role:
            raise ValueError(
                f"image inventory entry has wrong role: {path}: "
                f"expected {reviewed_role}, found {role}"
            )
        if entry_type in {"symlink", "hardlink"}:
            if not isinstance(link_target, str) or link_target == "":
                raise ValueError(f"image inventory link target is invalid: {path}")
        elif link_target is not None:
            raise ValueError(f"image inventory link target is invalid: {path}")
        if entry_type == "regular":
            if not isinstance(sha256, str):
                raise ValueError(f"image inventory digest is invalid: {path}")
            if re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
                raise ValueError(f"image inventory digest is invalid: {path}")
        elif sha256 is not None:
            raise ValueError(f"image inventory digest is invalid: {path}")
        paths.append(path)

    if paths != sorted(paths):
        raise ValueError("image inventory entries are not sorted by path")
    if len(paths) != len(set(paths)):
        raise ValueError("image inventory contains duplicate paths")
    return value


def remove_entry_tree(entries: dict[str, dict[str, Any]], path: str) -> None:
    """Remove one path and all descendants from a flattened layer view."""
    descendants = [
        candidate
        for candidate in entries
        if candidate == path or candidate.startswith(path + "/")
    ]
    for candidate in descendants:
        del entries[candidate]


def whiteout_target(path: str) -> tuple[str, bool] | None:
    """Return the deleted path and opaque flag for one whiteout marker."""
    parsed = PurePosixPath(path)
    name = parsed.name
    if name == ".wh..wh..opq":
        parent = parsed.parent.as_posix()
        return parent, True
    if not name.startswith(".wh."):
        return None
    removed_name = name.removeprefix(".wh.")
    parent = parsed.parent
    removed = (parent / removed_name).as_posix()
    return removed, False


def apply_layer(
    entries: dict[str, dict[str, Any]],
    handle: tarfile.TarFile,
    target: str,
) -> None:
    """Apply one image layer with OCI/Docker whiteout semantics."""
    additions: list[dict[str, Any]] = []
    whiteouts: list[tuple[str, bool]] = []
    layer_paths: set[str] = set()
    for member in handle:
        path = normalized_archive_path(member.name)
        whiteout = whiteout_target(path)
        if whiteout is not None:
            whiteouts.append(whiteout)
            continue
        entry = inventory_entry(handle, member, target)
        if path in layer_paths:
            raise ValueError(f"image layer contains duplicate path: {path}")
        layer_paths.add(path)
        additions.append(entry)

    for path, opaque in whiteouts:
        if opaque:
            prefix = ""
            if path != ".":
                prefix = path + "/"
            descendants = [
                candidate
                for candidate in entries
                if candidate != path and candidate.startswith(prefix)
            ]
            for candidate in descendants:
                del entries[candidate]
            continue
        remove_entry_tree(entries, path)

    for entry in additions:
        path = entry["path"]
        existing = entries.get(path)
        if existing is not None:
            preserve_descendants = (
                existing["type"] == "directory" and entry["type"] == "directory"
            )
            if not preserve_descendants:
                remove_entry_tree(entries, path)
        entries[path] = entry


def load_saved_image_manifest(handle: tarfile.TarFile) -> dict[str, Any]:
    """Load the single Docker image-save manifest record."""
    try:
        member = handle.getmember("manifest.json")
    except KeyError as error:
        raise ValueError("saved image archive is missing manifest.json") from error
    source = handle.extractfile(member)
    if source is None:
        raise ValueError("saved image manifest cannot be read")
    with source:
        try:
            value = json.load(source)
        except json.JSONDecodeError as error:
            raise ValueError("saved image manifest contains invalid JSON") from error
    if not isinstance(value, list) or len(value) != 1:
        raise ValueError("saved image archive must contain exactly one image")
    record = value[0]
    if not isinstance(record, dict):
        raise ValueError("saved image manifest record must be an object")
    return record


def apply_saved_layer(
    outer: tarfile.TarFile,
    layer_name: str,
    entries: dict[str, dict[str, Any]],
    target: str,
) -> None:
    """Read and apply one named layer without extracting it to disk."""
    if normalized_archive_path(layer_name) != layer_name:
        raise ValueError(f"saved image layer path is not canonical: {layer_name}")
    try:
        member = outer.getmember(layer_name)
    except KeyError as error:
        raise ValueError(
            f"saved image archive is missing layer: {layer_name}"
        ) from error
    source = outer.extractfile(member)
    if source is None:
        raise ValueError(f"saved image layer cannot be read: {layer_name}")
    with source, tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024) as payload:
        shutil.copyfileobj(source, payload)
        payload.seek(0)
        with tarfile.open(fileobj=payload, mode="r:*") as layer:
            apply_layer(entries, layer, target)


def inventory_from_saved_image(
    archive: Path,
    target: str,
    architecture: str,
) -> dict[str, Any]:
    """Flatten the immutable layers from one Docker image-save archive."""
    flattened: dict[str, dict[str, Any]] = {}
    with tarfile.open(archive, "r:*") as outer:
        manifest = load_saved_image_manifest(outer)
        layers = manifest.get("Layers")
        if not isinstance(layers, list) or not layers:
            raise ValueError("saved image manifest has no layers")
        for layer_name in layers:
            if not isinstance(layer_name, str):
                raise ValueError("saved image layer name must be a string")
            apply_saved_layer(outer, layer_name, flattened, target)
    entries = sorted(flattened.values(), key=lambda entry: entry["path"])
    return validate_inventory(
        {
            "schema_version": INVENTORY_SCHEMA_VERSION,
            "target": target,
            "architecture": architecture,
            "entries": entries,
        }
    )


def render_inventory(value: Any) -> bytes:
    """Encode one validated inventory in its only accepted form."""
    validated = validate_inventory(value)
    return (json.dumps(validated, sort_keys=True, indent=2) + "\n").encode("utf-8")


def load_inventory(target: str, architecture: str) -> dict[str, Any]:
    """Load one canonical, reviewed target-and-architecture manifest."""
    path = inventory_path(target, architecture)
    payload = path.read_bytes()
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError(f"image inventory is invalid JSON: {path}") from error
    validated = validate_inventory(value)
    if validated.get("target") != target:
        raise ValueError(f"image inventory has the wrong target: {path}")
    if validated.get("architecture") != architecture:
        raise ValueError(f"image inventory has the wrong architecture: {path}")
    if render_inventory(validated) != payload:
        raise ValueError(f"image inventory is not canonically encoded: {path}")
    return validated


def compare_inventories(expected: Any, actual: Any) -> None:
    """Reject every missing, extra, or metadata/content-changed entry."""
    expected_value = validate_inventory(expected)
    actual_value = validate_inventory(actual)
    for field in ("target", "architecture"):
        if expected_value[field] != actual_value[field]:
            raise ValueError(
                f"image inventory {field} differs: "
                f"expected {expected_value[field]}, found {actual_value[field]}"
            )
    expected_entries = {entry["path"]: entry for entry in expected_value["entries"]}
    actual_entries = {entry["path"]: entry for entry in actual_value["entries"]}
    missing = sorted(set(expected_entries) - set(actual_entries))
    extra = sorted(set(actual_entries) - set(expected_entries))
    if missing or extra:
        raise ValueError(
            f"image inventory paths differ: missing={missing} extra={extra}"
        )
    for path in sorted(expected_entries):
        expected_entry = expected_entries[path]
        actual_entry = actual_entries[path]
        for field in ("type", "mode", "link_target", "sha256", "role"):
            if expected_entry[field] != actual_entry[field]:
                raise ValueError(
                    f"image inventory entry differs: {path} {field}: "
                    f"expected {expected_entry[field]!r}, "
                    f"found {actual_entry[field]!r}"
                )


def image_architecture(image: str) -> str:
    """Read the architecture bound to a locally loaded image."""
    completed = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{.Architecture}}"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip()
        raise ValueError(f"image is unavailable: {image}: {detail}")
    architecture = completed.stdout.strip()
    if architecture not in ARCHITECTURES:
        raise ValueError(f"image has unsupported architecture: {architecture}")
    return architecture


def export_image_inventory(
    image: str,
    target: str,
    architecture: str,
) -> dict[str, Any]:
    """Save and inventory one exact image without creating a container."""
    actual_architecture = image_architecture(image)
    if actual_architecture != architecture:
        raise ValueError(
            f"image architecture differs: expected {architecture}, "
            f"found {actual_architecture}"
        )
    descriptor, archive_name = tempfile.mkstemp(
        prefix="lint-image-",
        suffix=".tar",
    )
    os.close(descriptor)
    archive = Path(archive_name)
    try:
        saved = subprocess.run(
            ["docker", "image", "save", "--output", str(archive), image],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if saved.returncode != 0:
            detail = saved.stderr.strip()
            raise ValueError(f"could not save image {image}: {detail}")
        return inventory_from_saved_image(archive, target, architecture)
    finally:
        archive.unlink(missing_ok=True)


def validate_required_notices(image: str, inventory: dict[str, Any]) -> None:
    """Keep common and target-local legal payloads in every final image."""
    paths = {entry["path"] for entry in inventory["entries"]}
    target = inventory.get("target")
    if not isinstance(target, str):
        raise ValueError(f"{image} inventory has no target")
    required = set(REQUIRED_LICENSE_PATHS)
    required.add(f"licenses/{target}/manifest.json")
    missing = sorted(required - paths)
    if missing:
        raise ValueError(f"{image} is missing license notices: {', '.join(missing)}")


def verify_image_contents(
    image: str,
    target: str,
    architecture: str,
) -> int:
    """Compare every final-image entry with its reviewed allowlist."""
    expected = load_inventory(target, architecture)
    actual = export_image_inventory(image, target, architecture)
    validate_required_notices(image, actual)
    compare_inventories(expected, actual)
    return len(actual["entries"])


def local_sizes(prefix: str) -> dict[str, int]:
    sizes: dict[str, int] = {}
    version = load_object(MATRIX_PATH)["version"]
    for row in image_rows():
        budget = row["budget_mib"]
        target = row["target"]
        for language in row["languages"]:
            image = f"{prefix}-{language}:{version}"
            sizes[image] = verify_image_budget(image, budget)
            architecture = image_architecture(image)
            verify_image_contents(image, target, architecture)
    return sizes


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument(
        "--local-prefix",
        help="validate every locally loaded PREFIX-language image",
    )
    argument_parser.add_argument("--image")
    argument_parser.add_argument("--budget-mib", type=int)
    argument_parser.add_argument("--target")
    argument_parser.add_argument("--architecture", choices=sorted(ARCHITECTURES))
    return argument_parser


def main() -> int:
    arguments = parser().parse_args()
    validate_coverage()
    validate_sources()
    response: dict[str, Any] = {
        "status": "ok",
        "languages": language_count(),
        "targets": len(image_rows()),
    }
    if arguments.image is not None:
        if arguments.budget_mib is None:
            raise ValueError("--image requires --budget-mib")
        if arguments.target is None:
            raise ValueError("--image requires --target")
        if arguments.architecture is None:
            raise ValueError("--image requires --architecture")
        response["compressed_bytes"] = verify_image_budget(
            arguments.image,
            arguments.budget_mib,
        )
        response["filesystem_entries"] = verify_image_contents(
            arguments.image,
            arguments.target,
            arguments.architecture,
        )
    elif arguments.budget_mib is not None:
        raise ValueError("--budget-mib requires --image")
    elif arguments.target is not None or arguments.architecture is not None:
        raise ValueError("--target and --architecture require --image")
    if arguments.local_prefix is not None:
        response["compressed_bytes"] = local_sizes(arguments.local_prefix)
    print(json.dumps(response, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
