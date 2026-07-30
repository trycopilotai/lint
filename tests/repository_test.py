from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_release_matrix():
    path = ROOT / "tools" / "release_matrix.py"
    specification = importlib.util.spec_from_file_location(
        "release_matrix_under_test",
        path,
    )
    if specification is None:
        raise RuntimeError("could not create release matrix specification")
    if specification.loader is None:
        raise RuntimeError("release matrix specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def load_image_verifier():
    path = ROOT / "images" / "verify_images.py"
    specification = importlib.util.spec_from_file_location(
        "image_verifier_under_test",
        path,
    )
    if specification is None:
        raise RuntimeError("could not create image verifier specification")
    if specification.loader is None:
        raise RuntimeError("image verifier specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


RELEASE_MATRIX = load_release_matrix()
IMAGE_VERIFIER = load_image_verifier()


class ReleaseMatrixTest(unittest.TestCase):
    def test_every_language_has_one_release_row(self) -> None:
        rows = RELEASE_MATRIX.release_rows()
        languages = [row["language"] for row in rows]

        self.assertEqual(26, len(rows))
        self.assertEqual(26, len(set(languages)))

    def test_evidenced_runtime_budgets_are_preserved(self) -> None:
        rows = RELEASE_MATRIX.release_rows()
        budgets = {row["language"]: row["budget_mib"] for row in rows}

        self.assertEqual(90, budgets["rust"])
        self.assertEqual(140, budgets["kotlin"])
        self.assertEqual(360, budgets["julia"])


class ImageContentsTest(unittest.TestCase):
    def test_prettier_image_ignores_repository_configuration(self) -> None:
        dockerfile = (ROOT / "images" / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("--no-config", dockerfile)
        self.assertIn("--no-editorconfig", dockerfile)

    def test_shells_package_managers_and_compilers_are_forbidden(self) -> None:
        paths = [
            "bin/ash",
            "usr/bin/dpkg",
            "usr/bin/cc",
            "usr/local/go/bin/go",
        ]

        for path in paths:
            with self.subTest(path=path):
                self.assertTrue(IMAGE_VERIFIER.forbidden_executable(path))

    def test_formatter_executables_are_accepted(self) -> None:
        paths = [
            "gofmt",
            "usr/lib/llvm18/bin/clang-format",
            "usr/bin/swift-format",
        ]

        for path in paths:
            with self.subTest(path=path):
                self.assertFalse(IMAGE_VERIFIER.forbidden_executable(path))


if __name__ == "__main__":
    unittest.main()
