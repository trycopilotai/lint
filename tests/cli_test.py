from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_lint():
    specification = importlib.util.spec_from_file_location(
        "lint_under_test",
        ROOT / "lint.py",
    )
    if specification is None:
        raise RuntimeError("could not create lint module specification")
    if specification.loader is None:
        raise RuntimeError("lint module specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


LINT = load_lint()


def initialize_repository(path: Path) -> None:
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(path)],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "set", "user.name", "Test"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "config",
            "set",
            "user.email",
            "test@example.invalid",
        ],
        check=True,
    )


class ParserTest(unittest.TestCase):
    def test_read_only_aliases_are_equivalent(self) -> None:
        for alias in ("--read-only", "--readonly", "-ro"):
            with self.subTest(alias=alias):
                arguments = LINT.parser().parse_args([alias])
                self.assertFalse(arguments.write)

    def test_write_aliases_are_equivalent(self) -> None:
        for alias in ("--write", "--apply", "-w"):
            with self.subTest(alias=alias):
                arguments = LINT.parser().parse_args([alias])
                self.assertTrue(arguments.write)

    def test_default_is_read_only_all_current_directory(self) -> None:
        arguments = LINT.parser().parse_args([])
        self.assertFalse(arguments.write)
        self.assertFalse(arguments.modified)
        self.assertFalse(arguments.json)
        self.assertEqual(".", arguments.cwd)
        self.assertEqual([], arguments.paths)


class SelectionTest(unittest.TestCase):
    def test_all_selects_tracked_and_nonignored_untracked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            initialize_repository(root)
            tracked = root / "tracked.py"
            untracked = root / "untracked.py"
            ignored = root / "ignored.py"
            tracked.write_text("x=1\n", encoding="utf-8")
            untracked.write_text("y=2\n", encoding="utf-8")
            ignored.write_text("z=3\n", encoding="utf-8")
            (root / ".gitignore").write_text(
                "ignored.py\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "-C", str(root), "add", "tracked.py", ".gitignore"],
                check=True,
            )

            selected = LINT.git_paths(root, modified=False)

            relative = {path.relative_to(root).as_posix() for path in selected}
            self.assertIn("tracked.py", relative)
            self.assertIn("untracked.py", relative)
            self.assertNotIn("ignored.py", relative)

    def test_modified_excludes_untracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            initialize_repository(root)
            tracked = root / "tracked.py"
            tracked.write_text("x = 1\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(root), "add", "tracked.py"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(root), "commit", "-qm", "fixture"],
                check=True,
            )
            tracked.write_text("x=1\n", encoding="utf-8")
            (root / "untracked.py").write_text("y=2\n", encoding="utf-8")

            selected = LINT.git_paths(root, modified=True)

            self.assertEqual([tracked], selected)

    def test_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            outside = root.parent / "outside.txt"
            outside.write_text("outside\n", encoding="utf-8")
            try:
                with self.assertRaises(LINT.SelectionError):
                    LINT.validate_explicit_path(root, "../outside.txt")
            finally:
                outside.unlink()

    def test_symbolic_link_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / "target.py"
            link = root / "link.py"
            target.write_text("x = 1\n", encoding="utf-8")
            os.symlink(target, link)
            with self.assertRaises(LINT.SelectionError):
                LINT.validate_explicit_path(root, "link.py")

    def test_symbolic_link_directory_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / "target"
            target.mkdir()
            (target / "file.py").write_text("x = 1\n", encoding="utf-8")
            os.symlink(target, root / "linked")
            with self.assertRaises(LINT.SelectionError):
                LINT.validate_explicit_path(root, "linked/file.py")


