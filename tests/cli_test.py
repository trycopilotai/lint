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


def load_dlint():
    specification = importlib.util.spec_from_file_location(
        "dlint_under_test",
        ROOT / "dlint.py",
    )
    if specification is None:
        raise RuntimeError("could not create dlint module specification")
    if specification.loader is None:
        raise RuntimeError("dlint module specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def load_requirements_entrypoint():
    specification = importlib.util.spec_from_file_location(
        "requirements_entrypoint_under_test",
        ROOT / "images" / "requirements_entrypoint.py",
    )
    if specification is None:
        raise RuntimeError("could not create requirements entrypoint specification")
    if specification.loader is None:
        raise RuntimeError("requirements entrypoint specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


LINT = load_lint()
DLINT = load_dlint()
REQUIREMENTS_ENTRYPOINT = load_requirements_entrypoint()


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

    def test_selection_modes_are_mutually_exclusive(self) -> None:
        combinations = (
            ("--all", "file.py"),
            ("--modified", "file.py"),
            ("--files-from0", "paths.bin", "file.py"),
            ("--all", "--files-from0", "paths.bin"),
            ("--modified", "--files-from0", "paths.bin"),
        )
        for arguments in combinations:
            error = io.StringIO()
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(error):
                exit_code = LINT.main(["--json", *arguments])
            self.assertEqual(LINT.EXIT_SELECTION, exit_code)
            response = json.loads(error.getvalue())
            self.assertEqual("selection_error", response["status"])

    def test_default_is_read_only_all_current_directory(self) -> None:
        arguments = LINT.parser().parse_args([])
        self.assertFalse(arguments.write)
        self.assertFalse(arguments.modified)
        self.assertFalse(arguments.json)
        self.assertEqual(".", arguments.cwd)
        self.assertEqual([], arguments.paths)

    def test_missing_cwd_is_a_selection_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                exit_code = LINT.main(["--json", "--cwd", str(missing)])

        self.assertEqual(LINT.EXIT_SELECTION, exit_code)
        response = json.loads(error.getvalue())
        self.assertEqual("selection_error", response["status"])
        self.assertIn("--cwd is not a directory", response["message"])

    def test_missing_files_from0_is_a_selection_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            missing = root / "missing.paths"
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                exit_code = LINT.main(
                    [
                        "--json",
                        "--cwd",
                        str(root),
                        "--files-from0",
                        str(missing),
                    ]
                )

        self.assertEqual(LINT.EXIT_SELECTION, exit_code)
        response = json.loads(error.getvalue())
        self.assertEqual("selection_error", response["status"])
        self.assertIn("--files-from0 cannot be read", response["message"])


class SelectionTest(unittest.TestCase):
    def test_all_selects_tracked_and_nonignored_untracked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            initialize_repository(root)
            tracked = root / "tracked.py"
            untracked = root / "untracked.py"
            ignored = root / "ignored.py"
            linked = root / "linked.py"
            tracked.write_text("x=1\n", encoding="utf-8")
            untracked.write_text("y=2\n", encoding="utf-8")
            ignored.write_text("z=3\n", encoding="utf-8")
            linked.symlink_to("tracked.py")
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
            self.assertNotIn("linked.py", relative)

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

    def test_modified_includes_staged_unstaged_and_rename_destinations(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            initialize_repository(root)
            for name in (
                "deleted.py",
                "rename-old.py",
                "staged.py",
                "unstaged.py",
            ):
                (root / name).write_text("value = 1\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(root), "add", "."],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(root), "commit", "-qm", "fixture"],
                check=True,
            )
            (root / "staged.py").write_text("value=2\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(root), "add", "staged.py"],
                check=True,
            )
            (root / "unstaged.py").write_text("value=2\n", encoding="utf-8")
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "mv",
                    "rename-old.py",
                    "rename-new.py",
                ],
                check=True,
            )
            (root / "deleted.py").unlink()
            (root / "untracked.py").write_text("value=2\n", encoding="utf-8")

            selected = LINT.git_paths(root, modified=True)

            relative = {path.relative_to(root).as_posix() for path in selected}
            self.assertEqual(
                {"rename-new.py", "staged.py", "unstaged.py"},
                relative,
            )

    def test_outside_git_uses_a_pruned_regular_file_walk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "kept.py").write_text("value=1\n", encoding="utf-8")
            cache = root / "node_modules"
            cache.mkdir()
            (cache / "ignored.js").write_text("value=1\n", encoding="utf-8")
            (root / "link.py").symlink_to("kept.py")

            selected = LINT.git_paths(root, modified=False)

            self.assertEqual([root / "kept.py"], selected)
            with self.assertRaisesRegex(
                LINT.SelectionError,
                "--modified requires a Git working tree",
            ):
                LINT.git_paths(root, modified=True)

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

    def test_explicit_symbolic_link_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / "target.py"
            link = root / "link.py"
            target.write_text("x = 1\n", encoding="utf-8")
            os.symlink(target, link)
            with self.assertRaisesRegex(
                LINT.SelectionError,
                "symbolic links are not accepted",
            ):
                LINT.select_paths(
                    cwd=root,
                    explicit_paths=["link.py"],
                    files_from0=None,
                    modified=False,
                )

    def test_explicit_symbolic_link_parent_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / "target"
            target.mkdir()
            (target / "file.py").write_text("x = 1\n", encoding="utf-8")
            os.symlink(target, root / "linked")
            with self.assertRaisesRegex(
                LINT.SelectionError,
                "symbolic links are not accepted",
            ):
                LINT.select_paths(
                    cwd=root,
                    explicit_paths=["linked/file.py"],
                    files_from0=None,
                    modified=False,
                )

    def test_git_selection_excludes_a_symlinked_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "project"
            outside = Path(directory).resolve() / "outside"
            root.mkdir()
            outside.mkdir()
            initialize_repository(root)
            tracked_directory = root / "tracked"
            tracked_directory.mkdir()
            tracked = tracked_directory / "file.py"
            tracked.write_text("inside = True\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(root), "add", "tracked/file.py"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(root), "commit", "-qm", "fixture"],
                check=True,
            )
            tracked.unlink()
            tracked_directory.rmdir()
            (outside / "file.py").write_text(
                "outside = True\n",
                encoding="utf-8",
            )
            os.symlink(outside, tracked_directory)

            selected = LINT.git_paths(root, modified=False)

            self.assertEqual([], selected)
            self.assertEqual(
                "outside = True\n",
                (outside / "file.py").read_text(encoding="utf-8"),
            )

    def test_selection_rejects_a_symlinked_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "project"
            outside = Path(directory).resolve() / "outside"
            root.mkdir()
            outside.mkdir()
            selected_directory = root / "selected"
            selected_directory.mkdir()
            selected = selected_directory / "requirements.txt"
            selected.write_text("alpha==1\n", encoding="utf-8")
            selected.unlink()
            selected_directory.rmdir()
            (outside / "requirements.txt").write_text(
                "outside==1\n",
                encoding="utf-8",
            )
            os.symlink(outside, selected_directory)

            with self.assertRaisesRegex(
                LINT.SelectionError,
                "symbolic links are not accepted",
            ):
                LINT.prepare_results(
                    cwd=root,
                    paths=[selected],
                    requested_languages=frozenset(),
                    use_docker=False,
                )
            self.assertEqual(
                "outside==1\n",
                (outside / "requirements.txt").read_text(encoding="utf-8"),
            )


