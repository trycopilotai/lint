#!/usr/bin/env python3
"""Generate target-local legal payloads from pinned sources."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import io
import json
import re
import shutil
import tarfile
import tempfile
import time
import tomllib
import urllib.request
import zipfile
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "images" / "license_sources.json"
OUTPUT_ROOT = ROOT / "images" / "licenses"
DOCKERFILE_PATH = ROOT / "images" / "Dockerfile"
SCHEMA_VERSION = 4
TARGETS = frozenset(
    {
        "black",
        "buildifier",
        "clang",
        "csharp",
        "go",
        "java",
        "julia",
        "kotlin",
        "prettier",
        "requirements",
        "rust",
        "shfmt",
        "swift",
        "taplo",
        "xml",
    }
)
SOURCE_DOCUMENT_KEYS = frozenset(
    {
        "archive_bundles",
        "cargo_bundles",
        "cargo_supplements",
        "schema_version",
        "sources",
    }
)
SOURCE_KEYS = frozenset(
    {
        "component",
        "license",
        "output",
        "sha256",
        "targets",
        "url",
        "version",
    }
)
MANIFEST_KEYS = frozenset({"entries", "schema_version", "target"})
MANIFEST_ENTRY_KEYS = frozenset(
    {
        "component",
        "license",
        "path",
        "payload_sha256",
        "source_sha256",
        "source_url",
        "version",
    }
)
CARGO_BUNDLE_KEYS = frozenset(
    {
        "component",
        "license",
        "lock_sha256",
        "lock_url",
        "output",
        "package_count",
        "packages_sha256",
        "payload_sha256",
        "target",
        "url_template",
        "version",
    }
)
CARGO_SUPPLEMENT_KEYS = frozenset(
    {
        "checksum",
        "evidence",
        "name",
        "payload_sha256",
        "repository_commit",
        "sources",
        "version",
    }
)
CARGO_SUPPLEMENT_SOURCE_KEYS = frozenset({"sha256", "url"})
ARCHIVE_BUNDLE_KEYS = frozenset(
    {
        "component",
        "license",
        "members",
        "output",
        "payload_sha256",
        "sha256",
        "targets",
        "url",
        "version",
    }
)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
LEGAL_NAME_PATTERN = re.compile(
    r"(?:copying|copyright|licen[cs]e|notice|unlicense)(?:[._-].*)?",
    flags=re.IGNORECASE,
)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def load_source_document() -> dict[str, Any]:
    value = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != SOURCE_DOCUMENT_KEYS:
        raise ValueError("legal source document has unexpected fields")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("legal source schema version differs")
    return value


def validate_output(output: str) -> None:
    parsed_output = PurePosixPath(output)
    if parsed_output.name != output:
        raise ValueError(f"legal source output is unsafe: {output}")
    if output in {".", "..", "manifest.json"}:
        raise ValueError(f"legal source output is unsafe: {output}")


def validate_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def load_sources() -> list[dict[str, Any]]:
    value = load_source_document()
    sources = value.get("sources")
    if not isinstance(sources, list):
        raise ValueError("legal sources must be a list")

    parsed: list[dict[str, Any]] = []
    paths: set[tuple[str, str]] = set()
    found_targets: set[str] = set()
    for source in sources:
        if not isinstance(source, dict) or set(source) != SOURCE_KEYS:
            raise ValueError("legal source entry has unexpected fields")
        for field in ("component", "license", "output", "url", "version"):
            field_value = source.get(field)
            if not isinstance(field_value, str) or field_value == "":
                raise ValueError(f"legal source {field} must be a string")

        output = str(source["output"])
        validate_output(output)

        validate_sha256(source.get("sha256"), "legal source sha256")

        url = str(source["url"])
        if not url.startswith("https://"):
            raise ValueError(f"legal source URL is not HTTPS: {url}")
        for mutable in ("/HEAD/", "/latest/", "/main/", "/master/"):
            if mutable in url:
                raise ValueError(f"legal source URL is mutable: {url}")

        targets = source.get("targets")
        if not isinstance(targets, list) or not targets:
            raise ValueError("legal source targets must be a nonempty list")
        if not all(isinstance(target, str) for target in targets):
            raise ValueError("legal source targets must be strings")
        if targets != sorted(set(targets)):
            raise ValueError("legal source targets must be sorted and unique")
        for target_value in targets:
            target = str(target_value)
            if target not in TARGETS:
                raise ValueError(f"legal source target is unknown: {target}")
            key = (target, output)
            if key in paths:
                raise ValueError(f"duplicate legal payload path: {key}")
            paths.add(key)
            found_targets.add(target)
        parsed.append(source)

    found_targets.update(bundle["target"] for bundle in load_cargo_bundles())
    for bundle in load_archive_bundles():
        found_targets.update(bundle["targets"])
    if found_targets != set(TARGETS):
        raise ValueError("legal source target set differs")
    return parsed


def load_archive_bundles() -> list[dict[str, Any]]:
    value = load_source_document()
    bundles = value.get("archive_bundles")
    if not isinstance(bundles, list):
        raise ValueError("archive legal bundles must be a list")

    parsed: list[dict[str, Any]] = []
    paths: set[tuple[str, str]] = set()
    for bundle in bundles:
        if not isinstance(bundle, dict) or set(bundle) != ARCHIVE_BUNDLE_KEYS:
            raise ValueError("archive legal bundle has unexpected fields")
        for field in (
            "component",
            "license",
            "output",
            "url",
            "version",
        ):
            field_value = bundle.get(field)
            if not isinstance(field_value, str) or field_value == "":
                raise ValueError(f"archive legal bundle {field} must be a string")
        output = str(bundle["output"])
        validate_output(output)
        validate_sha256(bundle.get("sha256"), "archive legal bundle sha256")
        validate_sha256(
            bundle.get("payload_sha256"),
            "archive legal bundle payload_sha256",
        )
        url = str(bundle["url"])
        if not url.startswith("https://"):
            raise ValueError("archive legal bundle URL is not HTTPS")
        for mutable in ("/HEAD/", "/latest/", "/main/", "/master/"):
            if mutable in url:
                raise ValueError(f"archive legal bundle URL is mutable: {url}")

        members = bundle.get("members")
        if not isinstance(members, list) or not members:
            raise ValueError("archive legal bundle members must be a list")
        if not all(isinstance(member, str) and member != "" for member in members):
            raise ValueError("archive legal bundle members must be strings")
        if members != sorted(set(members)):
            raise ValueError("archive legal bundle members must be sorted and unique")
        for member_value in members:
            member = PurePosixPath(str(member_value))
            if member.is_absolute() or ".." in member.parts:
                raise ValueError("archive legal bundle member is unsafe")

        targets = bundle.get("targets")
        if not isinstance(targets, list) or not targets:
            raise ValueError("archive legal bundle targets must be a list")
        if not all(isinstance(target, str) for target in targets):
            raise ValueError("archive legal bundle targets must be strings")
        if targets != sorted(set(targets)):
            raise ValueError("archive legal bundle targets must be sorted and unique")
        for target_value in targets:
            target = str(target_value)
            if target not in TARGETS:
                raise ValueError(f"archive legal bundle target is unknown: {target}")
            path_key = (target, output)
            if path_key in paths:
                raise ValueError(f"duplicate archive legal payload path: {path_key}")
            paths.add(path_key)
        parsed.append(bundle)
    return parsed


def load_cargo_bundles() -> list[dict[str, Any]]:
    value = load_source_document()
    bundles = value.get("cargo_bundles")
    if not isinstance(bundles, list):
        raise ValueError("cargo legal bundles must be a list")

    parsed: list[dict[str, Any]] = []
    paths: set[tuple[str, str]] = set()
    for bundle in bundles:
        if not isinstance(bundle, dict) or set(bundle) != CARGO_BUNDLE_KEYS:
            raise ValueError("cargo legal bundle has unexpected fields")
        for field in (
            "component",
            "license",
            "lock_url",
            "output",
            "target",
            "url_template",
            "version",
        ):
            field_value = bundle.get(field)
            if not isinstance(field_value, str) or field_value == "":
                raise ValueError(f"cargo legal bundle {field} must be a string")

        target = str(bundle["target"])
        if target not in TARGETS:
            raise ValueError(f"cargo legal bundle target is unknown: {target}")
        output = str(bundle["output"])
        validate_output(output)
        path_key = (target, output)
        if path_key in paths:
            raise ValueError(f"duplicate cargo legal payload path: {path_key}")
        paths.add(path_key)

        for field in (
            "lock_sha256",
            "packages_sha256",
            "payload_sha256",
        ):
            validate_sha256(
                bundle.get(field),
                f"cargo legal bundle {field}",
            )
        count = bundle.get("package_count")
        if not isinstance(count, int) or count <= 0:
            raise ValueError("cargo legal bundle package_count is invalid")
        lock_url = str(bundle["lock_url"])
        if not lock_url.startswith("https://"):
            raise ValueError("cargo legal bundle lock URL is not HTTPS")
        for mutable in ("/HEAD/", "/latest/", "/main/", "/master/"):
            if mutable in lock_url:
                raise ValueError(f"cargo legal bundle lock URL is mutable: {lock_url}")
        template = str(bundle["url_template"])
        required_fields = {"{name}", "{version}"}
        if not template.startswith("https://"):
            raise ValueError("cargo legal bundle URL template is not HTTPS")
        if not all(field in template for field in required_fields):
            raise ValueError("cargo legal bundle URL template is incomplete")
        parsed.append(bundle)
    return parsed


def load_cargo_supplements() -> dict[tuple[str, str, str], dict[str, Any]]:
    value = load_source_document()
    supplements = value.get("cargo_supplements")
    if not isinstance(supplements, list):
        raise ValueError("cargo legal supplements must be a list")

    parsed: dict[tuple[str, str, str], dict[str, Any]] = {}
    for supplement in supplements:
        if not isinstance(supplement, dict):
            raise ValueError("cargo legal supplement must be an object")
        if set(supplement) != CARGO_SUPPLEMENT_KEYS:
            raise ValueError("cargo legal supplement has unexpected fields")
        for field in ("evidence", "name", "repository_commit", "version"):
            field_value = supplement.get(field)
            if not isinstance(field_value, str) or field_value == "":
                raise ValueError(f"cargo legal supplement {field} must be a string")
        commit = str(supplement["repository_commit"])
        if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
            raise ValueError("cargo legal supplement repository commit is invalid")
        checksum = validate_sha256(
            supplement.get("checksum"),
            "cargo legal supplement checksum",
        )
        validate_sha256(
            supplement.get("payload_sha256"),
            "cargo legal supplement payload_sha256",
        )
        source_values = supplement.get("sources")
        if not isinstance(source_values, list) or not source_values:
            raise ValueError("cargo legal supplement sources must be a list")
        source_urls: list[str] = []
        for source in source_values:
            if not isinstance(source, dict):
                raise ValueError("cargo legal supplement source must be an object")
            if set(source) != CARGO_SUPPLEMENT_SOURCE_KEYS:
                raise ValueError("cargo legal supplement source has unexpected fields")
            url = source.get("url")
            if not isinstance(url, str) or not url.startswith("https://"):
                raise ValueError("cargo legal supplement source URL is invalid")
            validate_sha256(
                source.get("sha256"),
                "cargo legal supplement source sha256",
            )
            source_urls.append(url)
        if source_urls != sorted(set(source_urls)):
            raise ValueError("cargo legal supplement sources must be sorted and unique")
        key = (
            str(supplement["name"]),
            str(supplement["version"]),
            checksum,
        )
        if key in parsed:
            raise ValueError(f"duplicate cargo legal supplement: {key}")
        parsed[key] = supplement
    return parsed


def fetch(url: str) -> bytes:
    failure: Exception | None = None
    for attempt in range(3):
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "trycopilotai-lint-license-generator"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except Exception as error:
            failure = error
            if attempt < 2:
                time.sleep(attempt + 1)
    raise RuntimeError(f"could not download legal source: {url}") from failure


def manifest_entry(
    source: dict[str, Any],
    payload: bytes,
) -> dict[str, str]:
    checksum = str(source["sha256"])
    actual = sha256(payload)
    if actual != checksum:
        raise ValueError(
            f"legal source checksum differs: {source['url']}: "
            f"expected {checksum}, found {actual}"
        )
    return {
        "component": str(source["component"]),
        "license": str(source["license"]),
        "path": str(source["output"]),
        "payload_sha256": checksum,
        "source_sha256": checksum,
        "source_url": str(source["url"]),
        "version": str(source["version"]),
    }


def extract_archive_members(
    payload: bytes,
    requested: list[str],
) -> list[tuple[str, bytes]]:
    available: dict[str, bytes] = {}
    if zipfile.is_zipfile(io.BytesIO(payload)):
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                available[info.filename] = archive.read(info)
    else:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                handle = archive.extractfile(member)
                if handle is None:
                    raise ValueError(
                        f"archive legal bundle member is unreadable: {member.name}"
                    )
                available[member.name] = handle.read()

    extracted: list[tuple[str, bytes]] = []
    for requested_name in requested:
        matches = [
            name
            for name in available
            if name == requested_name or name.endswith("/" + requested_name)
        ]
        if len(matches) != 1:
            raise ValueError(
                f"archive legal bundle member set differs: {requested_name}"
            )
        extracted.append((requested_name, available[matches[0]]))
    return extracted


def archive_bundle_header(bundle: dict[str, Any]) -> bytes:
    return b"".join(
        [
            b"Archive-derived legal payload\n",
            f"Source: {bundle['url']}\n".encode("utf-8"),
            f"Source SHA-256: {bundle['sha256']}\n".encode("utf-8"),
            (
                "Members: "
                + json.dumps(bundle["members"], separators=(",", ":"))
                + "\n"
            ).encode("utf-8"),
        ]
    )


def render_archive_bundle(
    bundle: dict[str, Any],
    fetcher: Callable[[str], bytes] = fetch,
) -> bytes:
    source_payload = fetcher(str(bundle["url"]))
    actual = sha256(source_payload)
    expected = str(bundle["sha256"])
    if actual != expected:
        raise ValueError(
            f"archive legal bundle checksum differs: {bundle['url']}: "
            f"expected {expected}, found {actual}"
        )
    extracted = extract_archive_members(source_payload, bundle["members"])
    chunks = [archive_bundle_header(bundle)]
    for member, member_payload in extracted:
        chunks.extend(
            [
                f"\n--- {member} ---\n".encode("utf-8"),
                member_payload,
            ]
        )
        if not member_payload.endswith(b"\n"):
            chunks.append(b"\n")
    return b"".join(chunks)


def archive_manifest_entry(
    bundle: dict[str, Any],
    payload: bytes,
) -> dict[str, str]:
    actual = sha256(payload)
    expected = str(bundle["payload_sha256"])
    if actual != expected:
        raise ValueError(
            f"archive legal bundle payload checksum differs: "
            f"expected {expected}, found {actual}"
        )
    if not payload.startswith(archive_bundle_header(bundle)):
        raise ValueError("archive legal bundle metadata differs")
    return {
        "component": str(bundle["component"]),
        "license": str(bundle["license"]),
        "path": str(bundle["output"]),
        "payload_sha256": expected,
        "source_sha256": str(bundle["sha256"]),
        "source_url": str(bundle["url"]),
        "version": str(bundle["version"]),
    }


def cargo_packages(lock_payload: bytes) -> list[dict[str, str]]:
    lock = tomllib.loads(lock_payload.decode("utf-8"))
    packages_value = lock.get("package")
    if not isinstance(packages_value, list):
        raise ValueError("cargo legal bundle lock has no packages")
    packages: list[dict[str, str]] = []
    for package in packages_value:
        if not isinstance(package, dict):
            raise ValueError("cargo legal bundle package is invalid")
        source = package.get("source")
        if source != "registry+https://github.com/rust-lang/crates.io-index":
            continue
        name = package.get("name")
        version = package.get("version")
        checksum = package.get("checksum")
        if not isinstance(name, str) or name == "":
            raise ValueError("cargo legal bundle package name is invalid")
        if not isinstance(version, str) or version == "":
            raise ValueError("cargo legal bundle package version is invalid")
        validate_sha256(checksum, "cargo legal bundle package checksum")
        packages.append(
            {
                "checksum": str(checksum),
                "name": name,
                "version": version,
            }
        )
    packages.sort(key=lambda item: (item["name"], item["version"]))
    if len(packages) != len(
        {(package["name"], package["version"]) for package in packages}
    ):
        raise ValueError("cargo legal bundle packages are not unique")
    return packages


def render_cargo_package_inventory(packages: list[dict[str, str]]) -> bytes:
    return (json.dumps(packages, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def legal_file_is_usable(payload: bytes) -> bool:
    stripped = payload.strip()
    if stripped == b"":
        return False
    if len(stripped) > 512:
        return True
    try:
        pointer = PurePosixPath(stripped.decode("utf-8"))
    except UnicodeDecodeError:
        return True
    if "\n" in str(pointer) or "\r" in str(pointer):
        return True
    if pointer.name.lower().startswith(
        ("copying", "copyright", "license", "licence", "notice", "unlicense")
    ):
        return False
    return True


def render_cargo_supplement(
    supplement: dict[str, Any],
    fetcher: Callable[[str], bytes] = fetch,
) -> bytes:
    lines = [
        b"Supplemental Cargo legal payload\n",
        f"Package: {supplement['name']} {supplement['version']}\n".encode("utf-8"),
        f"Crate SHA-256: {supplement['checksum']}\n".encode("utf-8"),
        (f"Repository commit: {supplement['repository_commit']}\n").encode("utf-8"),
        f"Evidence: {supplement['evidence']}\n".encode("utf-8"),
    ]
    for source in supplement["sources"]:
        url = str(source["url"])
        payload = fetcher(url)
        actual = sha256(payload)
        expected = str(source["sha256"])
        if actual != expected:
            raise ValueError(
                f"cargo legal supplement source checksum differs: {url}: "
                f"expected {expected}, found {actual}"
            )
        lines.extend(
            [
                f"\n--- {url} ---\n".encode("utf-8"),
                f"Source SHA-256: {actual}\n\n".encode("utf-8"),
                payload,
            ]
        )
        if not payload.endswith(b"\n"):
            lines.append(b"\n")
    rendered = b"".join(lines)
    actual_payload = sha256(rendered)
    expected_payload = str(supplement["payload_sha256"])
    if actual_payload != expected_payload:
        raise ValueError(
            "cargo legal supplement payload checksum differs: "
            f"{supplement['name']} {supplement['version']}: "
            f"expected {expected_payload}, found {actual_payload}"
        )
    return rendered


def archive_legal_files(
    payload: bytes,
) -> tuple[dict[str, Any], list[tuple[str, bytes]]]:
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        cargo_members = [
            member
            for member in members
            if PurePosixPath(member.name).name == "Cargo.toml"
        ]
        if len(cargo_members) != 1:
            raise ValueError("cargo archive has an unexpected Cargo.toml set")
        cargo_handle = archive.extractfile(cargo_members[0])
        if cargo_handle is None:
            raise ValueError("cargo archive Cargo.toml is unreadable")
        metadata_value = tomllib.loads(cargo_handle.read().decode("utf-8"))
        metadata = metadata_value.get("package")
        if not isinstance(metadata, dict):
            raise ValueError("cargo archive package metadata is invalid")
        vcs_members = [
            member
            for member in members
            if PurePosixPath(member.name).name == ".cargo_vcs_info.json"
        ]
        if len(vcs_members) > 1:
            raise ValueError("cargo archive has an unexpected VCS receipt set")
        if vcs_members:
            vcs_handle = archive.extractfile(vcs_members[0])
            if vcs_handle is None:
                raise ValueError("cargo archive VCS receipt is unreadable")
            vcs_value = json.loads(vcs_handle.read())
            git_value = vcs_value.get("git")
            if not isinstance(git_value, dict):
                raise ValueError("cargo archive VCS receipt is invalid")
            sha1_value = git_value.get("sha1")
            if not isinstance(sha1_value, str):
                raise ValueError("cargo archive VCS receipt has no commit")
            if re.fullmatch(r"[0-9a-f]{40}", sha1_value) is None:
                raise ValueError("cargo archive VCS receipt commit is invalid")
            metadata["_cargo_vcs_sha1"] = sha1_value

        legal: list[tuple[str, bytes]] = []
        root = PurePosixPath(cargo_members[0].name).parent
        for member in members:
            relative = PurePosixPath(member.name).relative_to(root)
            if LEGAL_NAME_PATTERN.fullmatch(relative.name) is None:
                continue
            if member.size > 1024 * 1024:
                raise ValueError(f"cargo archive legal file is too large: {relative}")
            handle = archive.extractfile(member)
            if handle is None:
                raise ValueError(f"cargo archive legal file is unreadable: {relative}")
            legal.append((str(relative), handle.read()))
        legal.sort(key=lambda item: item[0])
        return metadata, legal


def render_cargo_bundle(
    bundle: dict[str, Any],
    fetcher: Callable[[str], bytes] = fetch,
) -> tuple[bytes, str]:
    lock_url = str(bundle["lock_url"])
    lock_payload = fetcher(lock_url)
    lock_actual = sha256(lock_payload)
    if lock_actual != bundle["lock_sha256"]:
        raise ValueError(
            f"cargo legal bundle lock checksum differs: {lock_url}: "
            f"expected {bundle['lock_sha256']}, found {lock_actual}"
        )
    packages = cargo_packages(lock_payload)
    if len(packages) != bundle["package_count"]:
        raise ValueError("cargo legal bundle package count differs")
    inventory_payload = render_cargo_package_inventory(packages)
    inventory_sha = sha256(inventory_payload)
    if inventory_sha != bundle["packages_sha256"]:
        raise ValueError("cargo legal bundle package inventory differs")
    supplements = load_cargo_supplements()
    package_keys = {
        (package["name"], package["version"], package["checksum"])
        for package in packages
    }
    if not set(supplements).issubset(package_keys):
        raise ValueError("cargo legal supplement is absent from the package inventory")

    def fetch_package(package: dict[str, str]) -> tuple[dict[str, str], bytes]:
        url = str(bundle["url_template"]).format(
            name=package["name"],
            version=package["version"],
        )
        archive_payload = fetcher(url)
        actual = sha256(archive_payload)
        if actual != package["checksum"]:
            raise ValueError(
                f"cargo package checksum differs: {url}: "
                f"expected {package['checksum']}, found {actual}"
            )
        return package, archive_payload

    fetched: list[tuple[dict[str, str], bytes]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        for result in executor.map(fetch_package, packages):
            fetched.append(result)

    lines: list[bytes] = [
        b"Taplo Cargo.lock legal payload\n",
        f"Lock source: {lock_url}\n".encode("utf-8"),
        f"Lock SHA-256: {lock_actual}\n".encode("utf-8"),
        f"Package count: {len(packages)}\n".encode("utf-8"),
        f"Package inventory SHA-256: {inventory_sha}\n".encode("utf-8"),
        f"Package URL template: {bundle['url_template']}\n".encode("utf-8"),
    ]
    for package, archive_payload in fetched:
        metadata, legal_files = archive_legal_files(archive_payload)
        metadata_name = metadata.get("name")
        metadata_version = metadata.get("version")
        if metadata_name != package["name"]:
            raise ValueError("cargo archive package name differs")
        if metadata_version != package["version"]:
            raise ValueError("cargo archive package version differs")
        source_url = str(bundle["url_template"]).format(
            name=package["name"],
            version=package["version"],
        )
        lines.extend(
            [
                b"\n================================================================\n",
                f"Package: {package['name']} {package['version']}\n".encode("utf-8"),
                f"Source: {source_url}\n".encode("utf-8"),
                f"Source SHA-256: {package['checksum']}\n".encode("utf-8"),
                f"Authors: {json.dumps(metadata.get('authors', []))}\n".encode("utf-8"),
                f"License expression: {metadata.get('license', '')}\n".encode("utf-8"),
                f"License file: {metadata.get('license-file', '')}\n".encode("utf-8"),
                f"Repository: {metadata.get('repository', '')}\n".encode("utf-8"),
            ]
        )
        usable_legal_files = [
            (relative, legal_payload)
            for relative, legal_payload in legal_files
            if legal_file_is_usable(legal_payload)
        ]
        supplement_key = (
            package["name"],
            package["version"],
            package["checksum"],
        )
        supplement = supplements.get(supplement_key)
        if not usable_legal_files and supplement is None:
            raise ValueError(
                "cargo archive has no usable legal file and no exact supplement: "
                f"{package['name']} {package['version']} {package['checksum']}"
            )
        if usable_legal_files and supplement is not None:
            raise ValueError(
                "cargo legal supplement is not required: "
                f"{package['name']} {package['version']}"
            )
        if supplement is not None and "_cargo_vcs_sha1" in metadata:
            if metadata["_cargo_vcs_sha1"] != supplement["repository_commit"]:
                raise ValueError(
                    "cargo legal supplement repository commit differs: "
                    f"{package['name']} {package['version']}"
                )
        for relative, legal_payload in usable_legal_files:
            lines.extend(
                [
                    f"\n--- {relative} ---\n".encode("utf-8"),
                    legal_payload,
                ]
            )
            if not legal_payload.endswith(b"\n"):
                lines.append(b"\n")
        if supplement is not None:
            lines.extend(
                [
                    b"\n--- supplemental-legal-payload ---\n",
                    render_cargo_supplement(supplement, fetcher),
                ]
            )
    return b"".join(lines), lock_actual


def cargo_manifest_entry(
    bundle: dict[str, Any],
    payload: bytes,
) -> dict[str, str]:
    actual = sha256(payload)
    expected = str(bundle["payload_sha256"])
    if actual != expected:
        raise ValueError(
            f"cargo legal bundle payload checksum differs: "
            f"expected {expected}, found {actual}"
        )
    header = b"".join(
        [
            b"Taplo Cargo.lock legal payload\n",
            f"Lock source: {bundle['lock_url']}\n".encode("utf-8"),
            f"Lock SHA-256: {bundle['lock_sha256']}\n".encode("utf-8"),
            f"Package count: {bundle['package_count']}\n".encode("utf-8"),
            (f"Package inventory SHA-256: " f"{bundle['packages_sha256']}\n").encode(
                "utf-8"
            ),
            f"Package URL template: {bundle['url_template']}\n".encode("utf-8"),
        ]
    )
    if not payload.startswith(header):
        raise ValueError("cargo legal bundle metadata differs")
    package_marker_count = payload.count(
        b"\n================================================================\nPackage: "
    )
    if package_marker_count != bundle["package_count"]:
        raise ValueError("cargo legal bundle payload package count differs")
    supplements = load_cargo_supplements()
    supplement_marker = b"\n--- supplemental-legal-payload ---\n"
    if payload.count(supplement_marker) != len(supplements):
        raise ValueError("cargo legal bundle supplement count differs")
    package_separator = (
        b"\n================================================================\nPackage: "
    )
    for supplement in supplements.values():
        header = b"".join(
            [
                b"Supplemental Cargo legal payload\n",
                (f"Package: {supplement['name']} " f"{supplement['version']}\n").encode(
                    "utf-8"
                ),
                f"Crate SHA-256: {supplement['checksum']}\n".encode("utf-8"),
                (f"Repository commit: {supplement['repository_commit']}\n").encode(
                    "utf-8"
                ),
                f"Evidence: {supplement['evidence']}\n".encode("utf-8"),
            ]
        )
        start_marker = supplement_marker + header
        if payload.count(start_marker) != 1:
            raise ValueError(
                "cargo legal bundle supplement metadata differs: "
                f"{supplement['name']} {supplement['version']}"
            )
        start = payload.index(start_marker) + len(supplement_marker)
        end = payload.find(package_separator, start)
        if end == -1:
            end = len(payload)
        supplement_payload = payload[start:end]
        for source in supplement["sources"]:
            source_metadata = (
                f"\n--- {source['url']} ---\n" f"Source SHA-256: {source['sha256']}\n\n"
            ).encode("utf-8")
            if supplement_payload.count(source_metadata) != 1:
                raise ValueError(
                    "cargo legal bundle supplement source differs: "
                    f"{supplement['name']} {supplement['version']}"
                )
        if sha256(supplement_payload) != supplement["payload_sha256"]:
            raise ValueError(
                "cargo legal bundle supplement payload differs: "
                f"{supplement['name']} {supplement['version']}"
            )
    return {
        "component": str(bundle["component"]),
        "license": str(bundle["license"]),
        "path": str(bundle["output"]),
        "payload_sha256": expected,
        "source_sha256": str(bundle["lock_sha256"]),
        "source_url": str(bundle["lock_url"]),
        "version": str(bundle["version"]),
    }


def render_manifest(manifest: dict[str, Any]) -> bytes:
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_payloads(fetcher: Callable[[str], bytes] = fetch) -> None:
    fetched: list[tuple[dict[str, Any], bytes]] = []
    for source in load_sources():
        payload = fetcher(str(source["url"]))
        manifest_entry(source, payload)
        fetched.append((source, payload))
    archive_payloads: list[tuple[dict[str, Any], bytes]] = []
    for bundle in load_archive_bundles():
        payload = render_archive_bundle(bundle, fetcher)
        archive_manifest_entry(bundle, payload)
        archive_payloads.append((bundle, payload))
    cargo_payloads: list[tuple[dict[str, Any], bytes]] = []
    for bundle in load_cargo_bundles():
        payload, _lock_actual = render_cargo_bundle(bundle, fetcher)
        cargo_manifest_entry(bundle, payload)
        cargo_payloads.append((bundle, payload))

    targets: dict[str, list[dict[str, str]]] = {}
    with tempfile.TemporaryDirectory(
        prefix="lint-legal-payloads-",
        dir=OUTPUT_ROOT.parent,
    ) as temporary_value:
        temporary = Path(temporary_value)
        for source, payload in fetched:
            entry = manifest_entry(source, payload)
            for target_value in source["targets"]:
                target = str(target_value)
                target_directory = temporary / target
                target_directory.mkdir(parents=True, exist_ok=True)
                output = target_directory / entry["path"]
                output.write_bytes(payload)
                targets.setdefault(target, []).append(dict(entry))
        for bundle, payload in archive_payloads:
            entry = archive_manifest_entry(bundle, payload)
            for target_value in bundle["targets"]:
                target = str(target_value)
                target_directory = temporary / target
                target_directory.mkdir(parents=True, exist_ok=True)
                output = target_directory / entry["path"]
                output.write_bytes(payload)
                targets.setdefault(target, []).append(dict(entry))
        for bundle, payload in cargo_payloads:
            entry = cargo_manifest_entry(bundle, payload)
            target = str(bundle["target"])
            target_directory = temporary / target
            target_directory.mkdir(parents=True, exist_ok=True)
            output = target_directory / entry["path"]
            output.write_bytes(payload)
            targets.setdefault(target, []).append(dict(entry))
        for target, entries in sorted(targets.items()):
            entries.sort(key=lambda item: (item["component"], item["path"]))
            manifest = {
                "entries": entries,
                "schema_version": SCHEMA_VERSION,
                "target": target,
            }
            path = temporary / target / "manifest.json"
            path.write_bytes(render_manifest(manifest))
        if OUTPUT_ROOT.exists():
            shutil.rmtree(OUTPUT_ROOT)
        temporary.rename(OUTPUT_ROOT)


def check_payloads() -> None:
    sources = load_sources()
    expected: dict[tuple[str, str], dict[str, Any]] = {}
    expected_kind: dict[tuple[str, str], str] = {}
    for source in sources:
        for target_value in source["targets"]:
            key = (str(target_value), str(source["output"]))
            expected[key] = source
            expected_kind[key] = "source"
    for bundle in load_archive_bundles():
        for target_value in bundle["targets"]:
            key = (str(target_value), str(bundle["output"]))
            if key in expected:
                raise ValueError(f"duplicate legal payload path: {key}")
            expected[key] = bundle
            expected_kind[key] = "archive"
    for bundle in load_cargo_bundles():
        key = (str(bundle["target"]), str(bundle["output"]))
        if key in expected:
            raise ValueError(f"duplicate legal payload path: {key}")
        expected[key] = bundle
        expected_kind[key] = "cargo"

    if not OUTPUT_ROOT.is_dir():
        raise ValueError("generated legal payload root is missing")
    root_entries = list(OUTPUT_ROOT.iterdir())
    if not all(path.is_dir() and not path.is_symlink() for path in root_entries):
        raise ValueError("generated legal payload root has unexpected files")
    actual_targets = {path.name for path in root_entries}
    if actual_targets != set(TARGETS):
        raise ValueError("generated legal payload target set differs")

    found: set[tuple[str, str]] = set()
    for target in sorted(actual_targets):
        manifest_path = OUTPUT_ROOT / target / "manifest.json"
        manifest_payload = manifest_path.read_bytes()
        manifest = json.loads(manifest_payload)
        if not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS:
            raise ValueError(f"legal manifest has unexpected fields: {target}")
        if render_manifest(manifest) != manifest_payload:
            raise ValueError(f"legal manifest is not canonical: {target}")
        if manifest.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"legal manifest schema differs: {target}")
        if manifest.get("target") != target:
            raise ValueError(f"legal manifest target differs: {target}")

        entries = manifest.get("entries")
        if not isinstance(entries, list):
            raise ValueError(f"legal manifest entries differ: {target}")
        entry_order: list[tuple[str, str]] = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != MANIFEST_ENTRY_KEYS:
                raise ValueError(f"legal manifest entry is invalid: {target}")
            relative = str(entry["path"])
            key = (target, relative)
            source = expected.get(key)
            if source is None:
                raise ValueError(f"unexpected legal payload: {key}")
            payload_path = OUTPUT_ROOT / target / relative
            if not payload_path.is_file() or payload_path.is_symlink():
                raise ValueError(f"legal payload is not a regular file: {key}")
            payload = payload_path.read_bytes()
            expected_entry: dict[str, str]
            if expected_kind[key] == "archive":
                expected_entry = archive_manifest_entry(source, payload)
            elif expected_kind[key] == "cargo":
                expected_entry = cargo_manifest_entry(source, payload)
            else:
                expected_entry = manifest_entry(source, payload)
            if entry != expected_entry:
                raise ValueError(f"legal payload manifest differs: {key}")
            found.add(key)
            entry_order.append((str(entry["component"]), relative))
        if entry_order != sorted(set(entry_order)):
            raise ValueError(f"legal manifest entries are not canonical: {target}")

        expected_files = {"manifest.json"}
        expected_files.update(
            relative for found_target, relative in expected if found_target == target
        )
        actual_files = {
            path.name
            for path in (OUTPUT_ROOT / target).iterdir()
            if path.is_file() and not path.is_symlink()
        }
        if actual_files != expected_files:
            raise ValueError(f"legal payload files differ: {target}")
        if len(list((OUTPUT_ROOT / target).iterdir())) != len(actual_files):
            raise ValueError(f"legal payload paths differ: {target}")

    if found != set(expected):
        raise ValueError("generated legal payload set differs")

    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    for target in sorted(TARGETS):
        pattern = rf"^FROM [^\n]+ AS {re.escape(target)}\n(?P<body>.*?)(?=^FROM |\Z)"
        matched = re.search(
            pattern,
            dockerfile,
            flags=re.MULTILINE | re.DOTALL,
        )
        if matched is None:
            raise ValueError(f"Dockerfile target is missing: {target}")
        copy = f"COPY images/licenses/{target} /licenses/{target}"
        if matched.group("body").count(copy) != 1:
            raise ValueError(f"Dockerfile legal payload copy differs: {target}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    if arguments.write:
        write_payloads()
    check_payloads()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
