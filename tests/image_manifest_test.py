from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(relative_path: str, name: str):
    path = ROOT / relative_path
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None:
        raise RuntimeError(f"could not load {relative_path}")
    if specification.loader is None:
        raise RuntimeError(f"could not load {relative_path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


LINT = load_module("lint.py", "lint_for_image_manifest_test")
RELEASE_MANIFEST = load_module(
    "tools/release_manifest.py",
    "release_manifest_for_lint_cli_test",
)


def release_manifest() -> dict:
    images = {}
    for index, language in enumerate(LINT.load_languages(), start=1):
        image = f"ghcr.io/trycopilotai/lint-{language.id}"
        images[image] = "sha256:" + f"{index:064x}"
    return {
        "schema_version": 1,
        "release": LINT.image_version(),
        "source": {
            "archive": f"lint-{LINT.image_version()}.tar.gz",
            "commit": "a" * 40,
            "sha256": "b" * 64,
        },
        "tools": LINT.tool_versions(),
        "images": images,
    }


def write_manifest(directory: Path, value: dict) -> Path:
    path = directory / "release-manifest.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class ImageManifestTest(unittest.TestCase):
    def assert_rejected(self, value: dict, message: str) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = write_manifest(Path(temporary), value)
            with self.assertRaisesRegex(LINT.SelectionError, message):
                LINT.load_image_manifest(path)

    def test_release_manifest_selects_all_exact_image_digests(self) -> None:
        value = release_manifest()
        with tempfile.TemporaryDirectory() as temporary:
            path = write_manifest(Path(temporary), value)
            references = LINT.load_image_manifest(path)

        self.assertEqual(28, len(references))
        self.assertEqual(
            "ghcr.io/trycopilotai/lint-python@"
            + value["images"]["ghcr.io/trycopilotai/lint-python"],
            references["python"],
        )
        validated = RELEASE_MANIFEST.validate_digest_map(value["images"])
        self.assertEqual(
            set(validated), {item.split("@", 1)[0] for item in references.values()}
        )

    def test_schema_release_source_tools_and_images_are_exact(self) -> None:
        value = release_manifest()

        missing_top_level = copy.deepcopy(value)
        del missing_top_level["source"]
        self.assert_rejected(missing_top_level, "keys differ")

        extra_top_level = copy.deepcopy(value)
        extra_top_level["other"] = None
        self.assert_rejected(extra_top_level, "keys differ")

        wrong_schema = copy.deepcopy(value)
        wrong_schema["schema_version"] = True
        self.assert_rejected(wrong_schema, "schema version")

        wrong_release = copy.deepcopy(value)
        wrong_release["release"] = "9.9.9"
        self.assert_rejected(wrong_release, "release version")

        extra_source = copy.deepcopy(value)
        extra_source["source"]["other"] = "value"
        self.assert_rejected(extra_source, "source keys differ")

        wrong_archive = copy.deepcopy(value)
        wrong_archive["source"]["archive"] = "other.tar.gz"
        self.assert_rejected(wrong_archive, "archive name")

        wrong_commit = copy.deepcopy(value)
        wrong_commit["source"]["commit"] = "a" * 39
        self.assert_rejected(wrong_commit, "source commit")

        wrong_source_digest = copy.deepcopy(value)
        wrong_source_digest["source"]["sha256"] = "b" * 63
        self.assert_rejected(wrong_source_digest, "source digest")

        wrong_tools = copy.deepcopy(value)
        wrong_tools["tools"]["black"] = "0.0.0"
        self.assert_rejected(wrong_tools, "tool versions")

        missing_image = copy.deepcopy(value)
        del missing_image["images"]["ghcr.io/trycopilotai/lint-python"]
        self.assert_rejected(missing_image, "coverage differs")

        extra_image = copy.deepcopy(value)
        extra_image["images"]["ghcr.io/trycopilotai/lint-other"] = "sha256:" + "c" * 64
        self.assert_rejected(extra_image, "coverage differs")

        wrong_image_digest = copy.deepcopy(value)
        wrong_image_digest["images"]["ghcr.io/trycopilotai/lint-python"] = (
            "sha256:" + "g" * 64
        )
        self.assert_rejected(wrong_image_digest, "invalid digest")

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "release-manifest.json"
            path.write_text(
                '{"schema_version":1,"schema_version":1}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(LINT.SelectionError, "repeats key"):
                LINT.load_image_manifest(path)

    def test_manifest_reference_reaches_the_exact_docker_argv(self) -> None:
        language = next(
            language for language in LINT.load_languages() if language.id == "python"
        )
        reference = "ghcr.io/trycopilotai/lint-python@sha256:" + "c" * 64
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=b"",
            stderr=b"",
        )
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(
                LINT.subprocess,
                "run",
                return_value=completed,
            ) as run:
                LINT.run_docker_formatter(
                    language,
                    Path(temporary),
                    Path("fixture.py"),
                    30,
                    reference,
                )

        command = run.call_args.args[0]
        self.assertIn(reference, command)
        self.assertNotIn(LINT.docker_image(language), command)

    def test_cli_routes_manifest_only_to_the_docker_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = write_manifest(directory, release_manifest())
            clean_response = {
                "schema_version": 2,
                "status": "clean",
                "mode": "read-only",
                "backend": "docker",
                "files": [],
                "summary": {
                    "selected": 0,
                    "would_change": 0,
                    "skipped": 0,
                },
            }
            output = io.StringIO()
            with mock.patch.object(
                LINT,
                "select_paths",
                return_value=[],
            ), mock.patch.object(
                LINT,
                "lint_files",
                return_value=clean_response,
            ) as lint_files, contextlib.redirect_stdout(
                output
            ):
                exit_code = LINT.main(
                    [
                        "--docker",
                        "--image-manifest",
                        str(path),
                        "--cwd",
                        str(directory),
                        "--json",
                    ]
                )

        self.assertEqual(LINT.EXIT_CLEAN, exit_code)
        call = lint_files.call_args.kwargs
        self.assertTrue(call["use_docker"])
        self.assertEqual(28, len(call["image_references"]))

    def test_local_and_list_only_invocations_reject_the_manifest(self) -> None:
        combinations = (
            ("--image-manifest", "missing.json"),
            (
                "--docker",
                "--image-manifest",
                "missing.json",
                "--list-languages",
            ),
        )
        for arguments in combinations:
            error = io.StringIO()
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(error):
                exit_code = LINT.main(["--json", *arguments])
            self.assertEqual(LINT.EXIT_SELECTION, exit_code)
            response = json.loads(error.getvalue())
            self.assertEqual("selection_error", response["status"])


if __name__ == "__main__":
    unittest.main()
