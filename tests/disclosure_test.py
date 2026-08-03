from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCANNER_PATH = ROOT / "tools" / "disclosure_scan.py"


def load_scanner():
    specification = importlib.util.spec_from_file_location(
        "disclosure_scanner_under_test",
        SCANNER_PATH,
    )
    if specification is None:
        raise RuntimeError("could not create disclosure scanner specification")
    if specification.loader is None:
        raise RuntimeError("disclosure scanner specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


SCANNER = load_scanner()


class DisclosureScannerTest(unittest.TestCase):
    def test_tree_scan_reports_rules_without_repeating_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sensitive = "/" + "Users" + "/operator/private.txt"
            (root / "notes.txt").write_text(sensitive, encoding="utf-8")

            findings = SCANNER.scan_tree(root)

        self.assertEqual(1, len(findings))
        self.assertEqual("local_absolute_path", findings[0].rule)
        self.assertEqual("notes.txt", findings[0].location)
        self.assertNotIn(sensitive, findings[0].render())

    def test_tree_scan_checks_names_and_secret_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reserved = "." + "g" + "p" + "t"
            (root / f"private{reserved}.txt").write_text(
                "-----BEGIN " + "PRIVATE KEY-----\n",
                encoding="utf-8",
            )

            rules = {finding.rule for finding in SCANNER.scan_tree(root)}

        self.assertEqual({"private_key", "reserved_name"}, rules)

    def test_history_scan_checks_blob_names_and_commit_messages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
            subprocess.run(
                ["git", "-C", str(root), "config", "user.name", "Fixture"],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "config",
                    "user.email",
                    "fixture@example.invalid",
                ],
                check=True,
            )
            reserved = "." + "g" + "p" + "t"
            (root / f"retired{reserved}.txt").write_text("clean\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            local_path = "/" + "home" + "/operator/repo"
            subprocess.run(
                ["git", "-C", str(root), "commit", "-qm", local_path],
                check=True,
            )

            findings = SCANNER.scan_history(root)

        rules = {finding.rule for finding in findings}
        self.assertEqual(
            {"local_absolute_path", "reserved_name"},
            rules,
        )

    def test_current_repository_passes_tree_and_history_scans(self) -> None:
        self.assertEqual([], SCANNER.scan_tree(ROOT))
        self.assertEqual([], SCANNER.scan_history(ROOT))

    def test_verify_and_ci_run_disclosure_and_workflow_checks(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("tools/disclosure_scan.py --history", makefile)
        self.assertIn("make verify", workflow)
        self.assertIn(
            "github.com/rhysd/actionlint/cmd/actionlint@v1.7.12",
            workflow,
        )

    def test_portable_matrix_runs_the_complete_test_suite(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        portable = workflow.split("  portable:", 1)[1].split(
            "  local-integration:",
            1,
        )[0]

        self.assertIn("make test", portable)
        self.assertNotIn("python tests/cli_test.py", portable)

    def test_local_matrix_exercises_pinned_formatters_and_transactions(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        local = workflow.split("  local-integration:", 1)[1].split(
            "  repository:",
            1,
        )[0]

        for language in ("markdown", "bazel", "python", "requirements"):
            self.assertIn(f"language: {language}", local)
        self.assertIn("actions/setup-node@", local)
        self.assertIn('node-version: "24.18.0"', local)
        self.assertIn("black==24.10.0", local)
        self.assertIn("--language", local)
        self.assertIn("matrix.language", local)
        self.assertIn("requirements-a.txt", local)
        self.assertIn("z-bad.py", local)
        self.assertNotIn("requirements-z-bad.txt", local)


if __name__ == "__main__":
    unittest.main()
