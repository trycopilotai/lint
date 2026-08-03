#!/usr/bin/env python3
"""Verify repository invariants that unit tests do not cover."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# The allowed-signers file the release job trusts is read from
# this commit, which is published on `main` and therefore
# fetchable by the release runner. Pinning an unpublished
# commit makes the trust-root checkout fail before a signature
# is ever checked.
RELEASE_TRUST_ROOT = "3d5d4ee7b83b2c6442039b8a72a571c729ffcead"

# Tag pushes must reach exactly one workflow. CI and Images
# also run for a tag push when they declare one, which
# duplicates every job of the branch push that carries the same
# commit.
CONTINUOUS_TRIGGERS = frozenset(
    {"pull_request", "push", "schedule", "workflow_dispatch"}
)
RELEASE_TRIGGERS = frozenset({"push", "workflow_dispatch"})

# Wording that described the repository before it was public.
# Each phrase is false to a reader of the public repository.
RETIRED_VISIBILITY_PHRASES = (
    "while the repository is private",
    "after public launch",
    "before the repository becomes public",
    "when the repository is public",
    "when it is public",
    "public actions capacity",
    "prelaunch",
    "private repository",
    "staging repository",
    "private registry",
)

ARCHITECTURE_LABELS = {"amd64": "AMD64", "arm64": "ARM64"}

IMAGE_VERIFIER_CALL = re.compile(
    r"images/verify_images\.py(?P<arguments>.*?)(?=python3 |- name:|$)"
)
ARCHITECTURE_ARGUMENT = re.compile(r'--architecture "?(?P<value>[a-z0-9]+)"?')


def load_image_verifier():
    path = ROOT / "images" / "verify_images.py"
    specification = importlib.util.spec_from_file_location(
        "image_verifier",
        path,
    )
    if specification is None:
        raise RuntimeError("could not create image verifier specification")
    if specification.loader is None:
        raise RuntimeError("image verifier specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def load_demo_evidence_verifier():
    path = ROOT / "scripts" / "verify_demo.py"
    specification = importlib.util.spec_from_file_location(
        "demo_evidence_verifier",
        path,
    )
    if specification is None:
        raise RuntimeError("could not create demo verifier specification")
    if specification.loader is None:
        raise RuntimeError("demo verifier specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def load_legal_payload_generator():
    path = ROOT / "tools" / "generate_legal_payloads.py"
    specification = importlib.util.spec_from_file_location(
        "legal_payload_generator",
        path,
    )
    if specification is None:
        raise RuntimeError("could not create legal generator specification")
    if specification.loader is None:
        raise RuntimeError("legal generator specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def load_dependency_closure_verifier():
    path = ROOT / "tools" / "verify_dependency_closures.py"
    specification = importlib.util.spec_from_file_location(
        "dependency_closure_verifier",
        path,
    )
    if specification is None:
        raise RuntimeError("could not create closure verifier specification")
    if specification.loader is None:
        raise RuntimeError("closure verifier specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def walked_files() -> list[Path]:
    """Return every file in the tree, ignoring Git entirely."""

    pruned = {".git", "__pycache__", ".venv", "node_modules"}
    files: list[Path] = []
    for directory, directory_names, file_names in os.walk(ROOT):
        directory_names[:] = [name for name in directory_names if name not in pruned]
        for name in file_names:
            path = Path(directory) / name
            if path.is_file() and not path.is_symlink():
                files.append(path)
    files.sort()
    return files


def walked_paths() -> list[Path]:
    """Return every path name without following links."""

    pruned = {".git", "__pycache__", ".venv", "node_modules"}
    paths: list[Path] = []
    for directory, directory_names, file_names in os.walk(
        ROOT,
        followlinks=False,
    ):
        retained_directories: list[str] = []
        for name in directory_names:
            if name in pruned:
                continue
            path = Path(directory) / name
            paths.append(path)
            if not path.is_symlink():
                retained_directories.append(name)
        directory_names[:] = retained_directories
        for name in file_names:
            paths.append(Path(directory) / name)
    paths.sort()
    return paths


def git_named_paths() -> list[Path] | None:
    """Return tracked names only when ROOT is the Git root."""

    root_result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if root_result.returncode != 0:
        return None
    repository_root = Path(root_result.stdout.strip()).resolve()
    if repository_root != ROOT.resolve():
        return None
    completed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    )
    paths: list[Path] = []
    for value in completed.stdout.split(b"\0"):
        if value == b"":
            continue
        paths.append(ROOT / value.decode("utf-8"))
    return paths


def tracked_files() -> list[Path]:
    """Return the files to verify, with or without Git.

    The published source archive contains no `.git`, so a
    recipient who runs the documented `make verify` on what
    they downloaded used to get a `git ls-files` traceback.
    Falling back to a walk makes the archive verifiable on its
    own terms.
    """

    paths = git_named_paths()
    if paths is None:
        return walked_files()
    files: list[Path] = []
    for path in paths:
        if path.is_file() and not path.is_symlink():
            files.append(path)
    return files


def named_paths() -> list[Path]:
    """Return every tracked path, symlinks and all.

    `tracked_files()` filters through is_file() so callers get
    readable regular files. Name checks need the unfiltered
    list, or a symlink can carry a name no regular file would
    be allowed to have.
    """

    paths = git_named_paths()
    if paths is None:
        return walked_paths()
    return paths


def verify_python_style(files: list[Path]) -> None:
    for path in files:
        if path.suffix != ".py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.IfExp):
                raise ValueError(f"ternary expression: {path}:{node.lineno}")


def verify_normal_names(files: list[Path]) -> None:
    reserved = "." + "g" + "p" + "t"
    reserved_bytes = reserved.encode("ascii")

    for path in sorted(set(files) | set(named_paths())):
        relative = path.relative_to(ROOT).as_posix()
        if reserved in relative:
            raise ValueError(f"reserved filename infix: {relative}")
        if path.is_symlink() or not path.is_file():
            continue
        if reserved_bytes in path.read_bytes():
            raise ValueError(f"reserved reference infix: {relative}")


def verify_manifests() -> None:
    versions = json.loads((ROOT / "languages.json").read_text(encoding="utf-8"))[
        "tools"
    ]
    expected = {
        "prettier": "3.7.4",
        "black": "24.10.0",
        "clang-format": "18.1.8",
        "google-java-format": "1.35.0",
        "buildifier": "8.2.1",
        "ktlint": "1.3.0",
        "shfmt": "3.13.1",
        "go": "1.26.5",
        "rust": "1.97.1",
        "rustfmt": "1.9.0",
        "taplo": "0.10.0",
        "libxml2": "2.15.3",
        "swift-format": "603.0.0",
        "csharpier": "1.3.0",
        "julia": "1.12.6",
        "juliaformatter": "2.12.3",
        "node": "24.18.0",
        "python": "3.13.14",
        "requirements": "sha256:723bfe9070dd70e804fff734a7156b963e694446e161739d03394ca30d9f29b5",
    }
    if versions != expected:
        raise ValueError("tool version manifest differs from the release set")

    skill = ROOT / "skill"
    if not skill.is_symlink():
        raise ValueError("skill must be a symbolic link")
    if skill.resolve() != (ROOT / "skills" / "lint").resolve():
        raise ValueError("skill symbolic link has the wrong target")

    for manifest in (
        ROOT / ".claude-plugin" / "plugin.json",
        ROOT / ".codex-plugin" / "plugin.json",
    ):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data["version"] != "0.1.6":
            raise ValueError(f"wrong plugin version: {manifest}")


def verify_demo() -> None:
    verifier = load_demo_evidence_verifier()
    verifier.verify_manifest(verifier.load_manifest())

    preview = ROOT / "assets" / "social-preview.png"
    payload = preview.read_bytes()
    if payload[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("social preview must be a PNG")
    width, height = struct.unpack(">II", payload[16:24])
    if (width, height) != (1280, 640):
        raise ValueError("social preview must be 1280 by 640")


def verify_actions(files: list[Path]) -> None:
    workflows = [
        path for path in files if path.parent == ROOT / ".github" / "workflows"
    ]
    if not workflows:
        raise ValueError("GitHub Actions workflows are missing")
    action_reference = re.compile(r"uses:\s+[^@\s]+@([^\s#]+)")
    full_sha = re.compile(r"[0-9a-f]{40}")
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        for match in action_reference.finditer(text):
            reference = match.group(1)
            if full_sha.fullmatch(reference) is None:
                raise ValueError(
                    f"action is not pinned to a commit: {path}: {reference}"
                )
        if re.search(r"^\s*pull_request_target\s*:", text, re.MULTILINE):
            raise ValueError(f"pull_request_target is not allowed: {path}")
        if "github.event.repository.private" in text:
            raise ValueError(f"workflow is incorrectly gated by visibility: {path}")

    images_workflow = (ROOT / ".github" / "workflows" / "images.yml").read_text(
        encoding="utf-8"
    )
    arm64_job = images_workflow.split("  build-arm64:", 1)
    if len(arm64_job) != 2:
        raise ValueError("ARM64 image job is missing")
    arm64_condition = arm64_job[1].split("    needs:", 1)[0]
    if "github.event.repository.visibility == 'public'" not in arm64_condition:
        raise ValueError("ARM64 image job must wait for public visibility")


def command_text(text: str) -> str:
    """Return one whitespace-normalized line of commands only.

    Every scan below reads shell invocations, so a commented
    line has to be dropped first. A comment naming a command
    the workflow deliberately does not run would otherwise
    register as running it.
    """

    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    return " ".join(" ".join(lines).split())


def workflow_triggers(text: str) -> dict[str, list[str]]:
    """Return each declared trigger with its own body lines."""

    lines = text.splitlines()
    if "on:" not in lines:
        raise ValueError("workflow has no top-level trigger block")
    triggers: dict[str, list[str]] = {}
    name = None
    for line in lines[lines.index("on:") + 1 :]:
        if line.strip() == "":
            continue
        if not line.startswith(" "):
            break
        if line.startswith("  ") and not line.startswith("   "):
            name = line.strip().rstrip(":")
            triggers[name] = []
            continue
        if name is None:
            raise ValueError("workflow trigger body precedes its trigger")
        triggers[name].append(line.strip())
    return triggers


def verify_workflow_triggers(workflows: dict[str, str]) -> None:
    """Require a version tag push to start the release alone."""

    for name in ("ci.yml", "images.yml"):
        triggers = workflow_triggers(workflows[name])
        if set(triggers) != set(CONTINUOUS_TRIGGERS):
            raise ValueError(f"{name} declares the wrong triggers: {sorted(triggers)}")
        if triggers["push"] != ["branches: [main]"]:
            raise ValueError(
                f"{name} push trigger is not main-only: {triggers['push']}"
            )
        if triggers["pull_request"] != []:
            raise ValueError(f"{name} narrows its pull request trigger")
        if triggers["workflow_dispatch"] != []:
            raise ValueError(f"{name} narrows its manual trigger")
        schedule = triggers["schedule"]
        if len(schedule) != 1 or not schedule[0].startswith("- cron:"):
            raise ValueError(f"{name} has no single scheduled run: {schedule}")
    triggers = workflow_triggers(workflows["release.yml"])
    if set(triggers) != set(RELEASE_TRIGGERS):
        raise ValueError(f"release.yml declares the wrong triggers: {sorted(triggers)}")
    if triggers["push"] != ['tags: ["v*"]']:
        raise ValueError(
            f"release.yml push trigger is not tag-only: {triggers['push']}"
        )


def workflow_inventory_architectures(text: str) -> list[str]:
    """Return every architecture the workflow content-checks."""

    architectures: list[str] = []
    for call in IMAGE_VERIFIER_CALL.finditer(command_text(text)):
        for match in ARCHITECTURE_ARGUMENT.finditer(call.group("arguments")):
            architectures.append(match.group("value"))
    return architectures


def verify_inventory_architecture_gate(
    workflows: dict[str, str],
    canonical: frozenset[str],
) -> None:
    """Bind image content checks to the inventories that exist.

    `images/verify_images.py --architecture X` loads the
    canonical inventory for X and fails when the file is
    absent. In the release job it fails after the images have
    already been pushed, so the gate has to be derived from the
    checked-in inventory set rather than from intent.
    """

    expected = sorted(canonical)
    for name in ("images.yml", "release.yml"):
        found = sorted(workflow_inventory_architectures(workflows[name]))
        if found != expected:
            raise ValueError(
                f"{name} content-checks {found}; canonical inventories cover {expected}"
            )


def verify_architecture_coverage(workflows: dict[str, str]) -> None:
    """Require ARM64 to keep every check an inventory does not gate."""

    images = workflows["images.yml"].split("  build-arm64:", 1)
    if len(images) != 2:
        raise ValueError("ARM64 image job is missing")
    release = workflows["release.yml"].split("- name: Verify ARM64 image", 1)
    if len(release) != 2:
        raise ValueError("ARM64 release verification is missing")
    surfaces = {
        "images.yml build-arm64": (
            command_text(images[1]),
            (
                "platforms: linux/arm64",
                "python3 images/verify_tool_version.py",
                "python3 images/smoke.py",
                'python3 images/verify_cli.py --target "$TARGET"',
                '--architecture "arm64"',
                "python3 images/verify_registry_size.py",
                "--platform linux/arm64",
                "format: cyclonedx",
                "severity: HIGH,CRITICAL",
                "name: Upload SBOM ARM64",
            ),
        ),
        "release.yml Verify ARM64 image": (
            command_text(release[1].split("\n  promote:", 1)[0]),
            (
                "docker pull --platform linux/arm64",
                "python3 images/verify_tool_version.py",
                "python3 images/smoke.py",
                "python3 images/verify_cli.py",
                '--architecture "arm64"',
                "python3 images/verify_registry_size.py",
                '--platform "linux/arm64"',
                '--budget-mib "$BUDGET_MIB"',
            ),
        ),
    }
    for surface, (text, required) in sorted(surfaces.items()):
        for fragment in required:
            if fragment not in text:
                raise ValueError(f"{surface} no longer runs: {fragment}")


def verify_public_wording(documents: dict[str, str], version: str) -> None:
    """Read every recipient surface as a reader of the public repo."""

    for name, text in sorted(documents.items()):
        # Prose is wrapped at 60 columns, so almost every phrase
        # this looks for is split across a line break in the file
        # a reader sees rendered as one sentence. Matching the
        # raw bytes would find only the phrases that happened to
        # fit on one line.
        lowered = " ".join(text.split()).lower()
        for phrase in RETIRED_VISIBILITY_PHRASES:
            if phrase in lowered:
                raise ValueError(f"retired visibility wording in {name}: {phrase}")
    readme = " ".join(documents["README.md"].split())
    # This release is published, so nothing about it may be
    # deferred to a publication that already happened. The
    # marketplace distribution in `trycopilotai/skills` is a
    # different repository's release, but it is pinned too and
    # must not retain a future-publication qualification.
    deferred = (
        "tag is published",
        "only once the matching image package is published",
        f"Once the matching `v{version}` release is published",
        "marketplace release is published",
    )
    for phrase in deferred:
        if phrase in readme:
            raise ValueError(f"README defers the published release: {phrase}")
    conditions = (
        (f"uses: trycopilotai/lint@v{version}", 1),
        (f"release=v{version}", 2),
        (f"--image-manifest release-manifest-{version}.json", 2),
        (f"`ghcr.io/trycopilotai/lint-<language>:{version}`", 1),
    )
    for phrase, count in conditions:
        if readme.count(phrase) != count:
            raise ValueError(
                f"README states {readme.count(phrase)} of {count}: {phrase}"
            )


def verify_inventory_documentation(
    readme: str,
    canonical: frozenset[str],
    inventory_count: int,
) -> None:
    """Describe the inventory set the repository actually ships."""

    normalized = " ".join(readme.split())
    for architecture in sorted(canonical):
        label = ARCHITECTURE_LABELS[architecture]
        # One inventory exists per formatter build target, not
        # per language image. Calling the set a count of images
        # read as a claim that 13 of the 28 published images
        # have no checked-in inventory behind them.
        claim = (
            "The checked-in canonical inventory set covers the "
            f"{inventory_count} {label} formatter build targets"
        )
        if claim not in normalized:
            raise ValueError(f"README does not state the {architecture} inventory set")
    for architecture in sorted(set(ARCHITECTURE_LABELS) - set(canonical)):
        label = ARCHITECTURE_LABELS[architecture]
        if f"No {label} canonical inventory exists" not in normalized:
            raise ValueError(f"README must deny a {architecture} canonical inventory")
        if (
            f"canonical inventory set covers the {inventory_count} {label}"
            in normalized
        ):
            raise ValueError(f"README claims a {architecture} canonical inventory")


def verify_release_surfaces() -> None:
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    release_trust_root = RELEASE_TRUST_ROOT
    if "private release candidate" in release.lower():
        raise ValueError("release metadata contains private-candidate wording")
    if release.count("uses: aquasecurity/trivy-action@") != 2:
        raise ValueError("release must scan both image platforms")
    if "platforms=linux/amd64" not in release:
        raise ValueError("private release platform is not AMD64-only")
    if 'if test "$REPOSITORY_VISIBILITY" = "public"; then' not in release:
        raise ValueError("public release platform expansion is missing")
    if "platforms=linux/amd64,linux/arm64" not in release:
        raise ValueError("public release platform set is incomplete")
    normalized_release = " ".join(release.split())
    platform_input = 'platforms: "${{ steps.platforms.outputs.value }}"'
    if platform_input not in normalized_release:
        raise ValueError("release build does not use the visibility-bound platforms")
    visibility_gate = "if: needs.matrix.outputs.repository_visibility == 'public'"
    for step_name in (
        "Set up QEMU",
        "Attest image",
        "Scan ARM64 image",
        "Attest source archive",
    ):
        step_parts = release.split(f"- name: {step_name}", 1)
        if len(step_parts) != 2:
            raise ValueError(f"release step is missing: {step_name}")
        step = step_parts[1].split("- name:", 1)[0]
        if visibility_gate not in " ".join(step.split()):
            raise ValueError(
                f"release step must wait for public visibility: {step_name}"
            )
    arm64_job = release.split("\n  verify-arm64:\n", 1)
    if len(arm64_job) != 2:
        raise ValueError("native ARM64 release verification is missing")
    arm64_header = arm64_job[1].split("    steps:", 1)[0]
    if "needs.matrix.outputs.repository_visibility == 'public'" not in " ".join(
        arm64_header.split()
    ):
        raise ValueError("native ARM64 verification must use the shared visibility")
    for platform in ("linux/amd64", "linux/arm64"):
        marker = f"TRIVY_PLATFORM: {platform}"
        if release.count(marker) != 1:
            raise ValueError(f"release scan is missing {platform}")
    if "ignore-unfixed: true" in release:
        raise ValueError("release scan must not ignore unfixed findings")
    allowed_signers = (ROOT / ".github" / "release-allowed-signers").read_text(
        encoding="utf-8"
    )
    if not allowed_signers.startswith("trycopilotai-release ssh-ed25519 "):
        raise ValueError("release signer trust root is missing")
    trust_checkout = release.split("- name: Check out release trust root", 1)
    if len(trust_checkout) != 2:
        raise ValueError("release trust root checkout is missing")
    trust_checkout = trust_checkout[1].split("- name: Verify signed release tag", 1)
    if len(trust_checkout) != 2:
        raise ValueError("release trust root checkout is not isolated")
    trust_checkout_text = trust_checkout[0]
    if f"ref: {release_trust_root}" not in trust_checkout_text:
        raise ValueError("release trust root is not pinned to the reviewed commit")
    if "path: release-trust" not in trust_checkout_text:
        raise ValueError("release trust root checkout has the wrong path")
    if "sparse-checkout: .github/release-allowed-signers" not in trust_checkout_text:
        raise ValueError("release trust root checkout is not signer-only")
    if "persist-credentials: false" not in trust_checkout_text:
        raise ValueError("release trust root checkout retains credentials")
    if "gpg.format=ssh" not in release:
        raise ValueError("release tag verification does not require SSH signatures")
    signer_configuration = (
        "gpg.ssh.allowedSignersFile=$GITHUB_WORKSPACE/release-trust/"
        ".github/release-allowed-signers"
    )
    if signer_configuration not in release:
        raise ValueError("release tag verification is missing its pinned trust root")
    candidate_configuration = (
        "gpg.ssh.allowedSignersFile=.github/release-allowed-signers"
    )
    if candidate_configuration in release:
        raise ValueError("release tag verification trusts the candidate signer file")
    if 'verify-tag "$RELEASE_REF"' not in release:
        raise ValueError("release tag signature is not verified")
    digest_verification = "python3 tools/release_manifest.py"
    digest_promotion = "for record in digests/*.json"
    if "--verify-only" not in release:
        raise ValueError("release digest-set verification is missing")
    if release.index(digest_verification) > release.index(digest_promotion):
        raise ValueError("release digests are promoted before verification")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    release_base = "https://github.com/trycopilotai/lint/releases/download/$release"
    if readme.count(release_base) != 2:
        raise ValueError("public skill installs must use the release archive")
    if readme.count("release=v0.1.6") != 2:
        raise ValueError("public skill installs must pin the release tag")
    if readme.count('version="${release#v}"') != 2:
        raise ValueError("public skill installs must derive the archive version")
    if readme.count("shasum -a 256 -c") != 2:
        raise ValueError("public skill installs must verify the checksums")
    if "gh api repos/trycopilotai/lint/tarball" in readme:
        raise ValueError("public skill installs must not require GitHub login")
    for invocation in ("`/lint`", "`/lint:lint`", "`$lint`", "`@lint`"):
        if invocation not in readme:
            raise ValueError(f"public skill invocation is missing: {invocation}")

    issue_surfaces = (
        ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml",
        ROOT / ".github" / "ISSUE_TEMPLATE" / "formatter.yml",
        ROOT / ".github" / "ISSUE_TEMPLATE" / "supply-chain.yml",
        ROOT / ".github" / "labels.yml",
    )
    for path in issue_surfaces:
        if not path.is_file():
            raise ValueError(f"issue launch surface is missing: {path}")

    labels = (ROOT / ".github" / "labels.yml").read_text(encoding="utf-8")
    for label in ("formatter", "supply-chain", "good first issue"):
        if f"name: {label}" not in labels:
            raise ValueError(f"label definition is missing: {label}")

    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    if release_trust_root not in security:
        raise ValueError("SECURITY.md documents a different release trust root")


def recipient_documents() -> dict[str, str]:
    """Return every text surface a reader of the repository meets."""

    paths = [
        ROOT / "README.md",
        ROOT / "SECURITY.md",
        ROOT / "CONTRIBUTING.md",
        ROOT / "action.yml",
        ROOT / ".claude-plugin" / "plugin.json",
        ROOT / ".codex-plugin" / "plugin.json",
        ROOT / ".github" / "labels.yml",
    ]
    paths.extend(sorted((ROOT / "docs").glob("*.md")))
    for pattern in ("*.md", "*.py", "*.yaml"):
        paths.extend(sorted((ROOT / "skills").rglob(pattern)))
    paths.extend(sorted((ROOT / ".github" / "workflows").glob("*.yml")))
    paths.extend(sorted((ROOT / ".github" / "ISSUE_TEMPLATE").glob("*.yml")))
    documents: dict[str, str] = {}
    for path in paths:
        if not path.is_file() or path.is_symlink():
            continue
        documents[path.relative_to(ROOT).as_posix()] = path.read_text(encoding="utf-8")
    return documents


def verify_inventory_set(verifier, inventory_root: Path | None = None) -> int:
    root = inventory_root
    if root is None:
        root = ROOT / "images" / "inventories"
    matrix = json.loads((ROOT / "images" / "matrix.json").read_text())
    targets = sorted(str(row["target"]) for row in matrix["images"])
    expected = {
        root / f"{target}-{architecture}.json"
        for target in targets
        for architecture in sorted(verifier.CANONICAL_INVENTORY_ARCHITECTURES)
    }
    if len(expected) != 15:
        raise ValueError("canonical image inventory set must contain 15 files")
    if not root.is_dir() or root.is_symlink():
        raise ValueError("canonical image inventory directory is missing")
    actual = set(root.iterdir())
    if actual != expected:
        missing = sorted(path.name for path in expected - actual)
        extra = sorted(path.name for path in actual - expected)
        raise ValueError(
            f"canonical image inventory set differs: missing={missing} extra={extra}"
        )
    for path in sorted(expected):
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"image inventory is not a regular file: {path}")
        payload = path.read_bytes()
        value = verifier.validate_inventory(json.loads(payload))
        target, architecture = path.stem.rsplit("-", 1)
        if value["target"] != target or value["architecture"] != architecture:
            raise ValueError(f"image inventory identity differs: {path}")
        if verifier.render_inventory(value) != payload:
            raise ValueError(f"image inventory is not canonical: {path}")
    return len(expected)


def main() -> int:
    files = tracked_files()
    verify_python_style(files)
    verify_normal_names(files)
    verify_manifests()
    verifier = load_image_verifier()
    verifier.validate_coverage()
    verifier.validate_sources()
    inventory_count = verify_inventory_set(verifier)
    verify_demo()
    legal_payload_generator = load_legal_payload_generator()
    legal_payload_generator.check_payloads()
    closure_verifier = load_dependency_closure_verifier()
    closure_verifier.validate_document(closure_verifier.load_document())
    verify_actions(files)
    verify_release_surfaces()
    workflows = {}
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        workflows[path.name] = path.read_text(encoding="utf-8")
    verify_workflow_triggers(workflows)
    verify_inventory_architecture_gate(
        workflows,
        verifier.CANONICAL_INVENTORY_ARCHITECTURES,
    )
    verify_architecture_coverage(workflows)
    documents = recipient_documents()
    version = json.loads((ROOT / "images" / "matrix.json").read_text())["version"]
    verify_public_wording(documents, version)
    verify_inventory_documentation(
        documents["README.md"],
        verifier.CANONICAL_INVENTORY_ARCHITECTURES,
        inventory_count,
    )
    # Counted, not asserted. A hardcoded 26 reported the number
    # the author expected rather than the number the manifest
    # contains, so it would have kept reporting 26 after a
    # language was added or removed.
    languages = json.loads((ROOT / "languages.json").read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "status": "ok",
                "tracked_files": len(files),
                "languages": len(languages["languages"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
