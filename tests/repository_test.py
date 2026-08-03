from __future__ import annotations

import importlib.util
import hashlib
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]

# The published `main` commit whose allowed-signers file the
# release job trusts. It is written out here rather than read
# from the workflow, so a workflow that repins itself has to
# disagree with this file to pass.
RELEASE_TRUST_ROOT = "3d5d4ee7b83b2c6442039b8a72a571c729ffcead"

# The trust root the release workflow used before this one. It
# was never pushed, so `actions/checkout` cannot fetch it and
# the release fails before it verifies a signature.
UNPUBLISHED_TRUST_ROOT = "5ec8a57d4cdddb23e446cf2bc69596d748a4d285"


def load_release_matrix():
    path = ROOT / "tools" / "release_matrix.py"
    specification = importlib.util.spec_from_file_location(
        "release_matrix_under_test",
        path,
    )
    if specification is None:
        raise RuntimeError("could not create release matrix specification")
    if specification.loader is None:
        raise RuntimeError("release matrix specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def load_image_verifier():
    path = ROOT / "images" / "verify_images.py"
    specification = importlib.util.spec_from_file_location(
        "image_verifier_under_test",
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


def load_changed_matrix():
    path = ROOT / "tools" / "changed_matrix.py"
    specification = importlib.util.spec_from_file_location(
        "changed_matrix_under_test",
        path,
    )
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def load_repository_verifier():
    path = ROOT / "tools" / "verify_repo.py"
    specification = importlib.util.spec_from_file_location(
        "repository_verifier_under_test",
        path,
    )
    if specification is None:
        raise RuntimeError("could not create repository verifier specification")
    if specification.loader is None:
        raise RuntimeError("repository verifier specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def workflow_sources() -> dict[str, str]:
    sources: dict[str, str] = {}
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        sources[path.name] = path.read_text(encoding="utf-8")
    return sources


RELEASE_MATRIX = load_release_matrix()
IMAGE_VERIFIER = load_image_verifier()


class ReleaseMatrixTest(unittest.TestCase):
    def test_every_language_has_one_release_row(self) -> None:
        rows = RELEASE_MATRIX.release_rows()
        languages = [row["language"] for row in rows]

        self.assertEqual(28, len(rows))
        self.assertEqual(28, len(set(languages)))

        targets = {row["language"]: row["target"] for row in rows}
        self.assertEqual("prettier", targets["css"])
        self.assertEqual("prettier", targets["scss"])
        self.assertEqual("prettier", targets["less"])

    def test_evidenced_runtime_budgets_are_preserved(self) -> None:
        rows = RELEASE_MATRIX.release_rows()
        budgets = {row["language"]: row["budget_mib"] for row in rows}

        self.assertEqual(90, budgets["rust"])
        self.assertEqual(140, budgets["kotlin"])
        self.assertEqual(360, budgets["julia"])


class ChangedMatrixTest(unittest.TestCase):
    def test_full_matrix_emits_every_addressable_language(self) -> None:
        module = load_changed_matrix()
        include = module.include_rows(None)
        expected = {
            language for row in module.all_rows() for language in row["languages"]
        }

        self.assertEqual(28, len(include))
        self.assertEqual(expected, {row["language"] for row in include})
        self.assertEqual(28, len({row["language"] for row in include}))

    def test_css_preprocessor_extensions_select_independent_languages(self) -> None:
        module = load_changed_matrix()

        self.assertEqual("css", module.language_for(Path("theme.css")))
        self.assertEqual("scss", module.language_for(Path("theme.scss")))
        self.assertEqual("less", module.language_for(Path("theme.less")))

        prettier = next(row for row in module.all_rows() if row["target"] == "prettier")
        with mock.patch.object(
            module,
            "changed_paths",
            return_value=[Path("fixtures/scss/needs.scss")],
        ):
            selected = module.selected_rows("base")

        self.assertEqual([prettier], selected)

    def test_selected_shared_family_emits_every_alias(self) -> None:
        module = load_changed_matrix()
        prettier = next(row for row in module.all_rows() if row["target"] == "prettier")
        with mock.patch.object(
            module,
            "changed_paths",
            return_value=[Path("README.md")],
        ):
            include = module.include_rows("base")

        self.assertEqual(
            set(prettier["languages"]),
            {row["language"] for row in include},
        )
        self.assertEqual(
            {"prettier"},
            {row["target"] for row in include},
        )

    def test_globbed_manifest_filenames_select_their_family(self) -> None:
        module = load_changed_matrix()

        for filename in (
            "requirements-dev.txt",
            "requirements-test.txt",
            "constraints-linux.txt",
        ):
            with self.subTest(filename=filename):
                self.assertEqual(
                    "requirements",
                    module.language_for(Path(filename)),
                )
        self.assertIsNone(module.language_for(Path("ordinary.txt")))

    def test_image_inputs_select_their_own_image(self) -> None:
        module = load_changed_matrix()
        rows = module.all_rows()

        # Without an explicit mapping the entrypoints fall through
        # to extension matching and the lockfile has no recognized
        # extension, so a change can rebuild the wrong image or no
        # image at all.
        expected = {
            "images/closures/buildifier-go-build-info.txt": "bazel",
            "images/closures/ktlint-runtime-classpath.txt": "kotlin",
            "images/closures/shfmt-go-build-info.txt": "shell",
            "images/julia_entrypoint.jl": "julia",
            "images/kotlin_entrypoint.c": "kotlin",
            "images/requirements_entrypoint.py": "requirements",
            "images/swift/Package.resolved": "swift",
            "images/xml_entrypoint.c": "xml",
        }
        for source, language in expected.items():
            with self.subTest(source=source):
                self.assertEqual(
                    language,
                    module.IMAGE_INPUT_TARGETS[source],
                )
                targets = {
                    row["target"] for row in rows if language in row["languages"]
                }
                self.assertEqual(1, len(targets))

    def test_shared_image_harness_changes_select_every_image(self) -> None:
        module = load_changed_matrix()
        expected = module.all_rows()
        for source in (
            "images/generate_image_inventory.py",
            "images/dependency_closures.json",
            "images/smoke.py",
            "images/verify_cli.py",
            "images/verify_images.py",
            "images/verify_registry_size.py",
            "images/verify_tool_version.py",
            "tools/changed_matrix.py",
            "tools/verify_dependency_closures.py",
        ):
            with self.subTest(source=source), mock.patch.object(
                module,
                "changed_paths",
                return_value=[Path(source)],
            ):
                self.assertEqual(expected, module.selected_rows("base"))

    def test_inventory_change_selects_its_exact_image_target(self) -> None:
        module = load_changed_matrix()
        expected = [row for row in module.all_rows() if row["target"] == "julia"]
        with mock.patch.object(
            module,
            "changed_paths",
            return_value=[Path("images/inventories/julia-arm64.json")],
        ):
            self.assertEqual(expected, module.selected_rows("base"))

    def test_legal_payload_change_selects_its_exact_image_target(self) -> None:
        module = load_changed_matrix()
        expected = [row for row in module.all_rows() if row["target"] == "swift"]
        with mock.patch.object(
            module,
            "changed_paths",
            return_value=[Path("images/licenses/swift/swift-LICENSE.txt")],
        ):
            self.assertEqual(expected, module.selected_rows("base"))


class ImageContentsTest(unittest.TestCase):
    def test_reported_language_count_comes_from_the_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "languages.json"
            manifest.write_text(
                json.dumps({"languages": [{"id": "only"}]}),
                encoding="utf-8",
            )
            with mock.patch.object(
                IMAGE_VERIFIER,
                "LANGUAGES_PATH",
                manifest,
            ):
                self.assertEqual(1, IMAGE_VERIFIER.language_count())

    def test_prettier_image_loads_configuration_with_locked_options(self) -> None:
        dockerfile = (ROOT / "images" / "Dockerfile").read_text(encoding="utf-8")

        self.assertNotIn("--no-config", dockerfile)
        self.assertNotIn("--no-editorconfig", dockerfile)
        self.assertIn('"--ignore-path", "/work/.lint-empty-ignore"', dockerfile)
        self.assertIn('"--print-width", "60"', dockerfile)
        self.assertIn('"--prose-wrap", "always"', dockerfile)
        self.assertIn('"--trailing-comma", "none"', dockerfile)

    def test_black_image_loads_configuration_with_locked_line_length(self) -> None:
        dockerfile = (ROOT / "images" / "Dockerfile").read_text(encoding="utf-8")

        self.assertNotIn('"--config", "/dev/null"', dockerfile)
        self.assertIn('"--line-length", "88"', dockerfile)

    def test_go_formatters_are_built_with_the_pinned_toolchain(self) -> None:
        dockerfile = (ROOT / "images" / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("FROM ${GO_IMAGE} AS go-tools-build", dockerfile)
        self.assertIn("./buildifier", dockerfile)
        self.assertIn("mvdan.cc/sh/v3/cmd/shfmt@v3.13.1", dockerfile)
        self.assertIn('test "$(/shfmt --version)" = "v3.13.1"', dockerfile)

    def test_every_final_image_copies_a_runtime_tool_identity(self) -> None:
        dockerfile = (ROOT / "images" / "Dockerfile").read_text(encoding="utf-8")
        targets = json.loads(
            (ROOT / "images" / "matrix.json").read_text(encoding="utf-8")
        )["images"]

        for row in targets:
            target = row["target"]
            with self.subTest(target=target):
                body = re.search(
                    rf"^FROM [^\n]+ AS {re.escape(target)}\n(.*?)(?=^FROM |\Z)",
                    dockerfile,
                    flags=re.MULTILINE | re.DOTALL,
                )
                self.assertIsNotNone(body)
                self.assertIn("/lint-tool-version", body.group(1))

    def test_every_final_image_names_the_canonical_repository_source(self) -> None:
        dockerfile = (ROOT / "images" / "Dockerfile").read_text(encoding="utf-8")
        targets = json.loads(
            (ROOT / "images" / "matrix.json").read_text(encoding="utf-8")
        )["images"]

        expected = (
            "LABEL org.opencontainers.image.source="
            '"https://github.com/trycopilotai/lint"'
        )
        for row in targets:
            target = row["target"]
            with self.subTest(target=target):
                body = re.search(
                    rf"^FROM [^\n]+ AS {re.escape(target)}\n(.*?)(?=^FROM |\Z)",
                    dockerfile,
                    flags=re.MULTILINE | re.DOTALL,
                )
                self.assertIsNotNone(body)
                self.assertIn(expected, body.group(1))

    def test_julia_image_removes_non_runtime_manifests(self) -> None:
        dockerfile = (ROOT / "images" / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("/usr/local/julia/share/julia", dockerfile)
        self.assertIn("/usr/local/julia/share/doc", dockerfile)
        self.assertIn("/opt/julia-depot/packages", dockerfile)
        self.assertIn(r"-type d \( -name docs -o -name test \)", dockerfile)

    def test_black_scanner_exception_is_scoped_and_expires(self) -> None:
        ignore = (ROOT / ".trivyignore.yaml").read_text(encoding="utf-8")
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")

        self.assertIn("CVE-2026-32274", ignore)
        self.assertIn("pkg:pypi/black@24.10.0", ignore)
        self.assertIn("expired_at: 2026-10-31", ignore)
        for surface in (ignore, security):
            self.assertIn("python-cell-magics", surface)
            self.assertNotIn("--config /dev/null", surface)


class ContinuousIntegrationWorkflowTest(unittest.TestCase):
    def test_local_integration_runs_on_ubuntu_and_macos(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("local-integration:", workflow)
        self.assertIn("ubuntu-latest", workflow)
        self.assertIn("macos-latest", workflow)
        self.assertIn("Verify local formatter transaction", workflow)
        self.assertIn("Verify failed multi-language transaction", workflow)
        self.assertIn("Exercise service API", workflow)
        self.assertIn("python tests/http_service_test.py", workflow)
        self.assertIn("git status --porcelain", workflow)

    def test_full_image_runs_cover_every_required_trigger(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "images.yml").read_text(
            encoding="utf-8"
        )
        matrix = load_changed_matrix().include_rows(None)

        self.assertIn("branches: [main]", workflow)
        self.assertNotIn('tags: ["v*"]', workflow)
        self.assertIn("schedule:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("github.event_name != 'pull_request'", workflow)
        self.assertEqual(28, len(matrix))
        self.assertEqual(28, len({row["language"] for row in matrix}))

    def test_full_image_runs_verify_sboms_and_vulnerabilities(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "images.yml").read_text(
            encoding="utf-8"
        )

        self.assertEqual(
            4,
            workflow.count("uses: aquasecurity/trivy-action@"),
        )
        self.assertEqual(2, workflow.count("format: cyclonedx"))
        self.assertEqual(2, workflow.count("severity: HIGH,CRITICAL"))
        self.assertEqual(2, workflow.count("Verify SBOM"))
        self.assertEqual(2, workflow.count("Upload SBOM"))
        self.assertNotIn("ignore-unfixed: true", workflow)

    def test_images_cover_parity_and_source_immutability(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "images.yml").read_text(
            encoding="utf-8"
        )
        amd64 = workflow.split("  build:", 1)[1].split("  build-arm64:", 1)[0]
        arm64 = workflow.split("  build-arm64:", 1)[1]

        self.assertEqual(
            2,
            workflow.count("Verify local and Docker golden parity"),
        )
        self.assertNotIn("matrix.target == 'requirements'", workflow)
        self.assertNotIn("fixtures/requirements", workflow)
        for architecture, job in (("amd64", amd64), ("arm64", arm64)):
            with self.subTest(architecture=architecture):
                parity = job.split(
                    "- name: Verify local and Docker golden parity",
                    1,
                )[
                    1
                ].split("- name:", 1)[0]
                self.assertNotIn("if:", parity)
                self.assertIn("python3 images/verify_cli.py", parity)
                self.assertIn('matrix.target }}"', parity)
                self.assertIn('matrix.language }}"', parity)
                self.assertIn(f'--architecture "{architecture}"', parity)
        self.assertGreaterEqual(workflow.count("git diff --exit-code"), 2)
        self.assertGreaterEqual(workflow.count("git status --porcelain"), 2)

    def test_images_verify_runtime_tool_identity_on_both_architectures(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "images.yml").read_text(
            encoding="utf-8"
        )

        self.assertEqual(2, workflow.count("python3 images/verify_tool_version.py"))
        self.assertEqual(2, workflow.count("Verify runtime formatter identity"))
        self.assertIn('matrix.language }}:ci"', workflow)
        self.assertIn('matrix.language }}:ci-arm64"', workflow)
        arm64 = workflow.split("  build-arm64:", 1)[1]
        self.assertIn(
            "github.event.repository.visibility == 'public'",
            arm64.split("    needs:", 1)[0],
        )


class ReleaseWorkflowTest(unittest.TestCase):
    def test_release_runs_are_serialized_by_release_ref(self) -> None:
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        concurrency = release.split("concurrency:", 1)[1].split("env:", 1)[0]

        self.assertIn("inputs.release-ref", concurrency)
        self.assertIn("github.ref_name", concurrency)
        self.assertIn("cancel-in-progress: false", concurrency)

    def test_release_tag_signature_uses_the_pinned_trust_root(self) -> None:
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        allowed_signers = (ROOT / ".github" / "release-allowed-signers").read_text(
            encoding="utf-8"
        )

        self.assertTrue(allowed_signers.startswith("trycopilotai-release "))
        self.assertIn("ssh-ed25519 ", allowed_signers)
        self.assertRegex(RELEASE_TRUST_ROOT, r"\A[0-9a-f]{40}\Z")
        trust_checkout = release.split(
            "- name: Check out release trust root",
            1,
        )[
            1
        ].split("- name: Verify signed release tag", 1)[0]
        self.assertIn(f"ref: {RELEASE_TRUST_ROOT}", trust_checkout)
        self.assertIn("path: release-trust", trust_checkout)
        self.assertNotIn("${{ env.RELEASE_REF }}", trust_checkout)
        self.assertIn("gpg.format=ssh", release)
        self.assertIn(
            "gpg.ssh.allowedSignersFile=$GITHUB_WORKSPACE/release-trust/"
            ".github/release-allowed-signers",
            release,
        )
        self.assertNotIn(
            "gpg.ssh.allowedSignersFile=.github/release-allowed-signers",
            release,
        )
        self.assertIn('verify-tag "$RELEASE_REF"', release)

    def test_candidate_signer_cannot_authorize_a_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "candidate"
            repository.mkdir()
            candidate_key = Path(directory) / "candidate-key"
            trusted_key = Path(directory) / "trusted-key"
            for key in (candidate_key, trusted_key):
                subprocess.run(
                    [
                        "ssh-keygen",
                        "-q",
                        "-t",
                        "ed25519",
                        "-N",
                        "",
                        "-f",
                        str(key),
                    ],
                    check=True,
                )

            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            for name, value in (
                ("user.name", "Release Test"),
                ("user.email", "release-test@example.invalid"),
                ("gpg.format", "ssh"),
                ("user.signingkey", str(candidate_key)),
            ):
                subprocess.run(
                    ["git", "-C", str(repository), "config", name, value],
                    check=True,
                )

            candidate_signers = repository / ".github" / "release-allowed-signers"
            candidate_signers.parent.mkdir()
            candidate_public_key = candidate_key.with_suffix(".pub").read_text(
                encoding="utf-8"
            )
            candidate_signers.write_text(
                f"trycopilotai-release {candidate_public_key}",
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "-C", str(repository), "add", ".github"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repository), "commit", "-qm", "candidate"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repository), "tag", "-sm", "candidate", "v0.0.0"],
                check=True,
            )

            pinned_signers = (
                repository / "release-trust" / ".github" / "release-allowed-signers"
            )
            pinned_signers.parent.mkdir(parents=True)
            trusted_public_key = trusted_key.with_suffix(".pub").read_text(
                encoding="utf-8"
            )
            pinned_signers.write_text(
                f"trycopilotai-release {trusted_public_key}",
                encoding="utf-8",
            )

            candidate_result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "-c",
                    "gpg.format=ssh",
                    "-c",
                    f"gpg.ssh.allowedSignersFile={candidate_signers}",
                    "verify-tag",
                    "v0.0.0",
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            pinned_result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "-c",
                    "gpg.format=ssh",
                    "-c",
                    f"gpg.ssh.allowedSignersFile={pinned_signers}",
                    "verify-tag",
                    "v0.0.0",
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

        self.assertEqual(0, candidate_result.returncode, candidate_result.stderr)
        self.assertNotEqual(0, pinned_result.returncode, pinned_result.stdout)

    def test_release_signature_verification_is_documented(self) -> None:
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        normalized = " ".join(security.split())

        self.assertIn("verify-tag v0.1.5", security)
        self.assertIn(".github/release-allowed-signers", security)
        self.assertIn(RELEASE_TRUST_ROOT, security)
        self.assertIn(
            "SHA256:DfKMRhe4zXosajTxEcDqVDi7dnQ/pwtvj/lLBDn7a9k",
            security,
        )
        self.assertIn("not independent identity proof", normalized)

    def test_promotion_requires_the_complete_digest_set(self) -> None:
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )

        verification = "python3 tools/release_manifest.py"
        promotion = "for record in digests/*.json"
        self.assertIn("--verify-only", release)
        self.assertLess(release.index(verification), release.index(promotion))

    def test_attestations_wait_for_public_visibility(self) -> None:
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )

        for step_name in ("Attest image", "Attest source archive"):
            with self.subTest(step=step_name):
                step = release.split(f"- name: {step_name}", 1)[1].split(
                    "- name:",
                    1,
                )[0]
                self.assertIn(
                    "if: steps.visibility.outputs.value == 'public'",
                    step,
                )
        self.assertGreaterEqual(
            release.count('gh api "repos/$GITHUB_REPOSITORY"'),
            2,
        )

    def test_private_release_builds_only_amd64(self) -> None:
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        normalized = " ".join(release.split())

        self.assertIn("platforms=linux/amd64", release)
        self.assertIn('if test "$visibility" = "public"; then', release)
        self.assertIn("platforms=linux/amd64,linux/arm64", release)
        self.assertIn(
            'platforms: "${{ steps.visibility.outputs.platforms }}"',
            normalized,
        )
        for step_name in (
            "Set up QEMU",
            "Verify ARM64 image",
            "Scan ARM64 image",
        ):
            with self.subTest(step=step_name):
                step = release.split(f"- name: {step_name}", 1)[1].split(
                    "- name:",
                    1,
                )[0]
                self.assertIn(
                    "if: steps.visibility.outputs.value == 'public'",
                    step,
                )

    def test_vulnerability_scan_covers_both_platforms(self) -> None:
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )

        self.assertEqual(
            2,
            release.count("uses: aquasecurity/trivy-action@"),
        )
        self.assertEqual(1, release.count("TRIVY_PLATFORM: linux/amd64"))
        self.assertEqual(1, release.count("TRIVY_PLATFORM: linux/arm64"))
        self.assertNotIn("ignore-unfixed: true", release)

    def test_release_repeats_cli_and_registry_size_verification(self) -> None:
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )

        self.assertEqual(2, release.count("python3 images/verify_cli.py"))
        self.assertEqual(
            2,
            release.count("python3 images/verify_registry_size.py"),
        )
        self.assertIn('--platform "linux/amd64"', release)
        self.assertIn('--platform "linux/arm64"', release)
        self.assertIn('--architecture "amd64"', release)
        self.assertIn('--architecture "arm64"', release)

    def test_release_repeats_runtime_tool_identity_verification(self) -> None:
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )

        self.assertEqual(2, release.count("python3 images/verify_tool_version.py"))
        self.assertIn("--image lint-release-test:amd64", release)
        self.assertIn("--image lint-release-test:arm64", release)


