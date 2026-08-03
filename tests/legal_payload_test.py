from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_generator():
    path = ROOT / "tools" / "generate_legal_payloads.py"
    specification = importlib.util.spec_from_file_location(
        "legal_payload_generator_under_test",
        path,
    )
    if specification is None:
        raise RuntimeError("could not create legal generator specification")
    if specification.loader is None:
        raise RuntimeError("legal generator specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


GENERATOR = load_generator()


class LegalPayloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source_path = self.root / "license_sources.json"
        self.output_root = self.root / "licenses"
        shutil.copyfile(
            ROOT / "images" / "license_sources.json",
            self.source_path,
        )
        shutil.copytree(ROOT / "images" / "licenses", self.output_root)
        self.patches = (
            mock.patch.object(GENERATOR, "SOURCE_PATH", self.source_path),
            mock.patch.object(GENERATOR, "OUTPUT_ROOT", self.output_root),
        )
        for patch in self.patches:
            patch.start()

    def tearDown(self) -> None:
        for patch in reversed(self.patches):
            patch.stop()
        self.temporary.cleanup()

    def source_document(self) -> dict:
        return json.loads(self.source_path.read_text(encoding="utf-8"))

    def write_source_document(self, value: dict) -> None:
        self.source_path.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def test_checked_in_payloads_are_complete(self) -> None:
        GENERATOR.check_payloads()

    def test_registry_hash_mutation_fails_closed(self) -> None:
        value = self.source_document()
        value["sources"][0]["sha256"] = "0" * 64
        self.write_source_document(value)

        with self.assertRaisesRegex(ValueError, "checksum differs"):
            GENERATOR.check_payloads()

    def test_payload_mutation_fails_closed(self) -> None:
        path = self.output_root / "prettier" / "prettier-LICENSE"
        path.write_bytes(path.read_bytes() + b"mutation")

        with self.assertRaisesRegex(ValueError, "checksum differs"):
            GENERATOR.check_payloads()

    def test_cargo_payload_mutation_fails_closed(self) -> None:
        path = self.output_root / "taplo" / "cargo-lock-legal-payload.txt"
        path.write_bytes(path.read_bytes() + b"mutation")

        with self.assertRaisesRegex(ValueError, "checksum differs"):
            GENERATOR.check_payloads()

    def test_archive_payload_mutation_fails_closed(self) -> None:
        path = self.output_root / "csharp" / "csharpier-runtime-dependencies.txt"
        path.write_bytes(path.read_bytes() + b"mutation")

        with self.assertRaisesRegex(ValueError, "checksum differs"):
            GENERATOR.check_payloads()

    def test_archive_registry_member_mutation_fails_closed(self) -> None:
        value = self.source_document()
        value["archive_bundles"][0]["members"] = [
            "tools/net9.0/any/CSharpier.deps.json"
        ]
        self.write_source_document(value)

        with self.assertRaisesRegex(ValueError, "metadata differs"):
            GENERATOR.check_payloads()

    def test_cargo_registry_metadata_mutation_fails_closed(self) -> None:
        value = self.source_document()
        value["cargo_bundles"][0]["package_count"] -= 1
        self.write_source_document(value)

        with self.assertRaisesRegex(ValueError, "metadata differs"):
            GENERATOR.check_payloads()

    def test_cargo_registry_template_mutation_fails_closed(self) -> None:
        value = self.source_document()
        value["cargo_bundles"][0][
            "url_template"
        ] = "https://example.com/{name}/{version}"
        self.write_source_document(value)

        with self.assertRaisesRegex(ValueError, "metadata differs"):
            GENERATOR.check_payloads()

    def test_cargo_legal_files_reject_empty_and_pointer_payloads(self) -> None:
        self.assertFalse(GENERATOR.legal_file_is_usable(b""))
        self.assertFalse(GENERATOR.legal_file_is_usable(b"../LICENSE\n"))
        self.assertFalse(GENERATOR.legal_file_is_usable(b"LICENSE-MIT\n"))
        self.assertTrue(
            GENERATOR.legal_file_is_usable(
                b"Copyright holder\nPermission is hereby granted to use this work.\n"
            )
        )

    def test_cargo_supplement_registry_is_exact_and_unique(self) -> None:
        supplements = GENERATOR.load_cargo_supplements()
        self.assertEqual(17, len(supplements))
        self.assertIn(
            (
                "plotters",
                "0.3.5",
                "d2c224ba00d7cadd4d5c660deaf2098e5e80e07846537c51f9cfa4be50c1fd45",
            ),
            supplements,
        )

    def test_cargo_supplement_metadata_mutation_fails_closed(self) -> None:
        for field in (
            "checksum",
            "evidence",
            "name",
            "payload_sha256",
            "repository_commit",
            "version",
        ):
            with self.subTest(field=field):
                value = self.source_document()
                supplement = value["cargo_supplements"][0]
                if field in {"checksum", "payload_sha256"}:
                    supplement[field] = "0" * 64
                elif field == "repository_commit":
                    supplement[field] = "0" * 40
                else:
                    supplement[field] += " changed"
                self.write_source_document(value)
                with self.assertRaisesRegex(ValueError, "supplement"):
                    GENERATOR.check_payloads()

    def test_cargo_supplement_source_mutation_fails_closed(self) -> None:
        value = self.source_document()
        value["cargo_supplements"][0]["sources"][0]["sha256"] = "0" * 64
        self.write_source_document(value)

        with self.assertRaisesRegex(ValueError, "supplement"):
            GENERATOR.check_payloads()

    def test_manifest_hash_mutation_fails_closed(self) -> None:
        path = self.output_root / "prettier" / "manifest.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["entries"][0]["payload_sha256"] = "0" * 64
        path.write_bytes(GENERATOR.render_manifest(value))

        with self.assertRaisesRegex(ValueError, "manifest differs"):
            GENERATOR.check_payloads()

    def test_manifest_metadata_mutation_fails_closed(self) -> None:
        path = self.output_root / "prettier" / "manifest.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["entries"][0]["component"] = "changed"
        path.write_bytes(GENERATOR.render_manifest(value))

        with self.assertRaisesRegex(ValueError, "manifest differs"):
            GENERATOR.check_payloads()

    def test_manifest_noncanonical_bytes_fail_closed(self) -> None:
        path = self.output_root / "prettier" / "manifest.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "not canonical"):
            GENERATOR.check_payloads()

    def test_extra_and_missing_targets_fail_closed(self) -> None:
        shutil.rmtree(self.output_root / "xml")
        with self.assertRaisesRegex(ValueError, "target set differs"):
            GENERATOR.check_payloads()

        shutil.copytree(ROOT / "images" / "licenses" / "xml", self.output_root / "xml")
        (self.output_root / "extra").mkdir()
        with self.assertRaisesRegex(ValueError, "target set differs"):
            GENERATOR.check_payloads()

    def test_extra_and_missing_files_fail_closed(self) -> None:
        extra = self.output_root / "prettier" / "extra.txt"
        extra.write_text("extra\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "files differ"):
            GENERATOR.check_payloads()

        extra.unlink()
        (self.output_root / "prettier" / "prettier-LICENSE").unlink()
        with self.assertRaisesRegex(ValueError, "not a regular file"):
            GENERATOR.check_payloads()

    def test_path_traversal_fails_closed(self) -> None:
        value = self.source_document()
        value["sources"][0]["output"] = "../outside"
        self.write_source_document(value)

        with self.assertRaisesRegex(ValueError, "output is unsafe"):
            GENERATOR.load_sources()

    def test_archive_member_path_traversal_fails_closed(self) -> None:
        value = self.source_document()
        value["archive_bundles"][0]["members"] = ["../outside"]
        self.write_source_document(value)

        with self.assertRaisesRegex(ValueError, "member is unsafe"):
            GENERATOR.load_archive_bundles()

    def test_unexpected_schema_fields_fail_closed(self) -> None:
        value = self.source_document()
        value["unexpected"] = True
        self.write_source_document(value)

        with self.assertRaisesRegex(ValueError, "unexpected fields"):
            GENERATOR.load_sources()

    def test_cargo_unexpected_schema_fields_fail_closed(self) -> None:
        value = self.source_document()
        value["cargo_bundles"][0]["unexpected"] = True
        self.write_source_document(value)

        with self.assertRaisesRegex(ValueError, "unexpected fields"):
            GENERATOR.load_cargo_bundles()

    def test_archive_unexpected_schema_fields_fail_closed(self) -> None:
        value = self.source_document()
        value["archive_bundles"][0]["unexpected"] = True
        self.write_source_document(value)

        with self.assertRaisesRegex(ValueError, "unexpected fields"):
            GENERATOR.load_archive_bundles()

    def test_live_fetch_mismatch_preserves_existing_payloads(self) -> None:
        sentinel = self.output_root / "sentinel"
        sentinel.write_text("keep\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "checksum differs"):
            GENERATOR.write_payloads(lambda _url: b"wrong")
        self.assertEqual("keep\n", sentinel.read_text(encoding="utf-8"))

    def test_archive_fetch_mismatch_preserves_existing_payloads(self) -> None:
        sentinel = self.output_root / "sentinel"
        sentinel.write_text("keep\n", encoding="utf-8")
        bundle = GENERATOR.load_archive_bundles()[0]

        with (
            mock.patch.object(GENERATOR, "load_sources", return_value=[]),
            mock.patch.object(
                GENERATOR,
                "load_archive_bundles",
                return_value=[bundle],
            ),
            mock.patch.object(
                GENERATOR,
                "load_cargo_bundles",
                return_value=[],
            ),
        ):
            with self.assertRaisesRegex(ValueError, "checksum differs"):
                GENERATOR.write_payloads(lambda _url: b"wrong")
        self.assertEqual("keep\n", sentinel.read_text(encoding="utf-8"))

    def test_cargo_fetch_mismatch_preserves_existing_payloads(self) -> None:
        sentinel = self.output_root / "sentinel"
        sentinel.write_text("keep\n", encoding="utf-8")
        bundle = GENERATOR.load_cargo_bundles()[0]

        with (
            mock.patch.object(GENERATOR, "load_sources", return_value=[]),
            mock.patch.object(
                GENERATOR,
                "load_archive_bundles",
                return_value=[],
            ),
            mock.patch.object(
                GENERATOR,
                "load_cargo_bundles",
                return_value=[bundle],
            ),
        ):
            with self.assertRaisesRegex(ValueError, "checksum differs"):
                GENERATOR.write_payloads(lambda _url: b"wrong")
        self.assertEqual("keep\n", sentinel.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
