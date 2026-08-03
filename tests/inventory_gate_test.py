"""Static coverage tests for the canonical image inventories."""

from __future__ import annotations

import importlib.util
import json
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from pathlib import PurePosixPath


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

# Git records one permission bit, so a checkout materializes a
# tracked file as 0666 or 0777 masked by the build host's
# umask. A host with a group-writable umask hands Docker 0664
# where the standard 0022 umask hands it 0644, `COPY` carries
# that host accident into the image, and a canonical inventory
# generated there records a mode no hosted build reproduces.
STANDARD_UMASK = 0o022
CHECKOUT_MODES = {
    "100644": f"{0o666 & ~STANDARD_UMASK:04o}",
    "100755": f"{0o777 & ~STANDARD_UMASK:04o}",
}


def context_copy_operands() -> list[list[str]]:
    """Return the operands of every mode-preserving context COPY."""

    text = IMAGES.DOCKERFILE_PATH.read_text(encoding="utf-8")
    joined = re.sub(r"\\\n\s*", " ", text)
    operands: list[list[str]] = []
    for line in joined.splitlines():
        instruction = line.strip()
        if not instruction.startswith("COPY "):
            continue
        words = shlex.split(instruction)[1:]
        flags = [word for word in words if word.startswith("--")]
        if any(flag.startswith(("--from=", "--chmod=")) for flag in flags):
            continue
        operands.append([word for word in words if not word.startswith("--")])
    return operands


def git_tracked_modes() -> dict[str, str] | None:
    """Return every tracked Git mode, or None outside a checkout."""

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
        ["git", "-C", str(ROOT), "ls-files", "-s", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    )
    modes: dict[str, str] = {}
    for record in completed.stdout.split(b"\0"):
        if record == b"":
            continue
        metadata, path = record.decode("utf-8").split("\t", 1)
        modes[path] = metadata.split()[0]
    return modes


def context_copied_modes() -> dict[str, str] | None:
    """Map each context-copied image path to its checkout mode."""

    tracked = git_tracked_modes()
    if tracked is None:
        return None
    copied: dict[str, str] = {}
    for operands in context_copy_operands():
        *sources, destination = operands
        for source in sources:
            image_path = destination.lstrip("/")
            if source in tracked:
                mode = CHECKOUT_MODES.get(tracked[source])
                if mode is None:
                    continue
                if image_path.endswith("/"):
                    image_path = image_path + PurePosixPath(source).name
                copied[image_path] = mode
                continue
            prefix = source.rstrip("/") + "/"
            for path, tracked_mode in tracked.items():
                if not path.startswith(prefix):
                    continue
                mode = CHECKOUT_MODES.get(tracked_mode)
                if mode is None:
                    continue
                member = path[len(prefix) :]
                copied[image_path.rstrip("/") + "/" + member] = mode
    return copied


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


class BuildContextModeTest(unittest.TestCase):
    """Keep the generating host's umask out of the inventories."""

    def test_context_copied_entries_use_the_standard_checkout_mode(self) -> None:
        expected = context_copied_modes()
        if expected is None:
            self.skipTest("tracked Git modes are unavailable")
        for row in IMAGES.image_rows():
            target = row["target"]
            for architecture in sorted(IMAGES.CANONICAL_INVENTORY_ARCHITECTURES):
                inventory = IMAGES.load_inventory(target, architecture)
                entries = {entry["path"]: entry for entry in inventory["entries"]}
                covered = set(IMAGES.REQUIRED_LICENSE_PATHS)
                covered.add(f"licenses/{target}/manifest.json")
                for path in sorted(covered):
                    with self.subTest(target=target, path=path):
                        self.assertIn(path, expected)
                        self.assertIn(path, entries)
                for path, entry in sorted(entries.items()):
                    mode = expected.get(path)
                    if mode is None:
                        continue
                    with self.subTest(target=target, path=path):
                        self.assertEqual(
                            mode,
                            entry["mode"],
                            f"{target} records a mode the tracked "
                            f"{path} source does not check out as",
                        )


if __name__ == "__main__":
    unittest.main()