class WorkflowTriggerTest(unittest.TestCase):
    """A version tag push must start the release workflow alone.

    A tag push and the branch push that carries the same commit
    are two events. When CI and Images also subscribe to tags,
    every one of their jobs runs twice on one publication, and
    the concurrency groups differ by ref so nothing dedupes
    them.
    """

    def setUp(self) -> None:
        self.verifier = load_repository_verifier()
        self.workflows = workflow_sources()

    def test_only_the_release_workflow_runs_for_version_tags(self) -> None:
        triggers = {}
        for name, text in self.workflows.items():
            triggers[name] = self.verifier.workflow_triggers(text)

        for name in ("ci.yml", "images.yml"):
            with self.subTest(workflow=name):
                self.assertEqual(
                    {"pull_request", "push", "schedule", "workflow_dispatch"},
                    set(triggers[name]),
                )
                self.assertEqual(["branches: [main]"], triggers[name]["push"])
        self.assertEqual({"push", "workflow_dispatch"}, set(triggers["release.yml"]))
        self.assertEqual(['tags: ["v*"]'], triggers["release.yml"]["push"])
        self.verifier.verify_workflow_triggers(self.workflows)

    def test_restoring_the_tag_trigger_fails(self) -> None:
        for name in ("ci.yml", "images.yml"):
            with self.subTest(workflow=name):
                mutated = dict(self.workflows)
                mutated[name] = mutated[name].replace(
                    "    branches: [main]\n",
                    '    branches: [main]\n    tags: ["v*"]\n',
                    1,
                )
                self.assertNotEqual(self.workflows[name], mutated[name])
                with self.assertRaisesRegex(
                    ValueError,
                    "push trigger is not main-only",
                ):
                    self.verifier.verify_workflow_triggers(mutated)

    def test_release_gaining_a_branch_push_fails(self) -> None:
        mutated = dict(self.workflows)
        mutated["release.yml"] = mutated["release.yml"].replace(
            '    tags: ["v*"]\n',
            '    branches: [main]\n    tags: ["v*"]\n',
            1,
        )

        with self.assertRaisesRegex(ValueError, "push trigger is not tag-only"):
            self.verifier.verify_workflow_triggers(mutated)

    def test_dropping_a_continuous_trigger_fails(self) -> None:
        mutated = dict(self.workflows)
        mutated["ci.yml"] = mutated["ci.yml"].replace(
            '  schedule:\n    - cron: "31 9 * * 1"\n',
            "",
            1,
        )
        self.assertNotIn("schedule:", mutated["ci.yml"])

        with self.assertRaisesRegex(ValueError, "declares the wrong triggers"):
            self.verifier.verify_workflow_triggers(mutated)


