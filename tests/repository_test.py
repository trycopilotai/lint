from __future__ import annotations

import importlib.util
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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


RELEASE_MATRIX = load_release_matrix()
IMAGE_VERIFIER = load_image_verifier()


class ReleaseMatrixTest(unittest.TestCase):
    def test_every_language_has_one_release_row(self) -> None:
        rows = RELEASE_MATRIX.release_rows()
        languages = [row["language"] for row in rows]

        self.assertEqual(26, len(rows))
        self.assertEqual(26, len(set(languages)))

    def test_evidenced_runtime_budgets_are_preserved(self) -> None:
        rows = RELEASE_MATRIX.release_rows()
        budgets = {row["language"]: row["budget_mib"] for row in rows}

        self.assertEqual(90, budgets["rust"])
        self.assertEqual(140, budgets["kotlin"])
        self.assertEqual(360, budgets["julia"])


class ImageContentsTest(unittest.TestCase):
    def test_prettier_image_ignores_repository_configuration(self) -> None:
        dockerfile = (ROOT / "images" / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("--no-config", dockerfile)
        self.assertIn("--no-editorconfig", dockerfile)

    def test_shells_package_managers_and_compilers_are_forbidden(self) -> None:
        paths = [
            "bin/ash",
            "usr/bin/dpkg",
            "usr/bin/cc",
            "usr/local/go/bin/go",
        ]

        for path in paths:
            with self.subTest(path=path):
                self.assertTrue(IMAGE_VERIFIER.forbidden_executable(path))

    def test_formatter_executables_are_accepted(self) -> None:
        paths = [
            "gofmt",
            "usr/lib/llvm18/bin/clang-format",
            "usr/bin/swift-format",
        ]

        for path in paths:
            with self.subTest(path=path):
                self.assertFalse(IMAGE_VERIFIER.forbidden_executable(path))

    def test_go_formatters_are_built_with_the_pinned_toolchain(self) -> None:
        dockerfile = (ROOT / "images" / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("FROM ${GO_IMAGE} AS go-tools-build", dockerfile)
        self.assertIn("./buildifier", dockerfile)
        self.assertIn("./cmd/shfmt", dockerfile)

    def test_julia_image_removes_non_runtime_manifests(self) -> None:
        dockerfile = (ROOT / "images" / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("/usr/local/julia/share/julia", dockerfile)
        self.assertIn("/usr/local/julia/share/doc", dockerfile)
        self.assertIn("/opt/julia-depot/packages", dockerfile)
        self.assertIn(r"-type d \( -name docs -o -name test \)", dockerfile)

    def test_black_scanner_exception_is_scoped_and_expires(self) -> None:
        ignore = (ROOT / ".trivyignore.yaml").read_text(encoding="utf-8")

        self.assertIn("CVE-2026-32274", ignore)
        self.assertIn("pkg:pypi/black@24.10.0", ignore)
        self.assertIn("expired_at: 2026-10-31", ignore)


class ReleaseWorkflowTest(unittest.TestCase):
    def test_attestations_wait_for_public_visibility(self) -> None:
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        publishing = (ROOT / "docs" / "publishing.md").read_text(encoding="utf-8")

        self.assertEqual(
            2,
            release.count("if: steps.visibility.outputs.value == 'public'"),
        )
        self.assertGreaterEqual(
            release.count('gh api "repos/$GITHUB_REPOSITORY"'),
            2,
        )
        self.assertIn("Re-run all jobs", publishing)
        self.assertIn("GitHub Free", publishing)


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

    def test_public_skill_installs_do_not_require_authentication(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        archive_url = (
            "https://github.com/trycopilotai/lint/" "archive/refs/tags/v0.1.4.tar.gz"
        )

        self.assertEqual(2, readme.count(archive_url))
        self.assertNotIn("gh api repos/trycopilotai/lint/tarball", readme)

    def test_public_transition_recreates_the_private_stage(self) -> None:
        publishing = (ROOT / "docs" / "publishing.md").read_text(encoding="utf-8")

        self.assertIn("Never make\nthis repository object public directly", publishing)
        self.assertIn("delete every Actions workflow run", publishing)
        self.assertIn("Push only `refs/heads/main` and", publishing)
        self.assertIn("`refs/tags/v0.1.4`", publishing)

    def test_issue_forms_and_domain_labels_are_declared(self) -> None:
        expected = [
            ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml",
            ROOT / ".github" / "ISSUE_TEMPLATE" / "formatter.yml",
            ROOT / ".github" / "ISSUE_TEMPLATE" / "supply-chain.yml",
            ROOT / ".github" / "labels.yml",
        ]
        for path in expected:
            with self.subTest(path=path):
                self.assertTrue(path.is_file())

        labels = (ROOT / ".github" / "labels.yml").read_text(encoding="utf-8")
        for label in ("formatter", "supply-chain", "good first issue"):
            with self.subTest(label=label):
                self.assertIn(f"name: {label}", labels)


if __name__ == "__main__":
    unittest.main()
