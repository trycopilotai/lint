"""Fail-closed tests for typed dependency-closure receipts."""

from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "verify_dependency_closures.py"
SPEC = importlib.util.spec_from_file_location(
    "dependency_closure_verifier_under_test",
    MODULE_PATH,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load dependency closure verifier")
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


class DependencyClosureTest(unittest.TestCase):
    def document(self) -> dict:
        return VERIFIER.load_document()

    def test_checked_in_receipts_and_mappings_are_complete(self) -> None:
        VERIFIER.validate_document(self.document())

    def test_every_family_rejects_a_missing_mapping(self) -> None:
        for name in sorted(VERIFIER.EXPECTED_CLOSURES):
            with self.subTest(name=name):
                value = copy.deepcopy(self.document())
                value["closures"][name]["mappings"].pop()
                with self.assertRaises(ValueError):
                    VERIFIER.validate_document(value)

    def test_every_family_rejects_an_extra_mapping(self) -> None:
        for name in sorted(VERIFIER.EXPECTED_CLOSURES):
            with self.subTest(name=name):
                value = copy.deepcopy(self.document())
                mapping = copy.deepcopy(value["closures"][name]["mappings"][-1])
                mapping["match"] += "-not-in-receipt"
                value["closures"][name]["mappings"].append(mapping)
                with self.assertRaises(ValueError):
                    VERIFIER.validate_document(value)

    def test_every_family_rejects_a_duplicate_mapping(self) -> None:
        for name in sorted(VERIFIER.EXPECTED_CLOSURES):
            with self.subTest(name=name):
                value = copy.deepcopy(self.document())
                mapping = copy.deepcopy(value["closures"][name]["mappings"][-1])
                value["closures"][name]["mappings"].append(mapping)
                with self.assertRaises(ValueError):
                    VERIFIER.validate_document(value)

    def test_every_family_rejects_version_drift(self) -> None:
        for name in sorted(VERIFIER.EXPECTED_CLOSURES):
            if name in {"cargo", "csharpier"}:
                continue
            with self.subTest(name=name):
                value = copy.deepcopy(self.document())
                mapping = value["closures"][name]["mappings"][-1]
                mapping["match"] += ".drift"
                with self.assertRaises(ValueError):
                    VERIFIER.validate_document(value)

    def test_every_path_bound_receipt_rejects_hash_drift(self) -> None:
        value = self.document()
        for name, closure in sorted(value["closures"].items()):
            if "source_sha256" not in closure:
                continue
            with self.subTest(name=name):
                changed = copy.deepcopy(value)
                changed["closures"][name]["source_sha256"] = "0" * 64
                with self.assertRaises(ValueError):
                    VERIFIER.validate_document(changed)

    def test_go_compiled_module_receipt_rejects_mutations(self) -> None:
        for name in ("go_buildifier", "go_shfmt"):
            for mutation in ("missing", "extra", "duplicate"):
                with self.subTest(name=name, mutation=mutation):
                    value = copy.deepcopy(self.document())
                    modules = value["closures"][name]["receipt"]["modules"]
                    if mutation == "missing":
                        modules.pop()
                    elif mutation == "extra":
                        modules.append(
                            {
                                "path": "example.invalid/module",
                                "sum": "h1:not-a-real-module-sum",
                                "version": "v0.0.0",
                            }
                        )
                    else:
                        modules.append(copy.deepcopy(modules[-1]))
                    with self.assertRaises(ValueError):
                        VERIFIER.validate_document(value)

    def test_ktlint_runtime_classpath_rejects_mutations(self) -> None:
        for mutation in ("missing", "extra", "duplicate"):
            with self.subTest(mutation=mutation):
                value = copy.deepcopy(self.document())
                components = value["closures"]["ktlint"]["receipt"]["components"]
                if mutation == "missing":
                    components.pop()
                elif mutation == "extra":
                    components.append("example.invalid:artifact:0")
                else:
                    components.append(components[-1])
                with self.assertRaises(ValueError):
                    VERIFIER.validate_document(value)

    def test_download_binding_rejects_unknown_artifact(self) -> None:
        value = copy.deepcopy(self.document())
        value["closures"]["ktlint"]["source_download"] = "missing"
        with self.assertRaises(ValueError):
            VERIFIER.validate_document(value)

    def test_source_module_binding_rejects_unknown_module(self) -> None:
        value = copy.deepcopy(self.document())
        value["closures"]["go_shfmt"]["source_module"] = "missing"
        with self.assertRaises(ValueError):
            VERIFIER.validate_document(value)


if __name__ == "__main__":
    unittest.main()
