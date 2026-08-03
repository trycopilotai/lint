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
        paths.add(manifest_path.relative_to(ROOT).as_posix())
        manifest = load_json(manifest_path)
        for entry in manifest["entries"]:
            relative = Path(entry["path"])
            payload_path = manifest_path.parent / relative
            paths.add(payload_path.relative_to(ROOT).as_posix())

    comparisons = load_json(ROOT / "evidence" / "comparison-sources.json")
    for source in comparisons["sources"]:
        paths.add(source["capture"]["context_path"])

    paths.add("images/matrix.json")

    for path in sorted((ROOT / "skills" / "lint").rglob("*")):
        if "__pycache__" in path.parts:
            continue
        if path.is_file():
            paths.add(path.relative_to(ROOT).as_posix())

    for path in sorted((ROOT / "fixtures").rglob("*")):
        if path.is_file():
            paths.add(path.relative_to(ROOT).as_posix())

    for path in sorted((ROOT / "images" / "inventories").glob("*.json")):
        paths.add(path.relative_to(ROOT).as_posix())

    return paths


def tracked_paths() -> set[str] | None:
    root_result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if root_result.returncode != 0:
        return None
    if Path(root_result.stdout.strip()).resolve() != ROOT.resolve():
        return None
    completed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    )
    return {
        record.decode("utf-8")
        for record in completed.stdout.split(b"\0")
        if record != b""
    }


def text_attributes(paths: set[str]) -> dict[str, str]:
    encoded = b"\0".join(path.encode("utf-8") for path in sorted(paths)) + b"\0"
    completed = subprocess.run(
        ["git", "-C", str(ROOT), "check-attr", "-z", "--stdin", "text"],
        check=True,
        input=encoded,
        stdout=subprocess.PIPE,
    )
    fields = completed.stdout.split(b"\0")
    if fields[-1] == b"":
        fields.pop()
    if len(fields) % 3 != 0:
        raise ValueError("git check-attr returned incomplete fields")

    attributes: dict[str, str] = {}
    for index in range(0, len(fields), 3):
        path = fields[index].decode("utf-8")
        attribute = fields[index + 1].decode("utf-8")
        value = fields[index + 2].decode("utf-8")
        if attribute != "text":
            raise ValueError(f"git check-attr returned {attribute} for {path}")
        attributes[path] = value
    return attributes


class CheckoutIntegrityTest(unittest.TestCase):
    def test_exact_byte_paths_are_tracked_regular_files(self) -> None:
        tracked = tracked_paths()
        if tracked is None:
            self.skipTest("tracked Git paths are unavailable")
        paths = exact_byte_paths()
        self.assertGreater(len(paths), 300)
        for path in paths:
            with self.subTest(path=path):
                self.assertIn(path, tracked)
                self.assertTrue((ROOT / path).is_file())
                self.assertFalse((ROOT / path).is_symlink())

    def test_exact_byte_paths_disable_checkout_translation(self) -> None:
        tracked = tracked_paths()
        if tracked is None:
            self.skipTest("tracked Git attributes are unavailable")
        paths = exact_byte_paths()
        attributes = text_attributes(paths)
        self.assertEqual(paths, set(attributes))
        translated = sorted(
            path for path, value in attributes.items() if value != "unset"
        )
        self.assertEqual(
            [],
            translated,
            "exact-byte paths permit checkout byte translation",
        )

    def test_no_other_path_disables_checkout_translation(self) -> None:
        tracked = tracked_paths()
        if tracked is None:
            self.skipTest("tracked Git attributes are unavailable")
        attributes = text_attributes(tracked)
        exempt = {path for path, value in attributes.items() if value == "unset"}
        self.assertEqual(exact_byte_paths(), exempt)


if __name__ == "__main__":
    unittest.main()