class FormattingTest(unittest.TestCase):
    def test_prettier_command_ignores_repository_configuration(self) -> None:
        language = LINT.Language(
            id="typescript",
            family="prettier",
            extensions=(".ts",),
            filenames=(),
        )
        command = LINT.command_for(language, Path("/work/example.ts"))

        self.assertIn("--no-config", command)
        self.assertIn("--no-editorconfig", command)
        self.assertNotIn("--plugin", command)

    def test_local_formatter_runs_in_external_mirror(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "project"
            root.mkdir()
            path = root / "nested" / "fixture.py"
            path.parent.mkdir()
            path.write_text("value = 1\n", encoding="utf-8")
            observed: dict[str, Path] = {}

            def record_formatter(
                language: LINT.Language,
                mirror_path: Path,
                formatter_cwd: Path,
                timeout_seconds: int,
            ) -> None:
                observed["path"] = mirror_path
                observed["cwd"] = formatter_cwd

            with mock.patch.object(
                LINT,
                "verify_formatter_version",
            ), mock.patch.object(
                LINT,
                "run_formatter",
                side_effect=record_formatter,
            ):
                LINT.prepare_results(
                    cwd=root,
                    paths=[path],
                    requested_languages=frozenset(),
                    use_docker=False,
                )

        mirror_path = observed["path"]
        formatter_cwd = observed["cwd"]
        self.assertEqual(
            Path("nested/fixture.py"),
            mirror_path.relative_to(formatter_cwd),
        )
        self.assertNotEqual(root, formatter_cwd)
        self.assertNotIn(root, formatter_cwd.parents)
        self.assertFalse(formatter_cwd.exists())

    def test_julia_path_is_passed_as_an_argument(self) -> None:
        language = LINT.Language(
            id="julia",
            family="julia",
            extensions=(".jl",),
            filenames=(),
        )
        path = Path('/work/a"); run(`touch injected`); #.jl')
        command = LINT.command_for(language, path)

        expression = command[command.index("-e") + 1]
        self.assertNotIn(str(path), expression)
        self.assertEqual(["--", str(path)], command[-2:])

    def test_kotlin_formatter_runs_two_convergence_passes(self) -> None:
        language = LINT.Language(
            id="kotlin",
            family="kotlin",
            extensions=(".kt",),
            filenames=(),
        )
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=b"",
            stderr=b"",
        )

        with mock.patch.object(
            LINT.subprocess,
            "run",
            return_value=completed,
        ) as run:
            LINT.run_formatter(
                language=language,
                path=Path("/work/fixture.kt"),
                cwd=Path("/work"),
                timeout_seconds=30,
            )

        self.assertEqual(2, run.call_count)

    def test_julia_version_check_requires_formatter_package(self) -> None:
        language = LINT.Language(
            id="julia",
            family="julia",
            extensions=(".jl",),
            filenames=(),
        )
        command, expected = LINT.version_command(language)
        versions = LINT.tool_versions()

        self.assertIn("JuliaFormatter", " ".join(command))
        self.assertIn(versions["julia"], expected)
        self.assertIn(versions["juliaformatter"], expected)

        completed = subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=f"{versions['julia']}\n0.0.0".encode("utf-8"),
            stderr=b"",
        )
        with mock.patch.object(
            LINT.subprocess,
            "run",
            return_value=completed,
        ), self.assertRaises(LINT.EngineError):
            LINT.verify_formatter_version(language)

    def test_read_only_reports_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / "requirements.txt"
            path.write_text("zeta==1\nalpha==1\n", encoding="utf-8")

            response = LINT.lint_files(
                cwd=root,
                paths=[path],
                requested_languages=frozenset(),
                write=False,
                use_docker=False,
            )

            self.assertEqual("needs_formatting", response["status"])
            self.assertEqual(
                "zeta==1\nalpha==1\n",
                path.read_text(encoding="utf-8"),
            )

    def test_write_applies_after_formatting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / "requirements.txt"
            path.write_text("zeta==1\nalpha==1\n", encoding="utf-8")

            response = LINT.lint_files(
                cwd=root,
                paths=[path],
                requested_languages=frozenset(),
                write=True,
                use_docker=False,
            )

            self.assertEqual("clean", response["status"])
            self.assertEqual(
                "alpha==1\nzeta==1\n",
                path.read_text(encoding="utf-8"),
            )

    def test_failed_write_rolls_back_prior_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_bytes(b"first\n")
            second.write_bytes(b"second\n")
            language = LINT.Language(
                id="requirements",
                family="requirements",
                extensions=(),
                filenames=(),
            )
            results = [
                LINT.FormatResult(
                    path=first,
                    relative_path="first.txt",
                    language=language,
                    original=b"first\n",
                    formatted=b"changed first\n",
                    mode=0o644,
                ),
                LINT.FormatResult(
                    path=second,
                    relative_path="second.txt",
                    language=language,
                    original=b"second\n",
                    formatted=b"changed second\n",
                    mode=0o644,
                ),
            ]
            real_atomic_write = LINT.atomic_write
            calls = 0

            def fail_second(path: Path, payload: bytes, mode: int) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("fixture failure")
                real_atomic_write(path, payload, mode)

            with mock.patch.object(LINT, "atomic_write", fail_second):
                with self.assertRaises(LINT.FormatterError):
                    LINT.apply_results(results)

            self.assertEqual(b"first\n", first.read_bytes())
            self.assertEqual(b"second\n", second.read_bytes())

    def test_docker_runner_has_isolation_flags(self) -> None:
        language = LINT.Language(
            id="python",
            family="black",
            extensions=(".py",),
            filenames=(),
        )
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=b"",
            stderr=b"",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with mock.patch.object(
                LINT.subprocess,
                "run",
                return_value=completed,
            ) as run:
                LINT.run_docker_formatter(
                    language,
                    root,
                    Path("fixture.py"),
                    30,
                )

        command = run.call_args.args[0]
        self.assertIn("none", command)
        self.assertIn("--read-only", command)
        self.assertIn("ALL", command)
        self.assertIn("no-new-privileges", command)