class FormattingTest(unittest.TestCase):
    def test_prettier_command_allows_data_configuration(self) -> None:
        language = LINT.Language(
            id="typescript",
            family="prettier",
            extensions=(".ts",),
            filenames=(),
        )
        command = LINT.command_for(language, Path("/work/example.ts"))

        self.assertNotIn("--no-config", command)
        self.assertNotIn("--no-editorconfig", command)
        self.assertIn("--ignore-path", command)
        self.assertIn("--print-width", command)
        self.assertIn("60", command)
        self.assertNotIn("--plugin", command)

    def test_black_command_allows_data_configuration(self) -> None:
        language = LINT.Language(
            id="python",
            family="black",
            extensions=(".py",),
            filenames=(),
        )
        command = LINT.command_for(language, Path("/work/example.py"))

        self.assertNotIn("--config", command)
        self.assertNotIn(os.devnull, command)
        self.assertIn("--line-length", command)
        self.assertIn("88", command)

    def test_local_formatter_runs_in_external_mirror(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "project"
            root.mkdir()
            path = root / "nested" / "fixture.py"
            path.parent.mkdir()
            path.write_text("value = 1\n", encoding="utf-8")
            path.chmod(0o600)
            observed: dict[str, Path | int] = {}

            def record_formatter(
                language: LINT.Language,
                mirror_path: Path,
                formatter_cwd: Path,
                timeout_seconds: int,
            ) -> None:
                observed["path"] = mirror_path
                observed["cwd"] = formatter_cwd
                observed["root_mode"] = formatter_cwd.stat().st_mode & 0o777
                observed["file_mode"] = mirror_path.stat().st_mode & 0o777

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
        # Windows has no POSIX permission bits. CPython synthesizes
        # st_mode there from the read-only attribute alone, so every
        # directory reports 0o777 and every writable file reports
        # 0o666 no matter what mkdtemp and chmod requested. Assert the
        # modes the running platform can actually report; every other
        # assertion in this test is platform independent.
        expected_root_mode = 0o700
        expected_file_mode = 0o600
        if os.name == "nt":
            expected_root_mode = 0o777
            expected_file_mode = 0o666
        self.assertEqual(expected_root_mode, observed["root_mode"])
        self.assertEqual(expected_file_mode, observed["file_mode"])

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

    def test_requirements_formatter_obeys_the_file_timeout(self) -> None:
        language = LINT.Language(
            id="requirements",
            family="requirements",
            extensions=(),
            filenames=("requirements.txt",),
        )

        with mock.patch.object(
            LINT.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("requirements", 30),
        ), self.assertRaisesRegex(LINT.FormatterError, "exceeded 30 seconds"):
            LINT.run_formatter(
                language=language,
                path=Path("/work/requirements.txt"),
                cwd=Path("/work"),
                timeout_seconds=30,
            )

    def test_julia_version_check_requires_formatter_package(self) -> None:
        language = LINT.Language(
            id="julia",
            family="julia",
            extensions=(".jl",),
            filenames=(),
        )
        probe = LINT.version_command(language)
        self.assertIsNotNone(probe)
        assert probe is not None
        versions = LINT.tool_versions()

        self.assertIn("JuliaFormatter", " ".join(probe.command))
        self.assertIn(versions["julia"], probe.expected)
        self.assertIn(versions["juliaformatter"], probe.expected)

        completed = subprocess.CompletedProcess(
            args=probe.command,
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

    def test_manifest_pins_drive_formatter_version_checks(self) -> None:
        versions = LINT.tool_versions()
        cases = (
            (
                "c",
                "clang",
                ".c",
                ("clang-format", "--version"),
                (versions["clang-format"],),
            ),
            (
                "xml",
                "xml",
                ".xml",
                ("xmllint", "--version"),
                ("21503",),
            ),
            (
                "go",
                "go",
                ".go",
                ("go", "version", "/tools/gofmt"),
                (versions["go"],),
            ),
            (
                "rust",
                "rust",
                ".rs",
                ("rustup", "run", versions["rust"], "rustfmt", "--version"),
                (versions["rustfmt"],),
            ),
        )
        for language_id, family, extension, expected_command, expected_pin in cases:
            with self.subTest(language=language_id):
                language = LINT.Language(
                    id=language_id,
                    family=family,
                    extensions=(extension,),
                    filenames=(),
                )
                with mock.patch.object(
                    LINT.shutil,
                    "which",
                    return_value="/tools/gofmt",
                ):
                    request = LINT.version_command(language)
                self.assertIsNotNone(request)
                self.assertEqual(expected_command, request.command)
                self.assertEqual(expected_pin, request.expected)

    def test_path_version_grammars_require_exact_pins(self) -> None:
        valid_outputs = {
            "black": ("black, 24.10.0 (compiled: yes)\n" "Python (CPython) 3.13.14"),
            "shfmt": "v3.13.1",
            "clang": "Homebrew clang-format version 18.1.8",
            "java": "google-java-format: Version 1.35.0",
            "go": "/tools/gofmt: go1.26.5",
            "rust": "rustfmt 1.9.0-stable (0123456789 2026-07-30)",
            "kotlin": "ktlint version 1.3.0",
            "toml": "taplo 0.10.0",
            "xml": (
                "/tools/xmllint: using libxml version 21503\n"
                "   compiled with: Threads Tree Reader "
            ),
            "swift": "603.0.0",
            "csharp": "1.3.0",
            "julia": "1.12.6\n2.12.3",
        }
        languages_by_family = {}
        for language in LINT.load_languages():
            languages_by_family.setdefault(language.family, language)

        self.assertEqual(
            set(valid_outputs),
            set(languages_by_family)
            - {
                "prettier",
                "buildifier",
                "requirements",
            },
        )
        for family, valid_output in valid_outputs.items():
            language = languages_by_family[family]
            with mock.patch.object(
                LINT.shutil,
                "which",
                return_value="/tools/gofmt",
            ):
                probe = LINT.version_command(language)
            self.assertIsNotNone(probe)
            assert probe is not None
            token = probe.expected[0]
            invalid_outputs = (
                valid_output.replace(token, "0.0.0", 1),
                f"unexpected {valid_output}",
                f"{valid_output} unexpected",
                valid_output.replace(token, f"{token}.dev999", 1),
                f" {valid_output}",
                f"{valid_output} ",
            )
            with self.subTest(family=family, output="valid"):
                self.assertTrue(LINT.version_output_matches(probe, valid_output))
            for invalid_output in invalid_outputs:
                with self.subTest(family=family, output=invalid_output):
                    self.assertFalse(LINT.version_output_matches(probe, invalid_output))

    def test_xmllint_numeric_version_is_compared_exactly(self) -> None:
        language = LINT.Language(
            id="xml",
            family="xml",
            extensions=(".xml",),
            filenames=(),
        )
        probe = LINT.version_command(language)
        banner = (
            "/opt/libxml2/bin/xmllint: using libxml version 21503\n"
            "   compiled with: Threads Tree Output Push Reader "
            "Patterns Writer SAXv1 DTDValid HTML C14N Catalog XPath "
            "XPointer XInclude ISO8859X Regexps Automata RelaxNG "
            "Schemas Schematron Modules \n"
        )

        self.assertEqual("2.15.3", probe.description)
        self.assertEqual(("21503",), probe.expected)
        self.assertEqual("21503", LINT.libxml_numeric_version("2.15.3"))
        self.assertEqual("30000", LINT.libxml_numeric_version("3.0.0"))
        self.assertTrue(LINT.version_output_matches(probe, banner))

        mismatched_outputs = (
            banner.replace("21503", "21502", 1),
            banner.replace("21503", "21504", 1),
            banner.replace("21503", "215030", 1),
            banner.replace("21503", "2.15.3", 1),
        )
        for mismatched in mismatched_outputs:
            with self.subTest(output=mismatched.splitlines()[0]):
                self.assertFalse(LINT.version_output_matches(probe, mismatched))

        malformed_outputs = (
            "/opt/libxml2/bin/xmllint: using libxml version\n",
            "using libxml version 21503\n",
            "xmllint: using libxml version 21503\nunexpected trailer\n",
            "xmllint: using libxml version 21503\n   compiled with: \n",
        )
        for malformed in malformed_outputs:
            with self.subTest(output=malformed):
                self.assertFalse(LINT.version_output_matches(probe, malformed))

        for malformed_pin in ("", "2", "2.15", "2.15.3.1", "2.15.x"):
            with self.subTest(pin=malformed_pin), self.assertRaisesRegex(
                LINT.FormatterError,
                "invalid libxml2 version",
            ):
                LINT.libxml_numeric_version(malformed_pin)

    def test_rust_formatter_uses_the_pinned_toolchain(self) -> None:
        language = LINT.Language(
            id="rust",
            family="rust",
            extensions=(".rs",),
            filenames=(),
        )
        command = LINT.command_for(language, Path("fixture.rs"))

        self.assertEqual(
            ["rustup", "run", "1.97.1", "rustfmt", "fixture.rs"],
            command,
        )

    def test_npx_uses_the_windows_node_entrypoint(self) -> None:
        resolved = {
            "node.exe": r"C:\Program Files\nodejs\node.exe",
            "npx.cmd": r"C:\Program Files\nodejs\npx.cmd",
        }
        with mock.patch.object(
            LINT.os,
            "name",
            "nt",
        ), mock.patch.object(
            LINT.shutil,
            "which",
            side_effect=resolved.get,
        ), mock.patch.object(
            LINT.os.path,
            "isfile",
            return_value=True,
        ):
            command = LINT.npx_command()

        self.assertEqual(resolved["node.exe"], command[0])
        self.assertEqual(
            r"C:\Program Files\nodejs\node_modules\npm\bin\npx-cli.js",
            command[1],
        )
        self.assertFalse(
            any(part.lower().endswith((".cmd", ".bat")) for part in command)
        )

    def test_npx_formatter_commands_use_the_safe_launcher(self) -> None:
        cases = (
            ("json", "prettier", ".json"),
            ("bazel", "buildifier", ".bzl"),
        )
        launcher = ["node.exe", "npx-cli.js"]
        for language_id, family, extension in cases:
            language = LINT.Language(
                id=language_id,
                family=family,
                extensions=(extension,),
                filenames=(),
            )
            path = Path(f"fixture{extension}")
            with self.subTest(language=language_id), mock.patch.object(
                LINT,
                "npx_command",
                side_effect=lambda: list(launcher),
            ):
                command = LINT.command_for(language, path)

                self.assertEqual(launcher, command[:2])

    def test_windows_npx_refuses_an_incomplete_safe_entrypoint(self) -> None:
        with mock.patch.object(
            LINT.os,
            "name",
            "nt",
        ), mock.patch.object(
            LINT.shutil,
            "which",
            return_value=None,
        ), self.assertRaisesRegex(
            LINT.EngineError,
            "safe npx entry point",
        ):
            LINT.npx_command()

    def test_windows_npx_refuses_a_relative_safe_entrypoint(self) -> None:
        resolved = {
            "node.exe": r".\node.exe",
            "npx.cmd": r".\npx.cmd",
        }
        with mock.patch.object(
            LINT.os,
            "name",
            "nt",
        ), mock.patch.object(
            LINT.shutil,
            "which",
            side_effect=resolved.get,
        ), mock.patch.object(
            LINT.os.path,
            "isfile",
            return_value=True,
        ), self.assertRaisesRegex(
            LINT.EngineError,
            "safe npx entry point",
        ):
            LINT.npx_command()

    def test_windows_npx_refuses_a_missing_cli_script(self) -> None:
        resolved = {
            "node.exe": r"C:\Program Files\nodejs\node.exe",
            "npx.cmd": r"C:\Program Files\nodejs\npx.cmd",
        }
        with mock.patch.object(
            LINT.os,
            "name",
            "nt",
        ), mock.patch.object(
            LINT.shutil,
            "which",
            side_effect=resolved.get,
        ), mock.patch.object(
            LINT.os.path,
            "isfile",
            return_value=False,
        ), self.assertRaisesRegex(
            LINT.EngineError,
            "safe npx entry point",
        ):
            LINT.npx_command()

    @unittest.skipUnless(sys.platform == "win32", "requires a Windows host")
    def test_windows_npx_handles_a_command_metacharacter_in_a_filename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            launcher = root / "node-install"
            entrypoint = launcher / "node_modules" / "npm" / "bin" / "npx-cli.js"
            entrypoint.parent.mkdir(parents=True)
            entrypoint.write_text(
                "const fs = require('fs');\n"
                "const file = process.argv[process.argv.length - 1];\n"
                "fs.writeFileSync(file, 'const value = { answer: 42 };\\n');\n",
                encoding="utf-8",
            )
            (launcher / "npx.cmd").write_text(
                "@echo off\r\nexit /b 97\r\n",
                encoding="utf-8",
            )
            path = project / "a&ver&.js"
            path.write_text("const value={answer:42};\n", encoding="utf-8")
            env = os.environ.copy()
            env["PATH"] = f"{launcher}{os.pathsep}{env['PATH']}"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "lint.py"),
                    "--cwd",
                    str(project),
                    "--write",
                    "--json",
                    path.name,
                ],
                check=False,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(1, payload["summary"]["changed"])
            self.assertEqual("const value = { answer: 42 };\n", path.read_text())

    def test_npx_dependency_failure_has_an_actionable_hint(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=127,
            stdout=b"",
            stderr=b"npm error: package command not found\n",
        )
        cases = (
            ("markdown", "prettier", ".md", "npx -y prettier@3.7.4 --version"),
            (
                "bazel",
                "buildifier",
                ".bzl",
                "npx -y @bazel/buildifier@8.2.1 --version",
            ),
        )
        for language_id, family, extension, install_command in cases:
            language = LINT.Language(
                id=language_id,
                family=family,
                extensions=(extension,),
                filenames=(),
            )
            with self.subTest(
                language=language_id
            ), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / f"fixture{extension}"
                path.write_text("fixture\n", encoding="utf-8")
                with mock.patch.object(
                    LINT.subprocess,
                    "run",
                    return_value=completed,
                ), self.assertRaises(LINT.EngineError) as caught:
                    LINT.run_formatter(
                        language=language,
                        path=path,
                        cwd=root,
                        timeout_seconds=30,
                    )
                self.assertIn(install_command, str(caught.exception))

    def test_npx_engine_failure_recognizes_npm_outages(self) -> None:
        details = (
            "npm error code ECONNREFUSED\n",
            "npm error code ECONNRESET\n",
            "npm error code ETIMEDOUT\n",
            "npm error code ENETUNREACH\n",
            "npm error code EHOSTUNREACH\n",
            "npm error code EAI_AGAIN\n",
            "npm error code ENOTFOUND\n",
            "npm error code E404\n",
            "npm error code E403\n",
            "npm error code EACCES\n",
            "npm error code EPERM\n",
            "npm error code ENOSPC\n",
            "npm error network request timed out\n",
            "npm ERR! code ECONNREFUSED\n",
            "npm ERR! network request timed out\n",
        )
        language = LINT.Language(
            id="json",
            family="prettier",
            extensions=(".json",),
            filenames=(),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "fixture.json"
            path.write_text('{"value": true}\n', encoding="utf-8")
            for detail in details:
                completed = subprocess.CompletedProcess(
                    args=[],
                    returncode=1,
                    stdout=b"",
                    stderr=detail.encode("utf-8"),
                )
                with self.subTest(detail=detail), mock.patch.object(
                    LINT.subprocess,
                    "run",
                    return_value=completed,
                ), self.assertRaises(LINT.EngineError):
                    LINT.run_formatter(
                        language=language,
                        path=path,
                        cwd=root,
                        timeout_seconds=30,
                    )

    def test_npx_engine_failure_ignores_formatter_output(self) -> None:
        details = (
            "npm error code 2\nnpm error command failed\n",
            "npm error code ELIFECYCLE\nnpm error command failed\n",
            "SyntaxError: source contains ETIMEDOUT\n",
            "SyntaxError: source contains EAI_AGAIN and ENOTFOUND\n",
            "formatter mentioned npm error while parsing input\n",
            "[error] > 4 | npm error code EACCES\n",
        )
        for detail in details:
            with self.subTest(detail=detail):
                self.assertFalse(LINT.npx_engine_failure(1, detail))

    def test_windows_npx_dependency_failure_stays_actionable(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout=b"",
            stderr=b"npm error code EAI_AGAIN\n",
        )
        language = LINT.Language(
            id="json",
            family="prettier",
            extensions=(".json",),
            filenames=(),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "fixture.json"
            path.write_text('{"value": true}\n', encoding="utf-8")
            with mock.patch.object(
                LINT.os,
                "name",
                "nt",
            ), mock.patch.object(
                LINT.subprocess,
                "run",
                return_value=completed,
            ), self.assertRaises(LINT.EngineError):
                LINT.run_formatter(
                    language=language,
                    path=path,
                    cwd=root,
                    timeout_seconds=30,
                )

    def test_npx_formatter_failure_is_not_a_dependency_failure(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout=b"",
            stderr=(
                b"npm error command failed\n"
                b"npm error SyntaxError: JSON Error in fixture.json\n"
            ),
        )
        language = LINT.Language(
            id="json",
            family="prettier",
            extensions=(".json",),
            filenames=(),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "fixture.json"
            path.write_text('{"value":', encoding="utf-8")
            with mock.patch.object(
                LINT.subprocess,
                "run",
                return_value=completed,
            ), self.assertRaises(LINT.FormatterError) as caught:
                LINT.run_formatter(
                    language=language,
                    path=path,
                    cwd=root,
                    timeout_seconds=30,
                )

        self.assertNotIsInstance(caught.exception, LINT.EngineError)

    def test_version_mismatch_names_an_exact_install_command(self) -> None:
        language = LINT.Language(
            id="python",
            family="black",
            extensions=(".py",),
            filenames=(),
        )
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=b"black, 1.0.0\n",
            stderr=b"",
        )
        with mock.patch.object(
            LINT.subprocess,
            "run",
            return_value=completed,
        ), self.assertRaises(LINT.EngineError) as caught:
            LINT.verify_formatter_version(language)

        self.assertIn(
            "pipx install --force black==24.10.0",
            str(caught.exception),
        )

    def test_install_commands_are_runnable_formatter_recovery_steps(self) -> None:
        expected = {
            "prettier": "npx -y prettier@3.7.4 --version",
            "buildifier": "npx -y @bazel/buildifier@8.2.1 --version",
            "black": "pipx install --force black==24.10.0",
            "shfmt": "go install mvdan.cc/sh/v3/cmd/shfmt@v3.13.1",
            "clang": "pipx install --force clang-format==18.1.8",
            "java": "docker pull ghcr.io/trycopilotai/lint-java:0.1.6",
            "go": "docker pull ghcr.io/trycopilotai/lint-go:0.1.6",
            "rust": "rustup toolchain install 1.97.1 --component rustfmt",
            "kotlin": "docker pull ghcr.io/trycopilotai/lint-kotlin:0.1.6",
            "toml": "cargo install taplo-cli --version 0.10.0 --locked",
            "xml": "docker pull ghcr.io/trycopilotai/lint-xml:0.1.6",
            "swift": "docker pull ghcr.io/trycopilotai/lint-swift:0.1.6",
            "csharp": "dotnet tool install --global csharpier --version 1.3.0",
            "julia": "docker pull ghcr.io/trycopilotai/lint-julia:0.1.6",
        }
        languages_by_family = {}
        for language in LINT.load_languages():
            if language.family == "requirements":
                continue
            languages_by_family.setdefault(language.family, language)

        self.assertEqual(set(expected), set(languages_by_family))
        for family, command in expected.items():
            with self.subTest(family=family):
                language = languages_by_family[family]
                self.assertEqual(
                    command,
                    LINT.formatter_install_command(language),
                )

    def test_requirements_hash_continuations_stay_with_requirement(
        self,
    ) -> None:
        source = (
            "zeta==1\n"
            "alpha==1 \\\n"
            "    --hash=sha256:aaaaaaaa \\\n"
            "    --hash=sha256:bbbbbbbb\n"
        )
        expected = (
            "alpha==1 \\\n"
            "    --hash=sha256:aaaaaaaa \\\n"
            "    --hash=sha256:bbbbbbbb\n"
            "zeta==1\n"
        )

        local = LINT.requirements_output(source.encode("utf-8")).decode("utf-8")
        image = REQUIREMENTS_ENTRYPOINT.formatted(source)

        self.assertEqual(expected, local)
        self.assertEqual(expected, image)

    def test_requirements_golden_matches_both_formatters(self) -> None:
        fixture = ROOT / "fixtures" / "requirements"
        source = (fixture / "requirements.txt").read_bytes()
        expected = (fixture / "expected.txt").read_text(encoding="utf-8")

        local = LINT.requirements_output(source).decode("utf-8")
        image = REQUIREMENTS_ENTRYPOINT.formatted(source.decode("utf-8"))

        self.assertEqual(expected, local)
        self.assertEqual(expected, image)

    def test_requirements_sort_by_canonical_distribution_name(self) -> None:
        source = (
            "pyasn1-modules==0.4.2\n"
            "pyasn1==0.6.1\n"
            "google-auth-oauthlib==1.2.2\n"
            "google-auth==2.40.3\n"
            "requests-oauthlib==2.0.0\n"
            "requests==2.32.4\n"
            "pydantic-settings==2.10.1\n"
            "pydantic==2.11.7\n"
        )
        expected = (
            "google-auth==2.40.3\n"
            "google-auth-oauthlib==1.2.2\n"
            "pyasn1==0.6.1\n"
            "pyasn1-modules==0.4.2\n"
            "pydantic==2.11.7\n"
            "pydantic-settings==2.10.1\n"
            "requests==2.32.4\n"
            "requests-oauthlib==2.0.0\n"
        )

        local = LINT.requirements_output(source.encode("utf-8")).decode("utf-8")
        image = REQUIREMENTS_ENTRYPOINT.formatted(source)

        self.assertEqual(expected, local)
        self.assertEqual(expected, image)

    def test_clang_configuration_is_copied_into_each_mirror(self) -> None:
        language = LINT.Language(
            id="cpp",
            family="clang",
            extensions=(".cc",),
            filenames=(),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            nested = root / "src"
            nested.mkdir()
            source = nested / "fixture.cc"
            source.write_text("int main() { return 0; }\n", encoding="utf-8")
            (root / ".clang-format").write_text(
                "BasedOnStyle: Google\nColumnLimit: 60\n",
                encoding="utf-8",
            )
            (nested / "_clang-format").write_text(
                "BreakAfterAttributes: Always\n",
                encoding="utf-8",
            )

            def assert_mirror(mirror_root: Path) -> None:
                self.assertEqual(
                    "BasedOnStyle: Google\nColumnLimit: 60\n",
                    (mirror_root / ".clang-format").read_text(encoding="utf-8"),
                )
                self.assertEqual(
                    "BreakAfterAttributes: Always\n",
                    (mirror_root / "src" / "_clang-format").read_text(encoding="utf-8"),
                )

            def run_local(
                active_language,
                mirror_path,
                mirror_root,
                timeout_seconds,
            ):
                self.assertEqual(language.id, active_language.id)
                self.assertEqual(language.family, active_language.family)
                self.assertEqual(mirror_root / "src" / "fixture.cc", mirror_path)
                self.assertEqual(30, timeout_seconds)
                assert_mirror(mirror_root)

            with mock.patch.object(
                LINT,
                "verify_formatter_version",
            ), mock.patch.object(
                LINT,
                "run_formatter",
                side_effect=run_local,
            ):
                local_results, local_findings = LINT.prepare_results(
                    cwd=root,
                    paths=[source],
                    requested_languages=frozenset(),
                    use_docker=False,
                )

            def run_docker(
                active_language,
                mirror_root,
                relative_path,
                timeout_seconds,
            ):
                self.assertEqual(language.id, active_language.id)
                self.assertEqual(language.family, active_language.family)
                self.assertEqual(Path("src/fixture.cc"), relative_path)
                self.assertEqual(30, timeout_seconds)
                assert_mirror(mirror_root)

            with mock.patch.object(
                LINT,
                "run_docker_formatter",
                side_effect=run_docker,
            ):
                docker_results, docker_findings = LINT.prepare_results(
                    cwd=root,
                    paths=[source],
                    requested_languages=frozenset(),
                    use_docker=True,
                )

            self.assertEqual([], local_findings)
            self.assertEqual([], docker_findings)
            self.assertEqual(1, len(local_results))
            self.assertEqual(1, len(docker_results))

    def test_clang_configuration_symlink_is_rejected(self) -> None:
        language = LINT.Language(
            id="c",
            family="clang",
            extensions=(".c",),
            filenames=(),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "fixture.c"
            target = root / "style.yaml"
            source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
            target.write_text("BasedOnStyle: Google\n", encoding="utf-8")
            (root / ".clang-format").symlink_to(target)

            with self.assertRaisesRegex(
                LINT.FormatterError,
                "symbolic formatter configuration is not accepted",
            ):
                LINT.formatter_configuration_paths(root, source, language)

    def test_native_configuration_names_cover_supported_families(self) -> None:
        expected = {
            "prettier": {
                ".editorconfig",
                ".prettierrc",
                ".prettierrc.cjs",
                ".prettierrc.cts",
                ".prettierrc.js",
                ".prettierrc.json",
                ".prettierrc.json5",
                ".prettierrc.mjs",
                ".prettierrc.mts",
                ".prettierrc.toml",
                ".prettierrc.ts",
                ".prettierrc.yaml",
                ".prettierrc.yml",
                "package.json",
                "prettier.config.cjs",
                "prettier.config.cts",
                "prettier.config.js",
                "prettier.config.mjs",
                "prettier.config.mts",
                "prettier.config.ts",
            },
            "black": {"pyproject.toml"},
            "shfmt": {".editorconfig"},
            "clang": {".clang-format", "_clang-format"},
            "rust": {".rustfmt.toml", "rustfmt.toml"},
            "kotlin": {".editorconfig"},
            "toml": {".taplo.toml", "taplo.toml"},
            "swift": {".swift-format"},
            "csharp": {
                ".csharpierrc",
                ".csharpierrc.json",
                ".editorconfig",
            },
            "julia": {".JuliaFormatter.toml"},
        }

        actual = {
            family: set(names)
            for family, names in LINT.FORMATTER_CONFIGURATION_NAMES.items()
        }
        self.assertEqual(expected, actual)

    def test_prettier_configuration_reaches_local_and_docker_mirrors(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            nested = root / "docs"
            nested.mkdir()
            source = nested / "needs.md"
            source.write_text("# heading\n", encoding="utf-8")
            (root / ".prettierrc.json").write_text(
                '{"tabWidth": 4, "printWidth": 120}\n',
                encoding="utf-8",
            )
            (root / ".editorconfig").write_text(
                "root = true\n[*]\nindent_size = 4\n",
                encoding="utf-8",
            )
            (root / ".prettierignore").write_text(
                "docs/needs.md\n",
                encoding="utf-8",
            )

            def assert_mirror(mirror_root: Path) -> None:
                self.assertTrue((mirror_root / ".prettierrc.json").is_file())
                self.assertTrue((mirror_root / ".editorconfig").is_file())
                self.assertFalse((mirror_root / ".prettierignore").exists())
                self.assertEqual(b"", (mirror_root / ".lint-empty-ignore").read_bytes())

            with mock.patch.object(
                LINT,
                "run_formatter",
                side_effect=lambda language, path, cwd, timeout: assert_mirror(cwd),
            ):
                LINT.prepare_results(
                    cwd=root,
                    paths=[source],
                    requested_languages=frozenset(),
                    use_docker=False,
                )

            with mock.patch.object(
                LINT,
                "run_docker_formatter",
                side_effect=lambda language, cwd, path, timeout: assert_mirror(cwd),
            ):
                LINT.prepare_results(
                    cwd=root,
                    paths=[source],
                    requested_languages=frozenset(),
                    use_docker=True,
                )

            language = LINT.detect_language(source, LINT.load_languages())
            assert language is not None
            command = LINT.command_for(language, source)
            self.assertNotIn("--no-config", command)
            self.assertNotIn("--no-editorconfig", command)
            self.assertIn("--print-width", command)
            self.assertIn("60", command)
            self.assertIn("--prose-wrap", command)
            self.assertIn("always", command)
            self.assertIn("--trailing-comma", command)
            self.assertIn("none", command)

    def test_project_prettier_plugins_fail_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "needs.md"
            source.write_text("# heading\n", encoding="utf-8")
            (root / ".prettierrc.json").write_text(
                '{"plugins": ["prettier-plugin-example"]}\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                LINT.FormatterError,
                "project Prettier plugins are not supported",
            ):
                LINT.prepare_results(
                    cwd=root,
                    paths=[source],
                    requested_languages=frozenset(),
                    use_docker=False,
                )

    def test_executable_prettier_configuration_fails_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "needs.md"
            source.write_text("# heading\n", encoding="utf-8")
            (root / "prettier.config.js").write_text(
                "export default {};\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                LINT.FormatterError,
                "executable Prettier configuration is not supported",
            ):
                LINT.prepare_results(
                    cwd=root,
                    paths=[source],
                    requested_languages=frozenset(),
                    use_docker=False,
                )

    def test_black_configuration_cannot_suppress_selected_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "needs.py"
            source.write_text("value=1\n", encoding="utf-8")
            (root / "pyproject.toml").write_text(
                '[tool.black]\nforce-exclude = "needs.py"\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                LINT.FormatterError,
                "Black selection option is not supported",
            ):
                LINT.prepare_results(
                    cwd=root,
                    paths=[source],
                    requested_languages=frozenset(),
                    use_docker=False,
                )

            language = LINT.detect_language(source, LINT.load_languages())
            assert language is not None
            command = LINT.command_for(language, source)
            self.assertNotIn(os.devnull, command)
            self.assertIn("--line-length", command)
            self.assertIn("88", command)

    def test_black_python_cell_magics_configuration_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "needs.py"
            source.write_text("value=1\n", encoding="utf-8")
            for name in (
                "python-cell-magics",
                "python_cell_magics",
                '"--python-cell-magics"',
            ):
                (root / "pyproject.toml").write_text(
                    f'[tool.black]\n{name} = ["../../work/owned"]\n',
                    encoding="utf-8",
                )

                for use_docker in (False, True):
                    with self.subTest(name=name, use_docker=use_docker):
                        with self.assertRaisesRegex(
                            LINT.FormatterError,
                            "Black python-cell-magics option is not supported",
                        ):
                            LINT.prepare_results(
                                cwd=root,
                                paths=[source],
                                requested_languages=frozenset(),
                                use_docker=use_docker,
                            )

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

            self.assertEqual("changed", response["status"])
            self.assertEqual("changed", response["files"][0]["status"])
            self.assertEqual(
                "alpha==1\nzeta==1\n",
                path.read_text(encoding="utf-8"),
            )

    def test_failed_multi_language_write_rolls_back_prior_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_bytes(b"first\n")
            second.write_bytes(b"second\n")
            requirements = LINT.Language(
                id="requirements",
                family="requirements",
                extensions=(),
                filenames=(),
            )
            python = LINT.Language(
                id="python",
                family="black",
                extensions=(".py",),
                filenames=(),
            )
            results = [
                LINT.FormatResult(
                    path=first,
                    relative_path="first.txt",
                    language=requirements,
                    original=b"first\n",
                    formatted=b"changed first\n",
                    mode=0o644,
                ),
                LINT.FormatResult(
                    path=second,
                    relative_path="second.txt",
                    language=python,
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

    def test_a_tracked_symlink_is_excluded_from_default_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            initialize_repository(root)
            target = root / "requirements.txt"
            target.write_text("zeta==1\nalpha==1\n", encoding="utf-8")
            (root / "link").symlink_to("requirements.txt")
            subprocess.run(
                ["git", "-C", str(root), "add", "requirements.txt", "link"],
                check=True,
            )

            response = LINT.lint_files(
                cwd=root,
                paths=LINT.git_paths(root, False),
                requested_languages=frozenset(),
                write=False,
                use_docker=False,
            )

            self.assertEqual("needs_formatting", response["status"])
            self.assertEqual(1, response["summary"]["selected"])
            self.assertEqual(0, response["summary"]["skipped"])

    def test_read_only_never_reports_files_as_changed(self) -> None:
        language = LINT.Language(
            id="requirements",
            family="requirements",
            extensions=(),
            filenames=(),
        )
        results = [
            LINT.FormatResult(
                path=Path("needs.txt"),
                relative_path="needs.txt",
                language=language,
                original=b"zeta==1\nalpha==1\n",
                formatted=b"alpha==1\nzeta==1\n",
                mode=0o644,
            )
        ]

        read_only = LINT.response_for(
            results,
            [],
            write=False,
            backend="local",
        )
        self.assertEqual("read-only", read_only["mode"])
        self.assertNotIn("changed", read_only["summary"])
        self.assertEqual(1, read_only["summary"]["would_change"])

        written = LINT.response_for(
            results,
            [],
            write=True,
            backend="local",
        )
        self.assertEqual("write", written["mode"])
        self.assertNotIn("would_change", written["summary"])
        self.assertEqual(1, written["summary"]["changed"])

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
        self.assertIn(LINT.docker_user(), command)

    def test_docker_runner_has_a_windows_user_fallback(self) -> None:
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
                LINT.os,
                "getuid",
                side_effect=AttributeError,
                create=True,
            ), mock.patch.object(
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
        self.assertIn("65532:65532", command)

    def test_docker_runner_never_selects_root(self) -> None:
        with mock.patch.object(
            LINT.os,
            "getuid",
            return_value=0,
            create=True,
        ), mock.patch.object(
            LINT.os,
            "getgid",
            return_value=0,
            create=True,
        ):
            self.assertEqual("65532:65532", LINT.docker_user())


class MainTest(unittest.TestCase):
    def test_mode_selection_and_backend_cross_product(self) -> None:
        modes = (
            ((), False),
            (("--read-only",), False),
            (("--readonly",), False),
            (("-ro",), False),
            (("--write",), True),
            (("--apply",), True),
            (("-w",), True),
        )
        selections = (
            "default",
            "all",
            "modified",
            "files-from0",
            "positional",
        )
        backends = (
            ((), "local"),
            (("--docker",), "docker"),
            (("-d",), "docker"),
        )

        def fake_docker(
            language,
            mirror_root: Path,
            relative_path: Path,
            timeout_seconds: int,
        ) -> None:
            self.assertEqual("requirements", language.family)
            self.assertEqual(30, timeout_seconds)
            mirror = mirror_root / relative_path
            mirror.write_bytes(LINT.requirements_output(mirror.read_bytes()))

        for mode_arguments, write in modes:
            for selection in selections:
                for backend_arguments, backend in backends:
                    label = (mode_arguments, selection, backend)
                    with self.subTest(
                        label=label
                    ), tempfile.TemporaryDirectory() as directory:
                        root = Path(directory).resolve()
                        initialize_repository(root)
                        path = root / "requirements.txt"
                        clean = b"alpha==1\nzeta==1\n"
                        needs = b"zeta==1\nalpha==1\n"
                        path.write_bytes(clean)
                        subprocess.run(
                            ["git", "-C", str(root), "add", "requirements.txt"],
                            check=True,
                        )
                        subprocess.run(
                            ["git", "-C", str(root), "commit", "-qm", "fixture"],
                            check=True,
                        )
                        path.write_bytes(needs)
                        path_list = root.parent / f"{root.name}-paths.bin"
                        path_list.write_bytes(b"requirements.txt\0")
                        arguments = [
                            "--json",
                            "--cwd",
                            str(root),
                            *mode_arguments,
                            *backend_arguments,
                        ]
                        if selection == "all":
                            arguments.append("--all")
                        if selection == "modified":
                            arguments.append("--modified")
                        if selection == "files-from0":
                            arguments.extend(["--files-from0", str(path_list)])
                        if selection == "positional":
                            arguments.append("requirements.txt")
                        output = io.StringIO()
                        try:
                            with mock.patch.object(
                                LINT,
                                "run_docker_formatter",
                                side_effect=fake_docker,
                            ), contextlib.redirect_stdout(output):
                                exit_code = LINT.main(arguments)
                        finally:
                            path_list.unlink(missing_ok=True)

                        response = json.loads(output.getvalue())
                        self.assertEqual(backend, response["backend"])
                        if write:
                            self.assertEqual(LINT.EXIT_CLEAN, exit_code)
                            self.assertEqual("write", response["mode"])
                            self.assertEqual(clean, path.read_bytes())
                        else:
                            self.assertEqual(LINT.EXIT_FORMATTING, exit_code)
                            self.assertEqual("read-only", response["mode"])
                            self.assertEqual(needs, path.read_bytes())

    def test_dlint_prepends_docker_to_read_only_all_defaults(self) -> None:
        with mock.patch.object(
            DLINT.lint,
            "main",
            return_value=0,
        ) as delegated, mock.patch.object(sys, "argv", ["dlint.py"]):
            self.assertEqual(0, DLINT.main())

        delegated.assert_called_once_with(["--docker"])

    def test_read_only_preserves_tracked_untracked_modes_and_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            initialize_repository(root)
            tracked = root / "requirements.txt"
            untracked = root / "constraints.txt"
            link = root / "requirements-link"
            tracked.write_bytes(b"zeta==1\nalpha==1\n")
            subprocess.run(
                ["git", "-C", str(root), "add", "requirements.txt"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(root), "commit", "-qm", "fixture"],
                check=True,
            )
            untracked.write_bytes(b"gamma==1\nbeta==1\n")
            tracked.chmod(0o640)
            untracked.chmod(0o600)
            link.symlink_to("requirements.txt")

            def snapshot(paths: tuple[Path, ...]) -> dict[str, tuple[int, bytes]]:
                captured: dict[str, tuple[int, bytes]] = {}
                for path in paths:
                    metadata = path.lstat()
                    if path.is_symlink():
                        payload = os.readlink(path).encode("utf-8")
                    else:
                        payload = path.read_bytes()
                    captured[path.name] = (metadata.st_mode, payload)
                return captured

            paths = (tracked, untracked, link)
            before_link_exclusion = snapshot(paths)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = LINT.main(["--json", "--cwd", str(root)])
            self.assertEqual(LINT.EXIT_FORMATTING, exit_code)
            response = json.loads(output.getvalue())
            self.assertEqual(2, response["summary"]["selected"])
            self.assertEqual(before_link_exclusion, snapshot(paths))

            link.unlink()
            remaining = (tracked, untracked)
            before_read_only = snapshot(remaining)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = LINT.main(["--json", "--cwd", str(root)])
            self.assertEqual(LINT.EXIT_FORMATTING, exit_code)
            response = json.loads(output.getvalue())
            self.assertEqual("read-only", response["mode"])
            self.assertEqual(2, response["summary"]["selected"])
            self.assertEqual(before_read_only, snapshot(remaining))

    def test_default_selection_excludes_symbolic_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / "requirements.txt"
            target.write_text("zeta==1\nalpha==1\n", encoding="utf-8")
            (root / "link").symlink_to("requirements.txt")
            before = (target.read_bytes(), os.readlink(root / "link"))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = LINT.main(["--json", "--cwd", str(root)])

            self.assertEqual(LINT.EXIT_FORMATTING, exit_code)
            response = json.loads(output.getvalue())
            self.assertEqual(1, response["summary"]["selected"])
            self.assertEqual(0, response["summary"]["skipped"])
            self.assertEqual(
                before,
                (target.read_bytes(), os.readlink(root / "link")),
            )

    def test_explicit_selection_routes_reject_symbolic_links(self) -> None:
        routes = ("explicit", "files-from0")
        for route in routes:
            with self.subTest(route=route), tempfile.TemporaryDirectory() as directory:
                container = Path(directory).resolve()
                root = container / "project"
                root.mkdir()
                target = root / "requirements.txt"
                target.write_text("zeta==1\nalpha==1\n", encoding="utf-8")
                (root / "link").symlink_to("requirements.txt")
                arguments = ["--json", "--cwd", str(root)]
                if route == "explicit":
                    arguments.extend(["link", "requirements.txt"])
                if route == "files-from0":
                    path_list = container / "paths.bin"
                    path_list.write_bytes(b"link\0requirements.txt\0")
                    arguments.extend(["--files-from0", str(path_list)])
                error = io.StringIO()
                with contextlib.redirect_stderr(error):
                    exit_code = LINT.main(arguments)

                self.assertEqual(LINT.EXIT_SELECTION, exit_code)
                response = json.loads(error.getvalue())
                self.assertEqual("selection_error", response["status"])
                self.assertIn(
                    "symbolic links are not accepted",
                    response["message"],
                )

    def test_requirements_worker_reports_invalid_input_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requirements.txt"
            path.write_bytes(b"\xff")
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                exit_code = LINT.main([LINT.REQUIREMENTS_WORKER, str(path)])

            self.assertEqual(LINT.EXIT_FORMATTING, exit_code)
            self.assertIn("requirements file is not UTF-8", error.getvalue())
            self.assertNotIn("Traceback", error.getvalue())

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
                "needs formatting: 1 selected, 1 would change, 0 skipped",
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
            path.write_bytes(b"alpha==1\n")
            subprocess.run(
                ["git", "-C", str(root), "add", "requirements.txt"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(root), "commit", "-qm", "fixture"],
                check=True,
            )
            path.write_bytes(b"# modified\nalpha==1\n")

            completed = subprocess.run(
                [
                    "make",
                    "-C",
                    str(root),
                    "-f",
                    str(ROOT / "Makefile"),
                    "lint",
                    f"PYTHON={sys.executable}",
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
            decoder = json.JSONDecoder()
            response, _ = decoder.raw_decode(completed.stdout[start:])
            self.assertEqual("clean", response["status"])
            self.assertEqual(1, response["summary"]["selected"])

    def test_make_short_aliases_match_local_and_docker_languages(self) -> None:
        expected = {
            "ts": "--language typescript --language tsx",
            "js": "--language javascript",
            "md": "--language markdown",
            "py": "--language python",
            "sh": "--language shell",
            "cs": "--language csharp",
        }
        for suffix, languages in expected.items():
            for prefix, script in (("lint", "lint.py"), ("dlint", "dlint.py")):
                target = f"{prefix}_{suffix}"
                with self.subTest(target=target):
                    completed = subprocess.run(
                        ["make", "-s", "-n", target],
                        cwd=ROOT,
                        check=True,
                        stdout=subprocess.PIPE,
                        text=True,
                    )
                    command = " ".join(completed.stdout.split())
                    self.assertIn(f"python3 {script}", command)
                    self.assertIn(languages, command)

    def test_every_long_make_target_is_phony_and_language_specific(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for language in LINT.load_languages():
                for prefix, script in (("lint", "lint.py"), ("dlint", "dlint.py")):
                    target = f"{prefix}_{language.id}"
                    (root / target).write_text("", encoding="utf-8")
                    completed = subprocess.run(
                        [
                            "make",
                            "-s",
                            "-n",
                            "-C",
                            str(root),
                            "-f",
                            str(ROOT / "Makefile"),
                            target,
                        ],
                        check=True,
                        stdout=subprocess.PIPE,
                        text=True,
                    )
                    command = " ".join(completed.stdout.split())
                    with self.subTest(target=target):
                        self.assertIn(f"python3 {script}", command)
                        self.assertIn(
                            f'--language "{language.id}"',
                            command,
                        )


if __name__ == "__main__":
    unittest.main()
