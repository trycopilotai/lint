#!/usr/bin/env python3
"""Mechanize the repetitive parts of cutting a lint release.

The previous release moved lint's own version string across 17
files by hand. A first pass missed six of them and only the test
suite noticed, so this program owns the rewrite,
the packaged-copy sync, and the completeness check, and prints
the release steps that stay human.

Nothing here states a version literal on purpose. A file that
records one is a file the next bump rewrites, so a version
written into this program's own prose would silently become a
claim about a release that never happened.

Ordering matters and is not mechanical:

    1. `bump` and any other source change, committed together.
    2. `refresh-evidence`, committed on its own.

`scripts/verify_demo.py` requires the demo evidence commit to be
the repository tip and to touch evidence paths only. Refreshing
evidence while source edits are still uncommitted fails with
"demo evidence commit contains unrelated paths".
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]

# `images/matrix.json` is what the release workflow compares a
# pushed tag against, so it is the single place the current
# version is read from. Asking the caller for the old version
# would let a typo rewrite nothing and still report success.
VERSION_DOCUMENT = "images/matrix.json"

# Directories a version rewrite must never enter.
#
# `images/licenses/` and `images/license_sources.json` are
# third-party legal provenance. The `anes` Rust crate records a
# version of its own that has already collided exactly with a
# lint version being replaced, and writing lint's number over
# it would record a release upstream never made.
#
# `evidence/` and `images/inventories/` are generated receipts.
# They are refreshed by their own generators against a built
# artifact, never by editing the recorded value.
EXCLUDED_DIRECTORIES = (
    "evidence/",
    "images/inventories/",
    "images/licenses/",
)
EXCLUDED_FILES = ("images/license_sources.json",)

LICENSE_SOURCES = "images/license_sources.json"

# Copies the skill ships so it runs standalone. `action_test.py`
# requires each to be byte-identical to its source, so a bump
# that edits only the source leaves the packaged copy behind.
PACKAGED_COPIES = (
    ("lint.py", "skills/lint/lint.py"),
    ("languages.json", "skills/lint/languages.json"),
    ("images/matrix.json", "skills/lint/images/matrix.json"),
)

CHECKLIST_DOCUMENT = "docs/releasing.md"

RELEASE_VERSION = re.compile(r"\A(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*)){2}\Z")


class ReleaseError(Exception):
    """A release step refused to proceed."""


def git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.decode("utf-8")


def tracked_files(root: Path) -> list[str]:
    """Return every tracked path, the set `git grep` would read."""

    listing = git(root, "ls-files", "-z")
    return sorted(path for path in listing.split("\0") if path != "")


def is_excluded(path: str) -> bool:
    if path in EXCLUDED_FILES:
        return True
    for directory in EXCLUDED_DIRECTORIES:
        if path.startswith(directory):
            return True
    return False


def rewritable_files(root: Path) -> list[str]:
    return [path for path in tracked_files(root) if not is_excluded(path)]


def version_pattern(version: str) -> str:
    """Match this version, never a longer number that contains it.

    The lookarounds keep a version out of any longer dotted
    number that contains it, while still matching every real
    site: a `v` prefix, an image tag, a manifest filename, and
    the hyphenated staging tag.
    """

    return r"(?<![0-9.])" + re.escape(version) + r"(?![0-9]|\.[0-9])"


def version_expression(version: str) -> re.Pattern[str]:
    return re.compile(version_pattern(version))


def version_expression_bytes(version: str) -> re.Pattern[bytes]:
    return re.compile(version_pattern(version).encode("ascii"))


def read_version(root: Path) -> str:
    document = json.loads((root / VERSION_DOCUMENT).read_text(encoding="utf-8"))
    version = document.get("version")
    if not isinstance(version, str):
        raise ReleaseError(f"{VERSION_DOCUMENT} has no version string")
    return version


def is_editable(root: Path, path: str) -> bool:
    """Report whether a rewrite may open this path at all.

    `skill` is a tracked symbolic link to `skills/lint`, whose
    contents are tracked under their own paths. Following it
    would read a directory and rewrite the same bytes twice.
    """

    target = root / path
    if target.is_symlink():
        return False
    return target.is_file()


def read_text(root: Path, path: str) -> str | None:
    """Return the file's text, or None when it is not UTF-8 text."""

    payload = (root / path).read_bytes()
    if b"\0" in payload:
        return None
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        return None


