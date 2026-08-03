from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import lint


def load_verifier():
    path = ROOT / "images" / "verify_cli.py"
    specification = importlib.util.spec_from_file_location(
        "image_cli_verifier_under_test",
        path,
    )
    if specification is None:
        raise RuntimeError("could not create image CLI verifier specification")
    if specification.loader is None:
        raise RuntimeError("image CLI verifier specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


VERIFIER = load_verifier()


def load_deriver():
    path = ROOT / "images" / "derive_golden.py"
    sys.path.insert(0, str(path.parent))
    specification = importlib.util.spec_from_file_location(
        "golden_deriver_under_test",
        path,
    )
    if specification is None:
        raise RuntimeError("could not create golden deriver specification")
    if specification.loader is None:
        raise RuntimeError("golden deriver specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


DERIVER = load_deriver()


class FixtureCoverageTest(unittest.TestCase):
    def test_every_public_language_has_complete_golden_evidence(self) -> None:
        languages = lint.load_languages()
        expected = {language.id for language in languages}

        self.assertEqual(expected, set(VERIFIER.FIXTURES))
        self.assertEqual(expected, set(VERIFIER.EXPECTED))
        self.assertEqual(expected, set(VERIFIER.MALFORMED))
        self.assertEqual(28, len(expected))
        for language_id, path in VERIFIER.FIXTURES.items():
            with self.subTest(language=language_id):
                self.assertTrue(path.is_file())
                detected = lint.detect_language(path, languages)
                self.assertIsNotNone(detected)
                assert detected is not None
                self.assertEqual(language_id, detected.id)
                expected_path = VERIFIER.EXPECTED[language_id]
                malformed_path = VERIFIER.MALFORMED[language_id]
                self.assertTrue(expected_path.is_file())
                self.assertTrue(malformed_path.is_file())
                self.assertNotEqual(path.read_bytes(), expected_path.read_bytes())

    def test_golden_output_is_loaded_from_committed_fixture(self) -> None:
        source = (ROOT / "images" / "verify_cli.py").read_text(encoding="utf-8")

        self.assertNotIn("stable_formatter_output", source)
        self.assertIn("EXPECTED", source)


class WorkflowCoverageTest(unittest.TestCase):
    def test_local_workflow_runs_the_committed_golden_verifier(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("local-golden-matrix", workflow)
        self.assertIn("--backend local", workflow)
        self.assertIn("images/verify_cli.py", workflow)
        self.assertIn("images/matrix.json", workflow)

    def test_local_workflow_pins_every_formatter_family(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        markers = {
            "prettier": 'node-version: "24.18.0"',
            "buildifier": "'buildifier'",
            "black": "black==24.10.0",
            "requirements": "--backend local",
            "shfmt": "shfmt@v3.13.1",
            "clang": "clang-format==18.1.8",
            "java": "/v1.35.0/google-java-format_linux-x86-64",
            "go": 'go-version: "1.26.5"',
            "rust": "rustup toolchain install 1.97.1",
            "kotlin": "/1.3.0/ktlint",
            "taplo": "/0.10.0/taplo-linux-x86_64.gz",
            "xml": "/libxml2-2.15.3.tar.xz",
            "swift": "/refs/tags/603.0.0.tar.gz",
            "csharp": "--version 1.3.0",
            "julia": "julia_formatter_source.sha256",
        }
        matrix_targets = {row["target"] for row in VERIFIER.load_matrix()}

        self.assertEqual(matrix_targets, set(markers))
        for target, marker in markers.items():
            with self.subTest(target=target):
                self.assertIn(marker, workflow)

    def test_image_workflow_checks_both_architectures_and_oci_layers(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "images.yml").read_text(
            encoding="utf-8"
        )

        self.assertEqual(2, workflow.count("--backend docker"))
        self.assertIn("linux/amd64", workflow)
        self.assertIn("linux/arm64", workflow)
        self.assertEqual(2, workflow.count("--platform"))
        self.assertEqual(2, workflow.count("--oci-layout"))

    def test_every_workflow_invocation_selects_a_backend(self) -> None:
        invocation_count = 0
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            workflow = path.read_text(encoding="utf-8")
            for remainder in workflow.split("python3 images/verify_cli.py")[1:]:
                invocation_count += 1
                step = remainder.split("\n      - name:", 1)[0]
                with self.subTest(workflow=path.name, invocation=invocation_count):
                    self.assertIn("--backend ", step)
        self.assertEqual(5, invocation_count)

    def test_local_julia_uses_the_locked_docker_project(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        install = workflow.split("- name: Install JuliaFormatter", 1)[1].split(
            "- name: Verify local golden", 1
        )[0]

        self.assertIn("cp -R images/julia", install)
        self.assertIn("images/sources.json", install)
        self.assertIn("julia_formatter_source.sha256", install)
        self.assertIn("Pkg.instantiate()", install)
        self.assertNotIn("Pkg.add", install)
        self.assertIn("JULIA_PROJECT:", workflow)
        self.assertIn("JULIA_DEPOT_PATH:", workflow)

    def test_julia_image_bounds_cold_start_compilation(self) -> None:
        dockerfile = (ROOT / "images" / "Dockerfile").read_text(encoding="utf-8")
        entrypoint = (
            'ENTRYPOINT ["/usr/local/julia/bin/julia", '
            '"--compiled-modules=no", "--compile=min", '
            '"--optimize=0", "--startup-file=no", '
            '"/julia-format.jl"]'
        )

        self.assertIn(entrypoint, dockerfile)


class CliTransitionTest(unittest.TestCase):
    def test_julia_entrypoints_propagate_parse_errors(self) -> None:
        language = lint.Language(
            id="julia",
            family="julia",
            extensions=(".jl",),
            filenames=(),
        )
        command = " ".join(lint.command_for(language, Path("broken.jl")))
        entrypoint = (ROOT / "images" / "julia_entrypoint.jl").read_text(
            encoding="utf-8"
        )

        self.assertIn("throw_on_error=true", command)
        self.assertIn("throw_on_error = true", entrypoint)

    def test_julia_version_check_gets_the_public_file_timeout(self) -> None:
        cases = (
            ("julia", 30, b"1.12.6\n2.12.3"),
            (
                "black",
                10,
                b"black, 24.10.0 (compiled: yes)\nPython (CPython) 3.13.14",
            ),
        )
        for family, expected_timeout, output in cases:
            with self.subTest(family=family):
                language = lint.Language(
                    id=family,
                    family=family,
                    extensions=(".fixture",),
                    filenames=(),
                )
                completed = subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=output,
                    stderr=b"",
                )
                with mock.patch.object(
                    lint.subprocess,
                    "run",
                    return_value=completed,
                ) as run:
                    lint.verify_formatter_version(language)

                self.assertEqual(expected_timeout, run.call_args.kwargs["timeout"])

    def test_local_read_only_write_and_clean_sequence_is_enforced(self) -> None:
        expected = b"alpha==1\nzeta==1\n"
        calls: list[bool] = []

        def runner(
            directory: Path,
            filename: str,
            language_id: str,
            write: bool,
        ):
            calls.append(write)
            path = directory / filename
            if write:
                path.write_bytes(expected)
                return VERIFIER.CliResult(
                    returncode=0,
                    response={
                        "backend": "local",
                        "mode": "write",
                        "status": "changed",
                    },
                )
            if len(calls) == 1:
                return VERIFIER.CliResult(
                    returncode=1,
                    response={
                        "backend": "local",
                        "mode": "read-only",
                        "status": "needs_formatting",
                    },
                )
            return VERIFIER.CliResult(
                returncode=0,
                response={
                    "backend": "local",
                    "mode": "read-only",
                    "status": "clean",
                },
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "requirements.txt"
            path.write_bytes(b"zeta==1\nalpha==1\n")
            path.chmod(0o640)

            VERIFIER.verify_cli_transitions(
                path=path,
                language_id="requirements",
                expected=expected,
                backend="local",
                runner=runner,
            )

            self.assertEqual(expected, path.read_bytes())
            expected_mode = 0o640
            if sys.platform == "win32":
                expected_mode = 0o666
            self.assertEqual(expected_mode, stat.S_IMODE(path.stat().st_mode))
        self.assertEqual([False, True, False], calls)

    def test_malformed_input_must_fail_without_writing(self) -> None:
        calls: list[bool] = []

        def runner(
            directory: Path,
            filename: str,
            language_id: str,
            write: bool,
        ):
            calls.append(write)
            return VERIFIER.CliResult(
                returncode=1,
                response={
                    "schema_version": 2,
                    "status": "formatter_error",
                },
            )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.py"
            malformed = b"def broken(:\n"
            path.write_bytes(malformed)
            path.chmod(0o640)

            VERIFIER.verify_malformed_input(
                path=path,
                language_id="python",
                backend="local",
                runner=runner,
            )

            self.assertEqual(malformed, path.read_bytes())
            expected_mode = 0o640
            if sys.platform == "win32":
                expected_mode = 0o666
            self.assertEqual(expected_mode, stat.S_IMODE(path.stat().st_mode))
        self.assertEqual([False], calls)

    def test_cli_result_requires_one_json_object(self) -> None:
        payload = json.dumps({"status": "clean"})
        result = VERIFIER.parse_cli_result(0, payload, "")

        self.assertEqual(0, result.returncode)
        self.assertEqual("clean", result.response["status"])
        with self.assertRaisesRegex(ValueError, "JSON object"):
            VERIFIER.parse_cli_result(0, "[]", "")

    def test_cli_result_reads_json_errors_from_standard_error(self) -> None:
        payload = json.dumps({"status": "formatter_error"})

        result = VERIFIER.parse_cli_result(1, "", payload)

        self.assertEqual(1, result.returncode)
        self.assertEqual("formatter_error", result.response["status"])


class GoldenDerivationTest(unittest.TestCase):
    def test_malformed_image_receives_the_malformed_relative_path(self) -> None:
        calls: list[str] = []

        def run_image(image: str, directory: Path, filename: str):
            calls.append(filename)
            path = directory / filename
            if len(calls) == 1:
                path.write_bytes(VERIFIER.EXPECTED["requirements"].read_bytes())
            returncode = 0
            if len(calls) == 3:
                returncode = 1
            return subprocess.CompletedProcess(
                args=[image, filename],
                returncode=returncode,
                stdout=b"",
                stderr=b"",
            )

        with mock.patch.object(DERIVER, "run_image", side_effect=run_image):
            result = DERIVER.derive("requirements", "fixture:golden")

        self.assertEqual(
            [
                "requirements.txt",
                "requirements.txt",
                "malformed/requirements.txt",
            ],
            calls,
        )
        self.assertEqual(1, result["malformed_image_returncode"])

    def test_syntax_fixture_must_be_rejected_by_the_image(self) -> None:
        calls = 0

        def run_image(image: str, directory: Path, filename: str):
            nonlocal calls
            calls += 1
            path = directory / filename
            if calls == 1:
                path.write_bytes(VERIFIER.EXPECTED["html"].read_bytes())
            return subprocess.CompletedProcess(
                args=[image, filename],
                returncode=0,
                stdout=b"",
                stderr=b"",
            )

        with mock.patch.object(
            DERIVER,
            "run_image",
            side_effect=run_image,
        ), self.assertRaisesRegex(ValueError, "accepted malformed"):
            DERIVER.derive("html", "fixture:golden")


if __name__ == "__main__":
    unittest.main()
