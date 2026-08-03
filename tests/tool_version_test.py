from __future__ import annotations

import importlib.util
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_tool_version_verifier():
    path = ROOT / "images" / "verify_tool_version.py"
    specification = importlib.util.spec_from_file_location(
        "tool_version_verifier_under_test",
        path,
    )
    if specification is None:
        raise RuntimeError("could not create tool version verifier specification")
    if specification.loader is None:
        raise RuntimeError("tool version verifier specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


VERIFIER = load_tool_version_verifier()


def identity_archive(payload: bytes, name: str = "lint-tool-version") -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        member = tarfile.TarInfo(name)
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    return output.getvalue()


class ToolVersionTest(unittest.TestCase):
    def test_every_image_target_has_one_runtime_tool_identity(self) -> None:
        matrix = json.loads(
            (ROOT / "images" / "matrix.json").read_text(encoding="utf-8")
        )
        targets = {row["target"] for row in matrix["images"]}

        self.assertEqual(15, len(targets))
        self.assertEqual(targets, set(VERIFIER.TARGET_TO_TOOL))
        self.assertEqual(15, len(set(VERIFIER.TARGET_TO_TOOL.values())))

    def test_exact_version_record_passes(self) -> None:
        with mock.patch.object(
            VERIFIER,
            "docker_identity",
            return_value=b"prettier=3.7.4\n",
        ):
            result = VERIFIER.verify("prettier", "lint-prettier:test")

        self.assertEqual("3.7.4", result["identity"])

    def test_mutated_and_near_versions_fail(self) -> None:
        for payload in (
            b"prettier=3.7.5\n",
            b"prettier=13.7.4\n",
            b"prettier=3.7.4-rc.1\n",
            b"prettier=v3.7.4\n",
            b"prettier=3.7.4 extra\n",
        ):
            with self.subTest(payload=payload), mock.patch.object(
                VERIFIER,
                "docker_identity",
                return_value=payload,
            ):
                with self.assertRaises(ValueError):
                    VERIFIER.verify("prettier", "lint-prettier:test")

    def test_record_grammar_rejects_suffixes_and_missing_newline(self) -> None:
        for payload in (
            b"prettier=3.7.4",
            b"prefix prettier=3.7.4\n",
            b"prettier=3.7.4\nextra",
            b"prettier=3.7.4\n\n",
            b"prettier =3.7.4\n",
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    VERIFIER.parse_identity(payload)

    def test_requirements_identity_is_an_exact_sha256(self) -> None:
        digest = "7" * 64
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "languages.json"
            manifest.write_text(
                json.dumps({"tools": {"requirements": f"sha256:{digest}"}}),
                encoding="utf-8",
            )
            with mock.patch.object(VERIFIER, "LANGUAGES_PATH", manifest):
                self.assertEqual(
                    ("requirements", f"sha256:{digest}"),
                    VERIFIER.expected_identity("requirements"),
                )
                manifest.write_text(
                    json.dumps({"tools": {"requirements": f"sha256:{digest}0"}}),
                    encoding="utf-8",
                )
                with self.assertRaises(ValueError):
                    VERIFIER.expected_identity("requirements")

    def test_archive_requires_one_exact_regular_path(self) -> None:
        payload = b"prettier=3.7.4\n"
        self.assertEqual(
            payload,
            VERIFIER.extract_identity_archive(identity_archive(payload)),
        )
        for archive in (
            identity_archive(payload, "other"),
            identity_archive(payload, "./lint-tool-version"),
        ):
            with self.subTest():
                with self.assertRaises(ValueError):
                    VERIFIER.extract_identity_archive(archive)

    def test_docker_extraction_removes_the_created_container(self) -> None:
        container_id = "a" * 64
        completed = [
            mock.Mock(stdout=f"{container_id}\n".encode("ascii")),
            mock.Mock(stdout=identity_archive(b"prettier=3.7.4\n")),
            mock.Mock(stdout=b""),
        ]
        with mock.patch.object(
            VERIFIER.subprocess,
            "run",
            side_effect=completed,
        ) as run:
            self.assertEqual(
                b"prettier=3.7.4\n",
                VERIFIER.docker_identity("lint-prettier:test"),
            )

        self.assertEqual(
            ["docker", "rm", "--force", container_id],
            run.call_args_list[2].args[0],
        )


if __name__ == "__main__":
    unittest.main()