class InventoryArchitectureGateTest(unittest.TestCase):
    """Image content checks are bound to the inventories on disk.

    `images/verify_images.py --architecture X` loads the
    canonical inventory for X. In the release job that call runs
    after `push: true`, so an architecture with no inventory
    fails once the images are already in the registry.
    """

    def setUp(self) -> None:
        self.verifier = load_repository_verifier()
        self.workflows = workflow_sources()
        self.canonical = IMAGE_VERIFIER.CANONICAL_INVENTORY_ARCHITECTURES

    def test_only_amd64_inventories_are_checked_in(self) -> None:
        inventories = ROOT / "images" / "inventories"

        self.assertEqual({"amd64"}, set(self.canonical))
        self.assertEqual([], sorted(inventories.glob("*-arm64.json")))
        self.assertEqual(15, len(sorted(inventories.glob("*-amd64.json"))))

    def test_an_arm64_content_check_cannot_be_satisfied(self) -> None:
        target = sorted(row["target"] for row in RELEASE_MATRIX.release_rows())[0]

        with self.assertRaises(FileNotFoundError):
            IMAGE_VERIFIER.load_inventory(target, "arm64")

    def test_workflows_content_check_only_the_checked_in_set(self) -> None:
        for name in ("images.yml", "release.yml"):
            with self.subTest(workflow=name):
                self.assertEqual(
                    ["amd64"],
                    self.verifier.workflow_inventory_architectures(
                        self.workflows[name]
                    ),
                )
        self.verifier.verify_inventory_architecture_gate(
            self.workflows,
            self.canonical,
        )

    def test_restoring_the_arm64_content_check_fails(self) -> None:
        mutations = {
            "images.yml": (
                "      - name: Generate ARM64 SBOM",
                "      - name: Enforce compressed size budget\n"
                "        run: >-\n"
                "          python3 images/verify_images.py --image"
                ' "lint-x:ci-arm64"\n'
                '          --budget-mib "1" --target "black" --architecture\n'
                '          "arm64"\n'
                "      - name: Generate ARM64 SBOM",
            ),
            "release.yml": (
                "          python3 images/verify_registry_size.py \\\n"
                '            --image "$IMAGE" \\\n'
                '            --digest "$DIGEST" \\\n'
                '            --platform "linux/arm64" \\',
                "          python3 images/verify_images.py \\\n"
                "            --image lint-release-test:arm64 \\\n"
                '            --budget-mib "$BUDGET_MIB" \\\n'
                '            --target "$TARGET" \\\n'
                '            --architecture "arm64"\n'
                "          python3 images/verify_registry_size.py \\\n"
                '            --image "$IMAGE" \\\n'
                '            --digest "$DIGEST" \\\n'
                '            --platform "linux/arm64" \\',
            ),
        }
        for name, (old, new) in sorted(mutations.items()):
            with self.subTest(workflow=name):
                mutated = dict(self.workflows)
                mutated[name] = mutated[name].replace(old, new, 1)
                self.assertNotEqual(self.workflows[name], mutated[name])
                with self.assertRaisesRegex(ValueError, "content-checks"):
                    self.verifier.verify_inventory_architecture_gate(
                        mutated,
                        self.canonical,
                    )

    def test_dropping_the_amd64_content_check_fails(self) -> None:
        mutated = dict(self.workflows)
        mutated["release.yml"] = mutated["release.yml"].replace(
            "          python3 images/verify_images.py \\\n"
            "            --image lint-release-test:amd64 \\\n"
            '            --budget-mib "$BUDGET_MIB" \\\n'
            '            --target "$TARGET" \\\n'
            '            --architecture "amd64"\n',
            "",
            1,
        )
        self.assertNotEqual(self.workflows["release.yml"], mutated["release.yml"])

        with self.assertRaisesRegex(ValueError, "content-checks"):
            self.verifier.verify_inventory_architecture_gate(
                mutated,
                self.canonical,
            )

    def test_a_commented_command_is_not_an_invocation(self) -> None:
        # Both ARM64 jobs carry a comment naming the call they
        # deliberately do not make. Reading comments as commands
        # would make the explanation itself the finding.
        commented = "\n".join(
            (
                "jobs:",
                "  build:",
                "    steps:",
                "      - name: Explain",
                "        run: |",
                "          # python3 images/verify_images.py" ' --architecture "arm64"',
                "          true",
            )
        )

        self.assertEqual(
            [],
            self.verifier.workflow_inventory_architectures(commented),
        )


