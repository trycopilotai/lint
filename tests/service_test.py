from __future__ import annotations

import base64
import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_service():
    specification = importlib.util.spec_from_file_location(
        "service_under_test",
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


def encoded(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


class ServiceTest(unittest.TestCase):
    def test_read_only_alias_returns_formatted_bytes(self) -> None:
        code, response = SERVICE.process_request(
            {
                "mode": "readonly",
                "policy": "default",
                "files": [
                    {
                        "path": "requirements.txt",
                        "input_bytes_base64": encoded(b"zeta==1\nalpha==1\n"),
                    }
                ],
            }
        )

        self.assertEqual(200, code)
        self.assertEqual("changed", response["status"])
        output = response["files"][0]["output_bytes_base64"]
        self.assertEqual(b"alpha==1\nzeta==1\n", base64.b64decode(output))

    def test_write_alias_is_accepted(self) -> None:
        code, response = SERVICE.process_request(
            {
                "mode": "apply",
                "files": [
                    {
                        "path": "requirements.txt",
                        "input_bytes_base64": encoded(b"alpha==1\n"),
                    }
                ],
            }
        )

        self.assertEqual(200, code)
        self.assertEqual("write", response["mode"])

    def test_parent_traversal_is_rejected(self) -> None:
        code, response = SERVICE.process_request(
            {
                "mode": "check",
                "files": [
                    {
                        "path": "../outside.txt",
                        "input_bytes_base64": encoded(b"outside\n"),
                    }
                ],
            }
        )

        self.assertEqual(400, code)
        self.assertEqual("request_error", response["status"])

    def test_invalid_base64_is_rejected(self) -> None:
        code, response = SERVICE.process_request(
            {
                "files": [
                    {
                        "path": "requirements.txt",
                        "input_bytes_base64": "not base64",
                    }
                ]
            }
        )

        self.assertEqual(400, code)
        self.assertEqual("request_error", response["status"])

    def test_duplicate_paths_are_rejected(self) -> None:
        item = {
            "path": "requirements.txt",
            "input_bytes_base64": encoded(b"alpha==1\n"),
        }
        code, response = SERVICE.process_request({"files": [item, dict(item)]})

        self.assertEqual(400, code)
        self.assertEqual("request_error", response["status"])


if __name__ == "__main__":
    unittest.main()