def rewrite_version(root: Path, old: str, new: str) -> dict[str, int]:
    """Replace `old` with `new` everywhere lint owns the string."""

    expression = version_expression(old)
    counts: dict[str, int] = {}
    for path in rewritable_files(root):
        if not is_editable(root, path):
            continue
        text = read_text(root, path)
        if text is None:
            continue
        replaced, count = expression.subn(new, text)
        if count == 0:
            continue
        # Bytes, not text. `write_text` translates every newline
        # to the host separator, so a rewrite run on Windows
        # would rewrite the line endings of all 17 files as a
        # side effect of moving one number.
        (root / path).write_bytes(replaced.encode("utf-8"))
        counts[path] = count
    return counts


def sync_packaged_copies(root: Path) -> list[str]:
    """Copy each source onto its packaged copy, byte for byte."""

    synced: list[str] = []
    for source, packaged in PACKAGED_COPIES:
        payload = (root / source).read_bytes()
        target = root / packaged
        if target.read_bytes() == payload:
            continue
        target.write_bytes(payload)
        synced.append(packaged)
    return synced


def verify_packaged_copies(root: Path) -> None:
    for source, packaged in PACKAGED_COPIES:
        if (root / source).read_bytes() != (root / packaged).read_bytes():
            raise ReleaseError(f"packaged copy differs from {source}: {packaged}")


def third_party_versions(root: Path) -> dict[str, str]:
    """Fingerprint every version recorded in the legal sources.

    Keyed by position in the document so a renamed or reordered
    entry is a difference too, not a silent match.
    """

    document = json.loads((root / LICENSE_SOURCES).read_text(encoding="utf-8"))
    recorded: dict[str, str] = {}

    def walk(value: Any, location: str) -> None:
        if isinstance(value, dict):
            version = value.get("version")
            if isinstance(version, str):
                name = value.get("name")
                if not isinstance(name, str):
                    name = value.get("component")
                recorded[f"{location} {name}"] = version
            for key in sorted(value):
                walk(value[key], f"{location}/{key}")
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{location}[{index}]")

    walk(document, "")
    return recorded


def verify_third_party_versions(root: Path, before: dict[str, str]) -> None:
    """Fail loudly if a bump reached the legal provenance record."""

    after = third_party_versions(root)
    if after == before:
        return
    differences: list[str] = []
    for location in sorted(set(before) | set(after)):
        recorded = before.get(location, "<absent>")
        current = after.get(location, "<absent>")
        if recorded != current:
            differences.append(f"{location}: {recorded} -> {current}")
    joined = ", ".join(differences)
    raise ReleaseError(f"third-party version records changed: {joined}")


def remaining_sites(root: Path, version: str) -> list[str]:
    """Return every surviving mention of a version, with line numbers.

    This is the in-process form of:

        git grep -n "<version>" -- . ':!images/licenses' \\
            ':!images/license_sources.json' ':!evidence'

    It deliberately reads more than the rewrite does. A file the
    rewrite skipped because it holds no decodable text is still
    searched, as bytes, so a version the rewrite could not edit
    fails the release instead of shipping stale.
    """

    expression = version_expression(version)
    payload_expression = version_expression_bytes(version)
    found: list[str] = []
    for path in rewritable_files(root):
        if not is_editable(root, path):
            continue
        text = read_text(root, path)
        if text is None:
            if payload_expression.search((root / path).read_bytes()) is not None:
                found.append(f"{path}: matched in a file the rewrite cannot edit")
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if expression.search(line) is None:
                continue
            found.append(f"{path}:{number}: {line.strip()}")
    return found


def bump(root: Path, new: str) -> dict[str, Any]:
    """Move lint's own version everywhere, then prove it moved."""

    if RELEASE_VERSION.fullmatch(new) is None:
        raise ReleaseError(f"release version must be MAJOR.MINOR.PATCH: {new}")
    old = read_version(root)
    if old == new:
        raise ReleaseError(f"repository is already at {new}")

    recorded = third_party_versions(root)
    counts = rewrite_version(root, old, new)
    synced = sync_packaged_copies(root)

    verify_third_party_versions(root, recorded)
    verify_packaged_copies(root)

    if read_version(root) != new:
        raise ReleaseError(f"{VERSION_DOCUMENT} still reports {read_version(root)}")
    stale = remaining_sites(root, old)
    if stale:
        joined = "\n".join(stale)
        raise ReleaseError(f"version {old} survives the bump:\n{joined}")

    return {
        "files": len(counts),
        "new_version": new,
        "old_version": old,
        "packaged_copies_synced": synced,
        "sites": sum(counts.values()),
        "status": "ok",
    }