class ArchitectureCoverageTest(unittest.TestCase):
    """Dropping the inventory check must not drop ARM64 itself."""

    def setUp(self) -> None:
        self.verifier = load_repository_verifier()
        self.workflows = workflow_sources()

    def test_arm64_keeps_every_check_no_inventory_gates(self) -> None:
        self.verifier.verify_architecture_coverage(self.workflows)

        images = self.workflows["images.yml"].split("  build-arm64:", 1)[1]
        release = (
            self.workflows["release.yml"]
            .split(
                "- name: Verify ARM64 image",
                1,
            )[1]
            .split("- name: Attest image", 1)[0]
        )
        for surface in (images, release):
            # Each job explains the absent call in a comment, so
            # the check reads commands rather than prose.
            self.assertIn("images/verify_images.py", surface)
            self.assertNotIn(
                "images/verify_images.py",
                self.verifier.command_text(surface),
            )

    def test_dropping_an_arm64_check_fails(self) -> None:
        removals = (
            "python3 images/verify_tool_version.py",
            "python3 images/smoke.py",
            "python3 images/verify_registry_size.py",
        )
        for command in removals:
            with self.subTest(command=command):
                mutated = dict(self.workflows)
                arm64 = mutated["images.yml"].split("  build-arm64:", 1)
                mutated["images.yml"] = (
                    arm64[0] + "  build-arm64:" + arm64[1].replace(command, "true #", 1)
                )
                with self.assertRaisesRegex(ValueError, "no longer runs"):
                    self.verifier.verify_architecture_coverage(mutated)

    def test_dropping_the_arm64_release_check_fails(self) -> None:
        mutated = dict(self.workflows)
        head, tail = mutated["release.yml"].split("- name: Verify ARM64 image", 1)
        mutated["release.yml"] = (
            head
            + "- name: Verify ARM64 image"
            + tail.replace('--architecture "arm64"', '--architecture "amd64"', 1)
        )

        with self.assertRaisesRegex(ValueError, "no longer runs"):
            self.verifier.verify_architecture_coverage(mutated)


