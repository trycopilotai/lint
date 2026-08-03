from __future__ import annotations

import base64
import http.client
import importlib.util
import json
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def load_service():
    specification = importlib.util.spec_from_file_location(
        "http_service_under_test",
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


class HttpServiceTest(unittest.TestCase):
    server: ThreadingHTTPServer
    thread: threading.Thread
    port: int

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), SERVICE.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        body = None
        headers: dict[str, str] = {}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            status = response.status
            decoded = json.loads(response.read())
        finally:
            connection.close()
        return status, decoded

    def lint_payload(self, mode: str) -> dict[str, Any]:
        content = base64.b64encode(b"zeta==1\nalpha==1\n").decode("ascii")
        return {
            "mode": mode,
            "policy_id": "default",
            "files": [
                {
                    "path": "fixture.unknown",
                    "language": "requirements",
                    "input_bytes_base64": content,
                }
            ],
        }

    def test_health_languages_and_unknown_route_contracts(self) -> None:
        status, health = self.request_json("GET", "/healthz")
        self.assertEqual(200, status)
        self.assertEqual({"status": "pass"}, health)

        status, languages = self.request_json("GET", "/v1/languages")
        self.assertEqual(200, status)
        self.assertEqual("default", languages["policy_id"])
        self.assertEqual(28, len(languages["languages"]))
        language_ids = {language["id"] for language in languages["languages"]}
        self.assertTrue({"css", "scss", "less"}.issubset(language_ids))

        status, missing = self.request_json("GET", "/missing")
        self.assertEqual(404, status)
        self.assertEqual({"status": "error", "diagnostics": []}, missing)

    def test_check_fix_and_alias_modes_preserve_results(self) -> None:
        expected = {
            "check": ("check", "fail"),
            "read-only": ("check", "fail"),
            "readonly": ("check", "fail"),
            "fix": ("fix", "pass"),
            "write": ("fix", "pass"),
            "apply": ("fix", "pass"),
        }
        for requested, result in expected.items():
            with self.subTest(mode=requested):
                status, response = self.request_json(
                    "POST",
                    "/v1/lint",
                    self.lint_payload(requested),
                )
                self.assertEqual(200, status)
                self.assertEqual(result[0], response["mode"])
                self.assertEqual(result[1], response["status"])
                self.assertEqual("changed", response["files"][0]["status"])
                self.assertTrue(response["files"][0]["changed"])
                self.assertIn(
                    "output_bytes_base64",
                    response["files"][0],
                )

    def test_structured_request_errors_reach_http_clients(self) -> None:
        payload = self.lint_payload("check")
        payload["files"][0]["path"] = "../outside.txt"
        status, response = self.request_json("POST", "/v1/lint", payload)

        self.assertEqual(400, status)
        self.assertEqual("error", response["status"])
        self.assertEqual("check", response["mode"])
        self.assertEqual("default", response["policy_id"])
        self.assertEqual("invalid_path", response["diagnostics"][0]["code"])

    def test_missing_content_length_is_rejected(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.putrequest("POST", "/v1/lint")
            connection.endheaders()
            response = connection.getresponse()
            status = response.status
            payload = json.loads(response.read())
        finally:
            connection.close()

        self.assertEqual(411, status)
        self.assertEqual(
            "missing_content_length",
            payload["diagnostics"][0]["code"],
        )


if __name__ == "__main__":
    unittest.main()
