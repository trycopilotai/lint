from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "http" / "compatibility.json"
sys.path.insert(0, str(ROOT))


def load_service():
    specification = importlib.util.spec_from_file_location(
        "compatibility_service_under_test",
        ROOT / "service.py",
    )
    if specification is None:
        raise RuntimeError("could not create service module specification")
    if specification.loader is None:
        raise RuntimeError("service module specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


SERVICE = load_service()


def stable_file(item: dict[str, Any]) -> dict[str, Any]:
    stable = {
        "path": item["path"],
        "status": item["status"],
        "language": item["language"],
        "changed": item["changed"],
        "diagnostics": item["diagnostics"],
    }
    if "output_bytes_base64" in item:
        stable["output_bytes_base64"] = item["output_bytes_base64"]
    return stable


def stable_response(response: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": response["status"],
        "mode": response["mode"],
        "policy_id": response["policy_id"],
        "files": [stable_file(item) for item in response["files"]],
        "diagnostics": response["diagnostics"],
    }


def process_case(case: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    fault = case.get("fault")
    if fault is None:
        return SERVICE.process_request(case["request"])
    if fault == "engine":
        error = SERVICE.lint.EngineError("formatter engine unavailable")
        with mock.patch.object(
            SERVICE.lint,
            "prepare_results",
            side_effect=error,
        ):
            return SERVICE.process_request(case["request"])
    raise AssertionError(f"unknown compatibility fault: {fault}")


class HttpCompatibilityTest(unittest.TestCase):
    def test_frozen_content_api_contract(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(1, fixture["schema_version"])
        for case in fixture["cases"]:
            with self.subTest(case=case["name"]):
                status_code, response = process_case(case)
                self.assertEqual(case["status_code"], status_code)
                self.assertEqual(fixture["limits"], response["limits"])
                self.assertEqual(case["response"], stable_response(response))

    def test_exact_file_count_limit(self) -> None:
        files = []
        for index in range(65):
            files.append(
                {
                    "path": f"file-{index}.txt",
                    "input_bytes_base64": "",
                }
            )
        status_code, response = SERVICE.process_request(
            {
                "mode": "check",
                "policy_id": "default",
                "files": files,
            }
        )

        self.assertEqual(400, status_code)
        self.assertEqual("too_many_files", response["diagnostics"][0]["code"])


if __name__ == "__main__":
    unittest.main()