class PublicWordingTest(unittest.TestCase):
    """Every recipient surface is read as a public reader reads it."""

    def setUp(self) -> None:
        self.verifier = load_repository_verifier()
        self.documents = self.verifier.recipient_documents()
        self.version = json.loads(
            (ROOT / "images" / "matrix.json").read_text(encoding="utf-8")
        )["version"]

    def test_no_surface_describes_a_repository_that_is_not_public(self) -> None:
        self.assertIn("README.md", self.documents)
        self.assertIn("SECURITY.md", self.documents)
        self.assertIn(".github/workflows/release.yml", self.documents)
        self.verifier.verify_public_wording(self.documents, self.version)

    def test_each_retired_phrase_is_detected_across_a_line_break(self) -> None:
        # Prose is wrapped at 60 columns, so a stale phrase
        # reaches the file split across a newline and is read as
        # one sentence. Injecting it unwrapped would test only
        # the phrases short enough to fit on one line.
        for phrase in self.verifier.RETIRED_VISIBILITY_PHRASES:
            with self.subTest(phrase=phrase):
                wrapped = phrase.replace(" ", "\n", 1)
                mutated = dict(self.documents)
                mutated["README.md"] = (
                    f"{mutated['README.md']}\nBuilt {wrapped} for now.\n"
                )
                with self.assertRaisesRegex(ValueError, "retired visibility wording"):
                    self.verifier.verify_public_wording(mutated, self.version)

    def test_dropping_an_install_condition_fails(self) -> None:
        conditions = (
            f"Once the matching `v{self.version}` tag is published",
            f"Once the matching `v{self.version}` release is published",
            "which resolves\nonly once the matching image package is published",
        )
        for condition in conditions:
            with self.subTest(condition=condition):
                mutated = dict(self.documents)
                mutated["README.md"] = mutated["README.md"].replace(condition, "", 1)
                self.assertNotEqual(self.documents["README.md"], mutated["README.md"])
                with self.assertRaisesRegex(ValueError, "README states"):
                    self.verifier.verify_public_wording(mutated, self.version)

    def test_readme_denies_the_inventory_it_does_not_have(self) -> None:
        canonical = IMAGE_VERIFIER.CANONICAL_INVENTORY_ARCHITECTURES
        self.verifier.verify_inventory_documentation(
            self.documents["README.md"],
            canonical,
            15,
        )

        mutated = self.documents["README.md"].replace(
            "No ARM64 canonical inventory exists",
            "The ARM64 canonical inventory covers every image",
            1,
        )
        with self.assertRaisesRegex(ValueError, "must deny a arm64"):
            self.verifier.verify_inventory_documentation(mutated, canonical, 15)

    def test_claiming_an_arm64_inventory_set_fails(self) -> None:
        canonical = IMAGE_VERIFIER.CANONICAL_INVENTORY_ARCHITECTURES
        mutated = self.documents["README.md"].replace(
            "The checked-in canonical inventory set covers the 15 AMD64\nimages.",
            "The checked-in canonical inventory set covers the 15 AMD64\n"
            "images. The checked-in canonical inventory set covers the 15\n"
            "ARM64 images.",
            1,
        )
        self.assertNotEqual(self.documents["README.md"], mutated)

        with self.assertRaisesRegex(ValueError, "claims a arm64"):
            self.verifier.verify_inventory_documentation(mutated, canonical, 15)