class MainTest(unittest.TestCase):
    def test_default_cli_is_human_readable_read_only_and_all(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_repository(root)
            path = root / "requirements.txt"
            path.write_text("zeta==1\nalpha==1\n", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = LINT.main(["--cwd", str(root)])

            self.assertEqual(LINT.EXIT_FORMATTING, exit_code)
            self.assertIn(
                "requirements.txt: needs formatting (requirements)",
                output.getvalue(),
            )
            self.assertIn(
                "needs formatting: 1 selected, 1 changed, 0 skipped",
                output.getvalue(),
            )
            self.assertEqual(
                "zeta==1\nalpha==1\n",
                path.read_text(encoding="utf-8"),
            )

    def test_json_cli_is_stable_read_only_and_all(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_repository(root)
            path = root / "requirements.txt"
            path.write_text("zeta==1\nalpha==1\n", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = LINT.main(["--json", "--cwd", str(root)])

            self.assertEqual(LINT.EXIT_FORMATTING, exit_code)
            response = json.loads(output.getvalue())
            self.assertEqual("read-only", response["mode"])
            self.assertEqual("needs_formatting", response["status"])
            self.assertEqual(
                "zeta==1\nalpha==1\n",
                path.read_text(encoding="utf-8"),
            )

    def test_make_modified_does_not_add_all(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            initialize_repository(root)
            for name in ("lint.py", "languages.json"):
                (root / name).write_bytes((ROOT / name).read_bytes())
            path = root / "requirements.txt"
            path.write_text("alpha==1\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(root), "add", "requirements.txt"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(root), "commit", "-qm", "fixture"],
                check=True,
            )
            path.write_text("# modified\nalpha==1\n", encoding="utf-8")

            completed = subprocess.run(
                [
                    "make",
                    "-C",
                    str(root),
                    "-f",
                    str(ROOT / "Makefile"),
                    "lint",
                    "ARGS=--modified --json",
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            self.assertEqual(
                0, completed.returncode, completed.stdout + completed.stderr
            )
            start = completed.stdout.index("{")
            response = json.loads(completed.stdout[start:])
            self.assertEqual("clean", response["status"])
            self.assertEqual(1, response["summary"]["selected"])


if __name__ == "__main__":
    unittest.main()
