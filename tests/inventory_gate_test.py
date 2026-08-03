"""Static coverage tests for the canonical image inventories."""

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
    if specification is None or specification.loader is None:
        raise RuntimeError(f"could not load {relative_path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


REPOSITORY = load_module("tools/verify_repo.py", "inventory_repository_verifier")
IMAGES = load_module("images/verify_images.py", "inventory_image_verifier")


class InventoryGateTest(unittest.TestCase):
    def write_inventory_set(self, root: Path) -> list[Path]:
        matrix = json.loads(
            (ROOT / "images" / "matrix.json").read_text(encoding="utf-8")
        )
        paths: list[Path] = []
        for row in matrix["images"]:
            for architecture in sorted(IMAGES.CANONICAL_INVENTORY_ARCHITECTURES):
                value = {
                    "architecture": architecture,
                    "entries": [],
                    "schema_version": IMAGES.INVENTORY_SCHEMA_VERSION,
                    "target": row["target"],
                }
                path = root / f"{row['target']}-{architecture}.json"
                path.write_bytes(IMAGES.render_inventory(value))
                paths.append(path)
        return paths

    def test_exact_fifteen_file_set_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self.write_inventory_set(root)
            self.assertEqual(15, len(paths))
            REPOSITORY.verify_inventory_set(IMAGES, root)

    def test_missing_and_extra_inventory_files_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self.write_inventory_set(root)
            paths[0].unlink()
            with self.assertRaisesRegex(ValueError, "inventory set differs"):
                REPOSITORY.verify_inventory_set(IMAGES, root)
            paths[0].write_text("{}\n", encoding="utf-8")
            (root / "unexpected.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "inventory set differs"):
                REPOSITORY.verify_inventory_set(IMAGES, root)

    def test_inventory_identity_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self.write_inventory_set(root)
            value = json.loads(paths[0].read_text(encoding="utf-8"))
            value["target"] = "black"
            paths[0].write_bytes(IMAGES.render_inventory(value))
            with self.assertRaisesRegex(ValueError, "identity differs"):
                REPOSITORY.verify_inventory_set(IMAGES, root)


if __name__ == "__main__":
    unittest.main()