class ReleaseTrustRootTest(unittest.TestCase):
    """The pinned trust root has to be fetchable by the runner."""

    def test_every_surface_pins_the_published_trust_root(self) -> None:
        verifier = load_repository_verifier()
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")

        self.assertEqual(RELEASE_TRUST_ROOT, verifier.RELEASE_TRUST_ROOT)
        self.assertIn(f"ref: {RELEASE_TRUST_ROOT}", release)
        self.assertIn(f'= "{RELEASE_TRUST_ROOT}"', release)
        self.assertIn(RELEASE_TRUST_ROOT, security)

    def test_the_unpublished_trust_root_is_no_longer_pinned(self) -> None:
        # `actions/checkout` cannot fetch a commit the remote does
        # not have, so pinning one fails the release before it
        # verifies any signature.
        surfaces = (
            ROOT / ".github" / "workflows" / "release.yml",
            ROOT / "SECURITY.md",
            ROOT / "tools" / "verify_repo.py",
        )
        self.assertNotEqual(RELEASE_TRUST_ROOT, UNPUBLISHED_TRUST_ROOT)
        for path in surfaces:
            with self.subTest(path=path.name):
                self.assertNotIn(
                    UNPUBLISHED_TRUST_ROOT,
                    path.read_text(encoding="utf-8"),
                )


class RepositoryVerificationWiringTest(unittest.TestCase):
    """`make verify` has to run the checks, not merely define them.

    The tests above call each detector directly, so deleting one
    breaks them. Deleting only its call from `main` would leave
    every one of them passing while `make verify` checked
    nothing.
    """

    def test_verification_runs_every_public_launch_detector(self) -> None:
        verifier = load_repository_verifier()
        source = inspect.getsource(verifier.main)

        for detector in (
            "verify_workflow_triggers",
            "verify_inventory_architecture_gate",
            "verify_architecture_coverage",
            "verify_public_wording",
            "verify_inventory_documentation",
        ):
            with self.subTest(detector=detector):
                self.assertTrue(callable(getattr(verifier, detector)))
                self.assertIn(f"{detector}(", source)

    def test_the_inventory_gate_reads_the_canonical_architecture_set(self) -> None:
        verifier = load_repository_verifier()
        source = inspect.getsource(verifier.main)

        # Passing a literal set here would let the gate keep
        # accepting amd64 after the canonical set changed.
        self.assertIn("verifier.CANONICAL_INVENTORY_ARCHITECTURES", source)


