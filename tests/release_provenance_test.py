from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


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


PROVENANCE = load_module(
    "tools/release_provenance.py",
    "release_provenance_under_test",
)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class ReleaseProvenanceTest(unittest.TestCase):
    def write_inputs(self, directory: Path) -> tuple[Path, Path, Path]:
        revision = "a" * 40
        archive = directory / "lint-0.1.6.tar.gz"
        archive.write_bytes(b"deterministic archive\n")
        workflow = directory / "release.yml"
        workflow.write_bytes(b"name: Release\n")
        manifest = directory / "release-manifest-0.1.6.json"
        manifest.write_text(
            json.dumps(
                {
                    "images": {
                        "ghcr.io/trycopilotai/lint-python": "sha256:" + "1" * 64,
                        "ghcr.io/trycopilotai/lint-rust": "sha256:" + "2" * 64,
                    },
                    "release": "0.1.6",
                    "schema_version": 1,
                    "source": {
                        "archive": archive.name,
                        "commit": revision,
                        "sha256": sha256(archive.read_bytes()),
                    },
                    "tools": {},
                },
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return archive, manifest, workflow

    def test_statement_binds_source_manifest_images_and_controller(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive, manifest, workflow = self.write_inputs(directory)
            statement = PROVENANCE.build_statement(
                version="0.1.6",
                revision="a" * 40,
                archive=archive,
                manifest=manifest,
                workflow=workflow,
            )
            manifest_digest = sha256(manifest.read_bytes())

        self.assertEqual("https://in-toto.io/Statement/v1", statement["_type"])
        self.assertEqual(
            "https://slsa.dev/provenance/v1",
            statement["predicateType"],
        )
        subjects = {row["name"]: row["digest"] for row in statement["subject"]}
        self.assertEqual(
            sha256(b"deterministic archive\n"),
            subjects["lint-0.1.6.tar.gz"]["sha256"],
        )
        self.assertEqual(
            manifest_digest,
            subjects["release-manifest-0.1.6.json"]["sha256"],
        )
        self.assertEqual(
            "1" * 64,
            subjects["ghcr.io/trycopilotai/lint-python"]["sha256"],
        )
        dependencies = statement["predicate"]["buildDefinition"]["resolvedDependencies"]
        self.assertEqual("a" * 40, dependencies[0]["digest"]["gitCommit"])
        self.assertEqual(
            sha256(b"name: Release\n"),
            dependencies[1]["digest"]["sha256"],
        )

    def test_serialization_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive, manifest, workflow = self.write_inputs(directory)
            first = directory / "first.json"
            second = directory / "second.json"

            for output in (first, second):
                PROVENANCE.write_statement(
                    version="0.1.6",
                    revision="a" * 40,
                    archive=archive,
                    manifest=manifest,
                    workflow=workflow,
                    output=output,
                )

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertTrue(first.read_bytes().endswith(b"\n"))

    def test_manifest_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive, manifest, workflow = self.write_inputs(directory)
            value = json.loads(manifest.read_text(encoding="utf-8"))
            value["source"]["sha256"] = "0" * 64
            manifest.write_text(json.dumps(value), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "archive digest"):
                PROVENANCE.build_statement(
                    version="0.1.6",
                    revision="a" * 40,
                    archive=archive,
                    manifest=manifest,
                    workflow=workflow,
                )

    def test_private_release_always_builds_local_attestation(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("python3 tools/release_provenance.py", workflow)
        self.assertIn("dist/provenance-$version.intoto.json", workflow)
        for step_name in ("Attest image", "Attest source archive"):
            with self.subTest(step=step_name):
                step = workflow.split(f"- name: {step_name}", 1)[1].split(
                    "- name:",
                    1,
                )[0]
                self.assertIn(
                    "if: steps.visibility.outputs.value == 'public'",
                    step,
                )


if __name__ == "__main__":
    unittest.main()