def load_demo_verifier(root: Path):
    path = root / "scripts" / "verify_demo.py"
    specification = importlib.util.spec_from_file_location(
        "release_demo_verifier",
        path,
    )
    if specification is None:
        raise ReleaseError("could not create demo verifier specification")
    if specification.loader is None:
        raise ReleaseError("demo verifier specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def pending_paths(root: Path) -> set[str]:
    """Return every path that differs from HEAD, tracked or not."""

    pending: set[str] = set()
    listings = (
        git(root, "diff", "--name-only", "-z", "HEAD"),
        git(root, "ls-files", "-z", "--others", "--exclude-standard"),
    )
    for listing in listings:
        for path in listing.split("\0"):
            if path != "":
                pending.add(path)
    return pending


def require_committed_source(root: Path, allowed: Iterable[str]) -> None:
    """Refuse to refresh evidence over pending source edits.

    `scripts/verify_demo.py` requires the evidence commit to be
    the repository tip and to touch evidence paths only. Writing
    the manifest first and discovering that afterwards leaves a
    half-refreshed file behind, so the ordering is checked
    before anything is written.
    """

    unrelated = sorted(pending_paths(root) - set(allowed))
    if not unrelated:
        return
    joined = ", ".join(unrelated)
    raise ReleaseError(
        "commit the source changes before refreshing evidence; "
        f"still pending: {joined}"
    )


def refresh_evidence(root: Path, verify: bool) -> dict[str, Any]:
    """Repoint the demo evidence manifest at HEAD.

    Run this only once every source change is committed. The
    manifest records the commit the demo was captured from, and
    `scripts/verify_demo.py` rejects an evidence commit that
    carries anything but evidence paths.
    """

    verifier = load_demo_verifier(root)
    require_committed_source(root, verifier.ALLOWED_EVIDENCE_DELTA)
    previous = verifier.load_manifest()
    head = verifier.git_text("rev-parse", "HEAD")
    manifest = verifier.build_manifest(
        input_commit=head,
        agent=previous["agent"],
        agent_version=previous["agent_version"],
        invocation_date=previous["invocation_date"],
    )
    verifier.MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    changed = sorted(
        path
        for path, digest in manifest["source_sha256"].items()
        if previous.get("source_sha256", {}).get(path) != digest
    )
    if verify:
        verifier.verify_manifest(verifier.load_manifest())
    return {
        "changed_sources": changed,
        "input_commit": head,
        "input_tree": manifest["input_tree"],
        "previous_input_commit": previous["input_commit"],
        "status": "ok",
        "verified": verify,
    }


def checklist(root: Path) -> str:
    return (root / CHECKLIST_DOCUMENT).read_text(encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(
        description="Cut a lint release.",
        epilog=(
            "Commit source changes first, refresh the demo "
            "evidence second, and commit the evidence alone. "
            "Reversing that order fails demo verification with "
            "'demo evidence commit contains unrelated paths'."
        ),
    )
    commands = argument_parser.add_subparsers(dest="command", required=True)

    bump_command = commands.add_parser(
        "bump",
        help="rewrite lint's own version everywhere and verify completeness",
    )
    bump_command.add_argument("--version", required=True)

    evidence_command = commands.add_parser(
        "refresh-evidence",
        help="repoint the demo evidence manifest at HEAD (commit this alone)",
    )
    evidence_command.add_argument(
        "--no-verify",
        action="store_true",
        help="write the manifest without replaying the demo",
    )

    commands.add_parser(
        "checklist",
        help="print the release steps that are not mechanical",
    )
    return argument_parser


def report(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def main(argv: Iterable[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.command == "bump":
            report(bump(ROOT, arguments.version))
            return 0
        if arguments.command == "refresh-evidence":
            report(refresh_evidence(ROOT, not arguments.no_verify))
            return 0
        sys.stdout.write(checklist(ROOT))
        return 0
    except ReleaseError as error:
        print(f"release: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