class LaunchSurfaceTest(unittest.TestCase):
    def test_readme_has_an_icon_and_every_workflow_badge(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        icon = ROOT / "assets" / "icon.svg"

        self.assertIn('src="assets/icon.svg"', readme)
        self.assertIn('alt="lint icon"', readme)
        self.assertTrue(icon.is_file())
        root = ET.parse(icon).getroot()
        self.assertEqual("512", root.attrib.get("width"))
        self.assertEqual("512", root.attrib.get("height"))

        workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
        self.assertEqual(3, len(workflows))
        for workflow in workflows:
            with self.subTest(workflow=workflow.name):
                action_url = (
                    "https://github.com/trycopilotai/lint/"
                    f"actions/workflows/{workflow.name}"
                )
                badge_and_link = f"({action_url}/badge.svg)]({action_url})"
                self.assertIn(badge_and_link, readme)

    def test_top_level_verifier_runs_schema_two_demo_verifier(self) -> None:
        verifier = load_repository_verifier()
        schema_two_verifier = mock.Mock()
        manifest = {"schema_version": 2}
        schema_two_verifier.load_manifest.return_value = manifest

        with mock.patch.object(
            verifier,
            "load_demo_evidence_verifier",
            return_value=schema_two_verifier,
        ):
            verifier.verify_demo()

        schema_two_verifier.load_manifest.assert_called_once_with()
        schema_two_verifier.verify_manifest.assert_called_once_with(manifest)

    def test_top_level_verifier_rejects_demo_verifier_failure(self) -> None:
        verifier = load_repository_verifier()
        schema_two_verifier = mock.Mock()
        schema_two_verifier.load_manifest.return_value = {"schema_version": 1}
        schema_two_verifier.verify_manifest.side_effect = ValueError(
            "demo manifest schema_version must be 2"
        )

        with mock.patch.object(
            verifier,
            "load_demo_evidence_verifier",
            return_value=schema_two_verifier,
        ), self.assertRaisesRegex(
            ValueError,
            "schema_version must be 2",
        ):
            verifier.verify_demo()

    def test_verification_does_not_require_a_git_checkout(self) -> None:
        verifier = (ROOT / "tools" / "verify_repo.py").read_text(encoding="utf-8")

        self.assertIn("def walked_files", verifier)
        self.assertIn('"rev-parse", "--show-toplevel"', verifier)
        self.assertIn("repository_root != ROOT.resolve()", verifier)
        self.assertIn("check=False", verifier)
        self.assertIn("return walked_files()", verifier)

    def test_archive_inside_another_repository_uses_its_own_tree(self) -> None:
        verifier = load_repository_verifier()
        with tempfile.TemporaryDirectory() as directory:
            outer = Path(directory) / "outer"
            archive = outer / "archive"
            archive.mkdir(parents=True)
            subprocess.run(
                ["git", "init", "-q", "-b", "main", str(outer)],
                check=True,
            )
            expected = archive / "README.md"
            expected.write_text("archive\n", encoding="utf-8")
            with mock.patch.object(verifier, "ROOT", archive):
                self.assertEqual([expected], verifier.tracked_files())

    def test_archive_name_scan_includes_directories_and_dangling_links(self) -> None:
        verifier = load_repository_verifier()
        reserved = "." + "g" + "p" + "t"
        for kind in ("directory", "dangling-link"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / f"reserved{reserved}-{kind}"
                if kind == "directory":
                    path.mkdir()
                else:
                    os.symlink("missing", path)
                with mock.patch.object(
                    verifier,
                    "ROOT",
                    root,
                ), self.assertRaisesRegex(
                    ValueError,
                    "reserved filename infix",
                ):
                    verifier.verify_normal_names(verifier.tracked_files())

    def test_one_codec_path_set_covers_every_workflow(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("CODEC_PATHS", makefile)

        # The set used to be spelled out per workflow, and the
        # copies drifted: the issue forms and label definitions
        # a contributor reads were checked by no automation.
        for name in ("ci.yml", "release.yml"):
            with self.subTest(workflow=name):
                workflow = (ROOT / ".github" / "workflows" / name).read_text(
                    encoding="utf-8"
                )
                self.assertIn("run: make codec", workflow)
                self.assertNotIn("prettier@3.7.4 --check", workflow)

        for path in (
            ".github/ISSUE_TEMPLATE",
            ".github/labels.yml",
            "evidence/comparison-sources.json",
            "images/THIRD_PARTY_NOTICES.md",
        ):
            with self.subTest(path=path):
                self.assertIn(path, makefile)

        # The Python formatter set was the second copy left
        # duplicated, in exactly the condition the codec sets
        # were in before they drifted.
        self.assertIn("BLACK_PATHS", makefile)
        for name in ("ci.yml", "release.yml"):
            with self.subTest(workflow=name):
                workflow = (ROOT / ".github" / "workflows" / name).read_text(
                    encoding="utf-8"
                )
                self.assertIn("run: make pyformat", workflow)
                self.assertNotIn("black --check", workflow)

    def test_release_compresses_the_archive_reproducibly(self) -> None:
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )

        # The published digest is the compressed archive's, so a
        # bare `gzip` here would publish a digest no recipient
        # can reproduce from the signed tag.
        self.assertIn("tools/compress_archive.py", release)
        self.assertNotIn("gzip -n", release)
        self.assertNotIn("gzip -9", release)

        compressor = ROOT / "tools" / "compress_archive.py"
        self.assertTrue(compressor.is_file())
        source = compressor.read_text(encoding="utf-8")
        self.assertIn("mtime=0", source)
        self.assertIn('filename=""', source)

    def test_codex_short_descriptions_agree_across_surfaces(self) -> None:
        manifest = json.loads(
            (ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        interface = manifest["interface"]
        agent_metadata = (
            ROOT / "skills" / "lint" / "agents" / "openai.yaml"
        ).read_text(encoding="utf-8")
        action = (ROOT / "action.yml").read_text(encoding="utf-8")

        # A Codex reader meets this sentence three times: in the
        # marketplace entry, in the installed skill's own agent
        # metadata, and in the composite action. Three copies
        # drift, and the marketplace validator in
        # trycopilotai/skills rejects the pair that disagrees.
        short = interface["shortDescription"]
        self.assertIn(f'"{short}"', agent_metadata)
        self.assertIn(f'"{short}"', action)

        display = interface["displayName"]
        self.assertIn(f'display_name: "{display}"', agent_metadata)

    def test_public_skill_installs_do_not_require_authentication(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        release_base = "https://github.com/trycopilotai/lint/releases/download/$release"

        self.assertEqual(2, readme.count(release_base))
        self.assertEqual(2, readme.count("release=v0.1.5"))
        self.assertEqual(2, readme.count('version="${release#v}"'))
        # The auto-generated tag tarball is the one artifact
        # no checksum is published for, so both installs take
        # the release asset and verify it.
        self.assertEqual(2, readme.count("shasum -a 256 -c"))
        self.assertNotIn("archive/refs/tags", readme)
        self.assertNotIn("gh api repos/trycopilotai/lint/tarball", readme)
        for invocation in ("`/lint`", "`/lint:lint`", "`$lint`", "`@lint`"):
            with self.subTest(invocation=invocation):
                self.assertIn(invocation, readme)

    def test_issue_forms_and_domain_labels_are_declared(self) -> None:
        expected = [
            ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml",
            ROOT / ".github" / "ISSUE_TEMPLATE" / "formatter.yml",
            ROOT / ".github" / "ISSUE_TEMPLATE" / "proposal.yml",
            ROOT / ".github" / "ISSUE_TEMPLATE" / "supply-chain.yml",
            ROOT / ".github" / "labels.yml",
        ]
        for path in expected:
            with self.subTest(path=path):
                self.assertTrue(path.is_file())

        # Blank issues are off, so the forms are the entire
        # contributor path. Both report forms demand a
        # reproduction, which a proposal or a question does not
        # have; without a third form the starter issues
        # CONTRIBUTING points at cannot be filed by an outsider.
        proposal = (ROOT / ".github" / "ISSUE_TEMPLATE" / "proposal.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("Proposal or question", proposal)
        self.assertNotIn("required: true", proposal.split("id: scope")[1])

        labels = (ROOT / ".github" / "labels.yml").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for label in ("formatter", "supply-chain", "good first issue"):
            with self.subTest(label=label):
                self.assertIn(f"name: {label}", labels)
                self.assertIn(label, readme)
        self.assertIn("Issues use the `formatter`", readme)
        private_launch_phrase = " ".join(("Private", "prelaunch"))
        self.assertNotIn(private_launch_phrase, readme)

    def test_recipient_surface_qualifies_unpublished_integrations(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        normalized = " ".join(readme.split())

        # The repository is public, so nothing may be deferred to
        # a launch that has already happened. What is still
        # unavailable is the tag, the release, and the image
        # package, and each install block says so.
        self.assertIn(
            "Once the matching `v0.1.5` tag is published, use the composite",
            normalized,
        )
        self.assertEqual(
            2,
            normalized.count("Once the matching `v0.1.5` release is published"),
        )
        self.assertIn(
            "which resolves only once the matching image package is published",
            normalized,
        )
        self.assertIn("prefers-reduced-motion", readme)
        self.assertNotIn("static reduced-motion poster", readme)

    def test_readme_distinguishes_discovered_and_explicit_links(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        normalized = " ".join(readme.split())

        self.assertIn("Discovered symbolic links", normalized)
        self.assertIn("excluded from `--all`", normalized)
        self.assertIn("without being followed", normalized)
        self.assertIn("named explicitly", normalized)
        self.assertIn("through `--files-from0` is rejected", normalized)

    def test_version_tags_are_not_described_as_pinned_images(self) -> None:
        skill = (ROOT / "skills" / "lint" / "SKILL.md").read_text(encoding="utf-8")
        action = (ROOT / "action.yml").read_text(encoding="utf-8")
        codex = (ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        openai = (ROOT / "skills" / "lint" / "agents" / "openai.yaml").read_text(
            encoding="utf-8"
        )

        self.assertIn("optional versioned language-specific containers", skill)
        for surface in (action, codex, openai):
            self.assertIn("canonical mixed-language formatting", surface)
            self.assertNotIn("pinned mixed-language formatting", surface)

    def test_comparison_claims_are_bound_to_source_captures(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        record = json.loads(
            (ROOT / "evidence" / "comparison-sources.json").read_text(encoding="utf-8")
        )

        self.assertIn("is a verbatim quote", readme)
        self.assertEqual(2, record["schema_version"])
        self.assertIn(record["checked_at"], readme)

        # Every cell about another project needs its own
        # capture. Sourcing only the breadth column left the
        # other two as unsourced assertions about other people's
        # work, under a preamble that claimed otherwise.
        columns = ("breadth", "role", "delivery")
        expected = {
            "MegaLinter": "https://megalinter.io/latest/",
            "Trunk Code Quality": (
                "https://marketplace.visualstudio.com/items?itemName=trunk.io"
            ),
            "pre-commit": "https://github.com/pre-commit/pre-commit",
        }
        sources = {
            (item["project"], item["column"]): item for item in record["sources"]
        }
        self.assertEqual(
            {(project, column) for project in expected for column in columns},
            set(sources),
        )
        for project, row_url in expected.items():
            rows = [line for line in readme.splitlines() if f"[{project}](" in line]
            self.assertEqual(1, len(rows))
            cells = [cell.strip() for cell in rows[0].strip("|").split("|")]
            self.assertEqual(4, len(cells))
            project_link = re.fullmatch(r"\[([^]]+)\]\((https://[^)]+)\)", cells[0])
            self.assertIsNotNone(project_link)
            assert project_link is not None
            self.assertEqual(project, project_link.group(1))
            self.assertEqual(row_url, project_link.group(2))
            for index, column in enumerate(columns, start=1):
                with self.subTest(project=project, column=column):
                    source = sources[(project, column)]
                    claim_link = re.fullmatch(
                        r"\[([^]]+)\]\((https://[^)]+)\)",
                        cells[index],
                    )
                    self.assertIsNotNone(claim_link)
                    assert claim_link is not None
                    self.assertEqual(
                        source["readme_claim"],
                        claim_link.group(1),
                    )
                    self.assertEqual(source["source_url"], claim_link.group(2))

        for (project, column), source in sorted(sources.items()):
            with self.subTest(project=project, column=column):
                capture = source["capture"]
                context_path = ROOT / capture["context_path"]
                self.assertTrue(context_path.is_file())
                self.assertFalse(context_path.is_symlink())
                self.assertTrue(context_path.is_relative_to(ROOT / "evidence"))
                context_bytes = context_path.read_bytes()
                context = context_bytes.decode("utf-8").removesuffix("\n")
                self.assertEqual(
                    hashlib.sha256(context_bytes).hexdigest(),
                    capture["context_sha256"],
                )

                # No response hash is recorded, deliberately.
                # Several of these pages carry a fixed-width
                # per-response token, so the digest changes
                # between two fetches of identical length and
                # can never stabilise.
                self.assertNotIn("body_sha256", capture)

                # The retained file must show the quote in its
                # surrounding text, not echo the search string
                # back with no context around it.
                self.assertIn(source["readme_claim"], context)
                self.assertGreater(len(context), len(source["readme_claim"]))
                self.assertGreater(capture["body_bytes"], len(context_bytes))
                self.assertEqual(200, capture["http_status"])
                self.assertTrue(
                    capture["retrieved_at"].startswith(record["checked_at"])
                )

                # A quote may come from the project's docs site
                # rather than the page the table links to, but it
                # must still be that project's own domain.
                self.assertTrue(source["source_url"].startswith("https://"))
                self.assertEqual(
                    capture["final_url"].startswith(source["source_url"]),
                    True,
                )

    def test_the_readme_breadth_number_matches_the_matrix(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        matrix = json.loads((ROOT / "images" / "matrix.json").read_text())
        languages = {
            language for entry in matrix["images"] for language in entry["languages"]
        }

        # The comparison table's own breadth cell is the one
        # number in it this project asserts about itself, and it
        # was checked against nothing.
        self.assertIn(f"{len(languages)} language entries", readme)

    def test_non_star_success_metric_is_present(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("Repository stars are not the success metric", readme)

    def test_http_api_documentation_matches_the_content_contract(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        normalized = " ".join(readme.split())

        self.assertIn('policy_id: "default"', readme)
        self.assertIn("`check` and `fix`", readme)
        self.assertIn("`read-only` and `write`", readme)
        self.assertIn(
            "authentication is outside the bundled development service",
            normalized.lower(),
        )

    def test_release_builds_the_image_set_it_advertises(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        lint_source = (ROOT / "lint.py").read_text(encoding="utf-8")
        matrix = json.loads((ROOT / "images" / "matrix.json").read_text())
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        version = matrix["version"]
        normalized_readme = " ".join(readme.split())

        # publish_images is true only when the tag version equals
        # the matrix version, so these three have to move
        # together. If they drift, the README promises an image
        # build that the tag skips, and --docker pulls a tag that
        # was never published.
        self.assertEqual("0.1.5", version)
        self.assertIn(
            'return f"{IMAGE_PREFIX}{language.id}:{image_version()}"',
            lint_source,
        )
        self.assertIn(f"lint-<language>:{version}", readme)
        self.assertIn(
            f"A matching v{version} tag builds and publishes",
            normalized_readme,
        )
        self.assertIn(
            "the image set for Linux AMD64 and Linux ARM64",
            normalized_readme,
        )

        # Software bills of materials are attached to images in
        # the registry; they have never been release assets, and
        # the README must not imply a reader can download one
        # from the release page.
        self.assertIn(
            "registry attachments on the images, not release assets",
            " ".join(readme.split()),
        )

        # A tag that does not match the matrix has no image set
        # to point at, so the release must stop rather than
        # publish a manifest with no images behind it.
        self.assertIn("publish_images", release)
        self.assertNotIn("images/inherited-release.json", release)


if __name__ == "__main__":
    unittest.main()
