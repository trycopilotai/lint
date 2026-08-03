#!/usr/bin/env python3
"""Verify independently derived dependency-closure receipts."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CLOSURE_PATH = ROOT / "images" / "dependency_closures.json"
SOURCES_PATH = ROOT / "images" / "sources.json"
LEGAL_SOURCES_PATH = ROOT / "images" / "license_sources.json"
DOCKERFILE_PATH = ROOT / "images" / "Dockerfile"
EXPECTED_CLOSURES = frozenset(
    {
        "cargo",
        "csharpier",
        "go_buildifier",
        "go_shfmt",
        "julia",
        "ktlint",
        "swift",
    }
)
TARGETS = {
    "cargo": "taplo",
    "csharpier": "csharp",
    "go_buildifier": "buildifier",
    "go_shfmt": "shfmt",
    "julia": "julia",
    "ktlint": "kotlin",
    "swift": "swift",
}
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


def load_document() -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate dependency closure key: {key}")
            value[key] = item
        return value

    return json.loads(
        CLOSURE_PATH.read_text(encoding="utf-8"),
        object_pairs_hook=unique_object,
    )


def validate_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def validate_source_path(
    closure: dict[str, Any],
    root: Path,
) -> bytes | None:
    source_path = closure.get("source_path")
    source_sha = closure.get("source_sha256")
    if source_path is None and source_sha is None:
        return None
    if not isinstance(source_path, str) or not isinstance(source_sha, str):
        raise ValueError("dependency closure source binding is incomplete")
    path = root / source_path
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"dependency closure source is not a regular file: {path}")
    payload = path.read_bytes()
    validate_sha256(source_sha, "dependency closure source sha256")
    if sha256(payload) != source_sha:
        raise ValueError(f"dependency closure source checksum differs: {source_path}")
    return payload


def legal_outputs(target: str, root: Path) -> set[str]:
    path = root / "images" / "licenses" / target / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"legal manifest entries are invalid: {target}")
    return {str(entry["path"]) for entry in entries}


def validate_mapping_shape(
    closure_name: str,
    closure: dict[str, Any],
    root: Path,
) -> list[dict[str, Any]]:
    mappings = closure.get("mappings")
    if not isinstance(mappings, list) or not mappings:
        raise ValueError(f"dependency closure mappings are missing: {closure_name}")
    expected_keys = {"legal_outputs", "match"}
    matches: list[str] = []
    allowed_outputs = legal_outputs(TARGETS[closure_name], root)
    for mapping in mappings:
        if not isinstance(mapping, dict) or set(mapping) != expected_keys:
            raise ValueError(f"dependency closure mapping is invalid: {closure_name}")
        match = mapping.get("match")
        outputs = mapping.get("legal_outputs")
        if not isinstance(match, str) or match == "":
            raise ValueError(f"dependency closure match is invalid: {closure_name}")
        if not isinstance(outputs, list) or not outputs:
            raise ValueError(f"dependency closure outputs are invalid: {closure_name}")
        if outputs != sorted(set(outputs)):
            raise ValueError(
                f"dependency closure outputs are not sorted and unique: {closure_name}"
            )
        if not all(output in allowed_outputs for output in outputs):
            raise ValueError(
                f"dependency closure output is absent from legal manifest: {closure_name}"
            )
        matches.append(match)
    if matches != sorted(set(matches)):
        raise ValueError(
            f"dependency closure mappings are not sorted and unique: {closure_name}"
        )
    return mappings


def parse_go_receipt(payload: bytes) -> dict[str, Any]:
    go_version = ""
    main: dict[str, str] | None = None
    modules: list[dict[str, str]] = []
    for line in payload.decode("utf-8").splitlines():
        fields = line.split()
        if fields[:1] == ["go"] and len(fields) == 2:
            go_version = fields[1]
        if fields[:1] == ["main"] and len(fields) in {3, 4}:
            main = {"path": fields[1], "version": fields[2]}
            if len(fields) == 4:
                main["sum"] = fields[3]
        if fields[:1] == ["dep"] and len(fields) == 4:
            modules.append({"path": fields[1], "sum": fields[3], "version": fields[2]})
    modules.sort(key=lambda item: item["path"])
    if go_version == "" or main is None:
        raise ValueError("Go build information receipt is incomplete")
    return {"go_version": go_version, "main": main, "modules": modules}


def validate_go(
    closure_name: str,
    closure: dict[str, Any],
    payload: bytes | None,
    mappings: list[dict[str, Any]],
) -> None:
    if payload is None:
        raise ValueError(f"Go closure receipt is missing: {closure_name}")
    receipt = closure.get("receipt")
    if receipt != parse_go_receipt(payload):
        raise ValueError(f"Go closure receipt differs: {closure_name}")
    identities = {"go:stdlib", str(receipt["main"]["path"])}
    identities.update(str(module["path"]) for module in receipt["modules"])
    matches = {str(mapping["match"]) for mapping in mappings}
    if identities != matches:
        raise ValueError(f"Go closure mapping differs: {closure_name}")
    modules = receipt["modules"]
    if len(modules) != len({module["path"] for module in modules}):
        raise ValueError(f"Go closure contains duplicate modules: {closure_name}")


def archive_member_json(payload: bytes) -> dict[str, Any]:
    marker = b"\n--- tools/net10.0/any/CSharpier.deps.json ---\n"
    parts = payload.split(marker)
    if len(parts) != 2:
        raise ValueError("CSharpier dependency receipt is malformed")
    return json.loads(parts[1])


def validate_csharpier(
    payload: bytes | None,
    mappings: list[dict[str, Any]],
) -> None:
    if payload is None:
        raise ValueError("CSharpier dependency receipt is missing")
    value = archive_member_json(payload)
    libraries = value.get("libraries")
    if not isinstance(libraries, dict) or not libraries:
        raise ValueError("CSharpier dependency receipt has no libraries")
    coordinates = sorted(str(value) for value in libraries)
    if len(coordinates) != len(set(coordinates)):
        raise ValueError("CSharpier dependency receipt contains duplicates")
    used_mappings: set[str] = set()
    for coordinate in coordinates:
        family = coordinate.rsplit("/", 1)[0]
        matches = [
            mapping
            for mapping in mappings
            if family == mapping["match"]
            or family.startswith(str(mapping["match"]) + ".")
        ]
        if len(matches) != 1:
            raise ValueError(f"CSharpier dependency mapping differs: {coordinate}")
        used_mappings.add(str(matches[0]["match"]))
    expected_mappings = {str(mapping["match"]) for mapping in mappings}
    if used_mappings != expected_mappings:
        raise ValueError("CSharpier dependency mapping contains an unused family")


def validate_swift(
    payload: bytes | None,
    mappings: list[dict[str, Any]],
) -> None:
    if payload is None:
        raise ValueError("Swift dependency receipt is missing")
    value = json.loads(payload)
    pins = value.get("pins")
    if not isinstance(pins, list):
        raise ValueError("Swift dependency receipt has no pins")
    derived: set[str] = set()
    for pin in pins:
        state = pin.get("state")
        if not isinstance(state, dict):
            raise ValueError("Swift dependency pin has no state")
        derived.add(f"{pin['identity']}@{state['version']}#{state['revision']}")
    matches = {str(mapping["match"]) for mapping in mappings}
    if len(pins) != len(derived) or derived != matches:
        raise ValueError("Swift dependency closure mapping differs")


def validate_julia(
    payload: bytes | None,
    mappings: list[dict[str, Any]],
) -> None:
    if payload is None:
        raise ValueError("Julia dependency receipt is missing")
    value = tomllib.loads(payload.decode("utf-8"))
    deps = value.get("deps")
    if not isinstance(deps, dict):
        raise ValueError("Julia dependency receipt has no dependencies")
    derived: set[str] = set()
    for name, records in deps.items():
        if not isinstance(records, list) or len(records) != 1:
            continue
        record = records[0]
        tree = record.get("git-tree-sha1")
        if isinstance(tree, str):
            derived.add(f"{name}@{record['version']}#{tree}")
        if name == "JuliaFormatter" and record.get("path"):
            derived.add(f"{name}@{record['version']}#local")
    matches = {str(mapping["match"]) for mapping in mappings}
    if derived != matches:
        raise ValueError("Julia dependency closure mapping differs")


def validate_ktlint(
    closure: dict[str, Any],
    payload: bytes | None,
    mappings: list[dict[str, Any]],
) -> None:
    if payload is None:
        raise ValueError("KtLint dependency receipt is missing")
    lines = payload.decode("utf-8").splitlines()
    derived = sorted(
        line.removeprefix("component ")
        for line in lines
        if line.startswith("component ")
    )
    receipt = closure.get("receipt")
    if not isinstance(receipt, dict):
        raise ValueError("KtLint typed receipt is missing")
    source_commit = receipt.get("source_commit")
    if not isinstance(source_commit, str):
        raise ValueError("KtLint source commit is missing")
    if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise ValueError("KtLint source commit is invalid")
    components = receipt.get("components")
    if components != sorted(set(derived)):
        raise ValueError("KtLint runtimeClasspath receipt differs")
    matches = [str(mapping["match"]) for mapping in mappings]
    if matches != components:
        raise ValueError("KtLint runtimeClasspath mapping differs")


def validate_cargo(
    closure: dict[str, Any],
    payload: bytes | None,
    mappings: list[dict[str, Any]],
) -> None:
    if payload is None:
        raise ValueError("Cargo dependency receipt is missing")
    text = payload.decode("utf-8")
    pattern = re.compile(
        r"^Package: (?P<name>\S+) (?P<version>\S+)\n"
        r"Source: .*\nSource SHA-256: (?P<checksum>[0-9a-f]{64})$",
        flags=re.MULTILINE,
    )
    packages = [
        {
            "checksum": match.group("checksum"),
            "name": match.group("name"),
            "version": match.group("version"),
        }
        for match in pattern.finditer(text)
    ]
    packages.sort(key=lambda item: (item["name"], item["version"]))
    receipt = closure.get("receipt")
    if not isinstance(receipt, dict):
        raise ValueError("Cargo typed receipt is missing")
    if len(packages) != receipt.get("package_count"):
        raise ValueError("Cargo dependency receipt package count differs")
    inventory = (
        json.dumps(packages, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if sha256(inventory) != receipt.get("packages_sha256"):
        raise ValueError("Cargo dependency receipt inventory differs")
    if mappings != [{"legal_outputs": ["cargo-lock-legal-payload.txt"], "match": "*"}]:
        raise ValueError("Cargo dependency closure mapping differs")


def validate_external_bindings(
    closures: dict[str, dict[str, Any]],
    root: Path,
) -> None:
    sources = json.loads((root / "images" / "sources.json").read_text())
    legal_sources = json.loads((root / "images" / "license_sources.json").read_text())
    dockerfile = (root / "images" / "Dockerfile").read_text()
    for closure_name, closure in closures.items():
        download_name = closure.get("source_download")
        if download_name is not None:
            download = sources["downloads"].get(download_name)
            if not isinstance(download, dict):
                raise ValueError(
                    f"dependency closure download binding is missing: {closure_name}"
                )
            if download["sha256"] not in dockerfile:
                raise ValueError(
                    f"dependency closure download is not bound in Dockerfile: {closure_name}"
                )
        module_name = closure.get("source_module")
        if module_name is not None:
            module = sources["go_modules"].get(module_name)
            if not isinstance(module, dict):
                raise ValueError("dependency closure Go module binding is missing")
            if module["version"] not in dockerfile or module["sum"] not in dockerfile:
                raise ValueError(
                    "dependency closure Go module is not bound in Dockerfile"
                )

    for closure_name in ("swift", "julia"):
        lock_name = "swift_package"
        if closure_name == "julia":
            lock_name = "julia_manifest"
        closure = closures[closure_name]
        lock = sources["lockfiles"][lock_name]
        if closure["source_path"] != lock["path"]:
            raise ValueError(f"dependency closure lock path differs: {closure_name}")
        if closure["source_sha256"] != lock["sha256"]:
            raise ValueError(
                f"dependency closure lock checksum differs: {closure_name}"
            )

    cargo_bundle = legal_sources["cargo_bundles"][0]
    cargo = closures["cargo"]
    receipt = cargo["receipt"]
    if receipt["lock_sha256"] != cargo_bundle["lock_sha256"]:
        raise ValueError("Cargo closure lock checksum differs")
    if receipt["package_count"] != cargo_bundle["package_count"]:
        raise ValueError("Cargo closure package count differs")
    if receipt["packages_sha256"] != cargo_bundle["packages_sha256"]:
        raise ValueError("Cargo closure package inventory differs")


def validate_document(
    value: Any,
    root: Path = ROOT,
) -> None:
    if not isinstance(value, dict) or set(value) != {"closures", "schema_version"}:
        raise ValueError("dependency closure document has unexpected fields")
    if value.get("schema_version") != 1:
        raise ValueError("dependency closure schema version differs")
    closures = value.get("closures")
    if not isinstance(closures, dict) or set(closures) != set(EXPECTED_CLOSURES):
        raise ValueError("dependency closure set differs")

    for closure_name in sorted(closures):
        closure = closures[closure_name]
        if not isinstance(closure, dict):
            raise ValueError(f"dependency closure is invalid: {closure_name}")
        payload = validate_source_path(closure, root)
        mappings = validate_mapping_shape(closure_name, closure, root)
        kind = closure.get("kind")
        if closure_name.startswith("go_"):
            if kind != "go_build_info":
                raise ValueError("Go closure kind differs")
            validate_go(closure_name, closure, payload, mappings)
        elif closure_name == "csharpier":
            if kind != "dotnet_deps":
                raise ValueError("CSharpier closure kind differs")
            validate_csharpier(payload, mappings)
        elif closure_name == "swift":
            if kind != "swift_package_resolved":
                raise ValueError("Swift closure kind differs")
            validate_swift(payload, mappings)
        elif closure_name == "julia":
            if kind != "julia_manifest":
                raise ValueError("Julia closure kind differs")
            validate_julia(payload, mappings)
        elif closure_name == "ktlint":
            if kind != "gradle_runtime_classpath":
                raise ValueError("KtLint closure kind differs")
            validate_ktlint(closure, payload, mappings)
        elif closure_name == "cargo":
            if kind != "cargo_lock":
                raise ValueError("Cargo closure kind differs")
            validate_cargo(closure, payload, mappings)

    validate_external_bindings(closures, root)


def main() -> int:
    validate_document(load_document())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
