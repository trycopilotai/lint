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


RELEASE_MATRIX = load_release_matrix()


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


if __name__ == "__main__":
    unittest.main()
