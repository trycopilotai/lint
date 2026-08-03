from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def exact_byte_paths() -> set[str]:
    paths: set[str] = set()

    demo = load_json(ROOT / "evidence" / "demo-manifest.json")
    paths.update(demo["source_sha256"])
    paths.update(demo["artifact_sha256"])

    closures = load_json(ROOT / "images" / "dependency_closures.json")
    for closure in closures["closures"].values():
        source_path = closure.get("source_path")
        if source_path is not None:
            paths.add(source_path)

    sources = load_json(ROOT / "images" / "sources.json")
    for lockfile in sources["lockfiles"].values():
        paths.add(lockfile["path"])

    license_root = ROOT / "images" / "licenses"
    for manifest_path in sorted(license_root.glob("*/manifest.json")):
        manifest = load_json(manifest_path)
        for entry in manifest["entries"]:
            relative = Path(entry["path"])
            payload_path = manifest_path.parent / relative
            paths.add(payload_path.relative_to(ROOT).as_posix())

    comparisons = load_json(ROOT / "evidence" / "comparison-sources.json")
    for source in comparisons["sources"]:
        paths.add(source["capture"]["context_path"])

    for path in sorted((ROOT / "fixtures").rglob("*")):
        if path.is_file():
            paths.add(path.relative_to(ROOT).as_posix())

    for path in sorted((ROOT / "images" / "inventories").glob("*.json")):
        paths.add(path.relative_to(ROOT).as_posix())

    return paths


class CheckoutIntegrityTest(unittest.TestCase):
    def test_exact_byte_paths_disable_checkout_translation(self) -> None:
        paths = exact_byte_paths()
        self.assertGreater(len(paths), 300)
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file())

        encoded = b"\0".join(path.encode("utf-8") for path in sorted(paths)) + b"\0"
        completed = subprocess.run(
            ["git", "-C", str(ROOT), "check-attr", "-z", "--stdin", "text"],
            check=True,
            input=encoded,
            stdout=subprocess.PIPE,
        )
        fields = completed.stdout.split(b"\0")
        self.assertEqual(b"", fields.pop())
        self.assertEqual(0, len(fields) % 3)

        attributes: dict[str, str] = {}
        for index in range(0, len(fields), 3):
            path = fields[index].decode("utf-8")
            attribute = fields[index + 1].decode("utf-8")
            value = fields[index + 2].decode("utf-8")
            self.assertEqual("text", attribute)
            attributes[path] = value

        self.assertEqual(paths, set(attributes))
        translated = sorted(
            path for path, value in attributes.items() if value != "unset"
        )
        self.assertEqual(
            [],
            translated,
            "exact-byte paths permit checkout byte translation",
        )


if __name__ == "__main__":
    unittest.main()
