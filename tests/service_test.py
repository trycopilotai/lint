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
    def test_read_only_alias_returns_wire_compatible_check_result(self) -> None:
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

        self.assertEqual(2, response["schema_version"])
        self.assertEqual("check", response["mode"])
        self.assertEqual("local", response["backend"])
        self.assertEqual("fail", response["status"])
        self.assertEqual("changed", response["files"][0]["status"])
        self.assertEqual("requirements", response["files"][0]["runner_id"])
        self.assertEqual(
            "ghcr.io/trycopilotai/lint-requirements:0.1.5",
            response["files"][0]["image"],
        )
        self.assertEqual(
            {"requirements-sort": "0.1.5"},
            response["files"][0]["tool_versions"],
        )
        self.assertTrue(response["files"][0]["changed"])
        self.assertEqual([], response["files"][0]["diagnostics"])
        self.assertEqual("default", response["policy_id"])
        self.assertEqual("default", response["policy"])
        self.assertEqual([], response["diagnostics"])
        self.assertEqual(SERVICE.lint.limits(), response["limits"])
        self.assertEqual(1, response["summary"]["would_change"])
        self.assertNotIn("changed", response["summary"])
        output = response["files"][0]["output_bytes_base64"]
        self.assertEqual(b"alpha==1\nzeta==1\n", base64.b64decode(output))

    def test_write_alias_is_accepted(self) -> None:
        code, response = SERVICE.process_request(
            {
                "mode": "apply",
                "policy_id": "default",
                "files": [
                    {
                        "path": "requirements.txt",
                        "input_bytes_base64": encoded(b"alpha==1\n"),
                    }
                ],
            }
        )

        self.assertEqual(200, code)
        self.assertEqual("fix", response["mode"])

    def test_write_result_preserves_the_content_api_status_vocabulary(self) -> None:
        code, response = SERVICE.process_request(
            {
                "mode": "write",
                "policy_id": "default",
                "files": [
                    {
                        "path": "requirements.txt",
                        "input_bytes_base64": encoded(b"zeta==1\nalpha==1\n"),
                    }
                ],
            }
        )

        self.assertEqual(200, code)
        self.assertEqual(2, response["schema_version"])
        self.assertEqual("local", response["backend"])
        self.assertEqual("pass", response["status"])
        self.assertEqual("changed", response["files"][0]["status"])
        self.assertEqual(1, response["summary"]["changed"])
        self.assertNotIn("would_change", response["summary"])

    def test_parent_traversal_is_rejected(self) -> None:
        code, response = SERVICE.process_request(
            {
                "mode": "check",
                "policy_id": "default",
                "files": [
                    {
                        "path": "../outside.txt",
                        "input_bytes_base64": encoded(b"outside\n"),
                    }
                ],
            }
        )

        self.assertEqual(400, code)
        self.assertEqual("error", response["status"])
        self.assertEqual("invalid_path", response["diagnostics"][0]["code"])

    def test_noncanonical_and_overlong_paths_are_rejected(self) -> None:
        paths = ("a//b.json", "a/./b.json", f"{'a' * 509}.json")
        for path in paths:
            with self.subTest(path=path):
                code, response = SERVICE.process_request(
                    {
                        "mode": "check",
                        "policy_id": "default",
                        "files": [
                            {
                                "path": path,
                                "input_bytes_base64": encoded(b"{}\n"),
                            }
                        ],
                    }
                )

                self.assertEqual(400, code)
                self.assertEqual("error", response["status"])
                self.assertEqual(
                    "invalid_path",
                    response["diagnostics"][0]["code"],
                )

    def test_invalid_base64_is_rejected(self) -> None:
        code, response = SERVICE.process_request(
            {
                "mode": "check",
                "policy_id": "default",
                "files": [
                    {
                        "path": "requirements.txt",
                        "input_bytes_base64": "not base64",
                    }
                ],
            }
        )

        self.assertEqual(400, code)
        self.assertEqual("error", response["status"])

    def test_duplicate_paths_are_rejected(self) -> None:
        item = {
            "path": "requirements.txt",
            "input_bytes_base64": encoded(b"alpha==1\n"),
        }
        code, response = SERVICE.process_request(
            {
                "mode": "check",
                "policy_id": "default",
                "files": [item, dict(item)],
            }
        )

        self.assertEqual(400, code)
        self.assertEqual("error", response["status"])

    def test_policy_aliases_must_not_conflict(self) -> None:
        code, response = SERVICE.process_request(
            {
                "mode": "check",
                "policy": "default",
                "policy_id": "other",
                "files": [],
            }
        )

        self.assertEqual(400, code)
        self.assertEqual("unsupported_policy", response["diagnostics"][0]["code"])

    def test_request_errors_preserve_valid_mode_and_unsupported_policy(self) -> None:
        code, response = SERVICE.process_request(
            {
                "mode": "read-only",
                "policy_id": "other",
                "files": [],
            }
        )

        self.assertEqual(400, code)
        self.assertEqual("check", response["mode"])
        self.assertEqual("other", response["policy_id"])

    def test_omitted_mode_and_policy_preserve_required_wire_fields(self) -> None:
        code, response = SERVICE.process_request({"files": []})

        self.assertEqual(400, code)
        self.assertEqual("error", response["status"])
        self.assertEqual("unknown", response["mode"])
        self.assertEqual("default", response["policy_id"])
        self.assertEqual(
            "unsupported_policy",
            response["diagnostics"][0]["code"],
        )

    def test_result_paths_echo_the_request_spelling(self) -> None:
        code, response = SERVICE.process_request(
            {
                "mode": "check",
                "policy_id": "default",
                "files": [
                    {
                        "path": "nested\\requirements.txt",
                        "input_bytes_base64": encoded(b"alpha==1\n"),
                    }
                ],
            }
        )

        self.assertEqual(200, code)
        self.assertEqual("nested\\requirements.txt", response["files"][0]["path"])

    def test_unknown_explicit_language_is_reported_as_unsupported(self) -> None:
        code, response = SERVICE.process_request(
            {
                "mode": "check",
                "policy_id": "default",
                "files": [
                    {
                        "path": "fixture.unknown",
                        "language": "unknown",
                        "input_bytes_base64": encoded(b"sample\n"),
                    }
                ],
            }
        )

        self.assertEqual(200, code)
        self.assertEqual("fail", response["status"])
        self.assertEqual("unsupported", response["files"][0]["status"])
        self.assertEqual(
            "unsupported_language",
            response["files"][0]["diagnostics"][0]["code"],
        )

    def test_check_fix_policy_id_and_explicit_language_are_compatible(self) -> None:
        code, response = SERVICE.process_request(
            {
                "mode": "check",
                "policy_id": "default",
                "files": [
                    {
                        "path": "fixture.unknown",
                        "language": "requirements",
                        "input_bytes_base64": encoded(b"zeta==1\nalpha==1\n"),
                    }
                ],
            }
        )

        self.assertEqual(200, code)
        self.assertEqual("check", response["mode"])
        self.assertEqual("default", response["policy_id"])
        self.assertEqual("fail", response["status"])
        self.assertEqual("requirements", response["files"][0]["language"])
        self.assertEqual("changed", response["files"][0]["status"])
        self.assertTrue(response["files"][0]["changed"])

    def test_languages_response_preserves_policy_limits_and_runtime_fields(
        self,
    ) -> None:
        response = SERVICE.languages_response()

        self.assertEqual("default", response["policy_id"])
        self.assertEqual(["default"], response["policy_ids"])
        self.assertEqual(SERVICE.lint.limits(), response["limits"])
        self.assertEqual(28, len(response["languages"]))
        for language in response["languages"]:
            self.assertIn("runner_id", language)
            self.assertIn("image", language)
            self.assertIn("image_target", language)
            self.assertIn("tool_versions", language)
        requirements = next(
            language
            for language in response["languages"]
            if language["id"] == "requirements"
        )
        self.assertEqual("requirements", requirements["image_target"])
        prettier_languages = {
            language["id"]
            for language in response["languages"]
            if language["image_target"] == "prettier"
        }
        self.assertTrue({"css", "scss", "less"}.issubset(prettier_languages))
        languages_by_id = {
            language["id"]: language for language in response["languages"]
        }
        self.assertEqual(
            "ghcr.io/trycopilotai/lint-scss:0.1.5",
            languages_by_id["scss"]["image"],
        )
        self.assertEqual(
            "ghcr.io/trycopilotai/lint-less:0.1.5",
            languages_by_id["less"]["image"],
        )

    def test_health_and_body_limit_preserve_the_content_api_contract(self) -> None:
        self.assertEqual({"status": "pass"}, SERVICE.health_response())
        self.assertGreater(
            SERVICE.max_request_body_bytes(),
            SERVICE.lint.limits()["max_request_bytes"],
        )


if __name__ == "__main__":
    unittest.main()
