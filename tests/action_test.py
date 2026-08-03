from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None:
        raise RuntimeError(f"could not create {name} specification")
    if specification.loader is None:
        raise RuntimeError(f"{name} specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


ACTION = load_module("action_under_test", ROOT / "action_entrypoint.py")
RUNNER = load_module(
    "skill_runner_under_test",
    ROOT / "skills" / "lint" / "run.py",
)


class ActionTest(unittest.TestCase):
    def test_ci_contract_traverses_the_repository_root(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        contract = workflow.split("- name: Verify read-only default", 1)[1].split(
            "- name: Check Python formatting",
            1,
        )[0]

        self.assertIn("--cwd .", contract)
        self.assertIn("--language requirements", contract)
        self.assertIn('summary["skipped"]', contract)

    def test_default_is_read_only_all_docker(self) -> None:
        environment = {"GITHUB_ACTION_PATH": str(ROOT)}
        with mock.patch.dict(os.environ, environment, clear=True):
            command = ACTION.command()

        self.assertIn("--read-only", command)
        self.assertIn("--all", command)
        self.assertNotIn("--write", command)
        self.assertIn("--docker", command)

        action = (ROOT / "action.yml").read_text(encoding="utf-8")
        docker_input = action.split("  docker:", 1)[1].split(
            "  languages:",
            1,
        )[0]
        self.assertIn('default: "true"', docker_input)

    def test_modified_scope_does_not_add_all(self) -> None:
        environment = {
            "GITHUB_ACTION_PATH": str(ROOT),
            "LINT_INPUT_MODIFIED": "true",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            command = ACTION.command()

        self.assertIn("--modified", command)
        self.assertNotIn("--all", command)

    def test_contradictory_selection_inputs_are_rejected(self) -> None:
        combinations = (
            {
                "LINT_INPUT_PATHS": "one.py",
                "LINT_INPUT_FILES_FROM0": "paths.bin",
            },
            {
                "LINT_INPUT_PATHS": "one.py",
                "LINT_INPUT_MODIFIED": "true",
            },
            {
                "LINT_INPUT_FILES_FROM0": "paths.bin",
                "LINT_INPUT_MODIFIED": "true",
            },
        )
        for inputs in combinations:
            environment = {
                "GITHUB_ACTION_PATH": str(ROOT),
                **inputs,
            }
            with self.subTest(inputs=inputs), mock.patch.dict(
                os.environ,
                environment,
                clear=True,
            ), self.assertRaisesRegex(ValueError, "selection input"):
                ACTION.command()

    def test_write_docker_languages_are_typed(self) -> None:
        environment = {
            "GITHUB_ACTION_PATH": str(ROOT),
            "LINT_INPUT_MODE": "write",
            "LINT_INPUT_DOCKER": "true",
            "LINT_INPUT_LANGUAGES": "typescript,tsx",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            command = ACTION.command()

        self.assertIn("--write", command)
        self.assertIn("--docker", command)
        self.assertEqual(2, command.count("--language"))

    def test_invalid_boolean_is_rejected(self) -> None:
        environment = {
            "GITHUB_ACTION_PATH": str(ROOT),
            "LINT_INPUT_DOCKER": "sometimes",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(ValueError):
                ACTION.command()

    def test_plugin_skill_runner_finds_engine(self) -> None:
        self.assertEqual(ROOT / "lint.py", RUNNER.engine_path())

    def test_standalone_skill_layout_launches_engine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "lint"
            target.mkdir()
            shutil.copy2(ROOT / "lint.py", target / "lint.py")
            shutil.copy2(ROOT / "languages.json", target / "languages.json")
            shutil.copy2(ROOT / "skills" / "lint" / "run.py", target / "run.py")

            completed = subprocess.run(
                [sys.executable, str(target / "run.py"), "--help"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("--read-only", completed.stdout)


if __name__ == "__main__":
    unittest.main()
