from __future__ import annotations

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


RELEASE_MANIFEST = load_module(
    "tools/release_manifest.py",
    "release_manifest_under_test",
)
RELEASE_MATRIX = load_module(
    "tools/release_matrix.py",
    "release_matrix_for_manifest_test",
)


class ImageDigestSetTest(unittest.TestCase):
    def write_records(self, directory: Path, languages: list[str]) -> None:
        for index, language in enumerate(languages, start=1):
            record = {
                "digest": "sha256:" + f"{index:064x}",
                "image": f"ghcr.io/trycopilotai/lint-{language}",
                "staging_tag": "0.1.6-deadbeef",
            }
            (directory / f"{language}.json").write_text(
                json.dumps(record),
                encoding="utf-8",
            )

    def languages(self) -> list[str]:
        return [row["language"] for row in RELEASE_MATRIX.release_rows()]

    def test_complete_digest_set_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.write_records(directory, self.languages())

            digests = RELEASE_MANIFEST.image_digests(
                directory,
                version="0.1.6",
                revision="deadbeef",
            )

        self.assertEqual(28, len(digests))

    def test_missing_digest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.write_records(directory, self.languages()[:-1])

            with self.assertRaisesRegex(ValueError, "coverage differs"):
                RELEASE_MANIFEST.image_digests(
                    directory,
                    version="0.1.6",
                    revision="deadbeef",
                )

    def test_duplicate_image_record_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.write_records(directory, self.languages())
            first = directory / f"{self.languages()[0]}.json"
            duplicate = directory / "duplicate.json"
            duplicate.write_bytes(first.read_bytes())

            with self.assertRaisesRegex(ValueError, "duplicate image"):
                RELEASE_MANIFEST.image_digests(
                    directory,
                    version="0.1.6",
                    revision="deadbeef",
                )


if __name__ == "__main__":
    unittest.main()
