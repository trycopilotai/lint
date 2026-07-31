#!/usr/bin/env python3
"""HTTP service for the lint request schema."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Sequence

SCRIPT_DIRECTORY = str(Path(__file__).resolve().parent)
if SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, SCRIPT_DIRECTORY)
import lint


READ_MODES = frozenset({"check", "read-only", "readonly"})
WRITE_MODES = frozenset({"fix", "write", "apply"})


class RequestError(Exception):
    """A client request violates the service schema."""


def request_path(value: Any) -> Path:
    if not isinstance(value, str):
        raise RequestError("file path must be a string")
    if value == "":
        raise RequestError("file path must not be empty")
    if "\x00" in value:
        raise RequestError("file path must not contain NUL bytes")
    normalized = value.replace("\\", "/")
    path = Path(normalized)
    if path.is_absolute():
        raise RequestError(f"file path must be relative: {value}")
    if "" in path.parts:
        raise RequestError(f"file path has an empty segment: {value}")
    if "." in path.parts:
        raise RequestError(f"file path has a dot segment: {value}")
    if ".." in path.parts:
        raise RequestError(f"file path traverses its root: {value}")
    return path


def request_mode(value: Any) -> bool:
    if not isinstance(value, str):
        raise RequestError("mode must be a string")
    if value in READ_MODES:
        return False
    if value in WRITE_MODES:
        return True
    raise RequestError(f"unsupported mode: {value}")


def process_request(
    payload: Any,
    use_docker: bool = False,
) -> tuple[int, dict[str, Any]]:
    try:
        return 200, _process_request(payload, use_docker=use_docker)
    except RequestError as error:
        return 400, {
            "schema_version": 1,
            "status": "request_error",
            "message": str(error),
        }
    except lint.FormatterError as error:
        return 422, {
            "schema_version": 1,
            "status": "formatter_error",
            "message": str(error),
        }
    except (OSError, ValueError, KeyError) as error:
        return 500, {
            "schema_version": 1,
            "status": "internal_error",
            "message": str(error),
        }


def _process_request(
    payload: Any,
    use_docker: bool,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RequestError("request must be an object")
    policy = payload.get("policy", "default")
    if policy != "default":
        raise RequestError(f"unsupported policy: {policy}")
    write = request_mode(payload.get("mode", "read-only"))
    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        raise RequestError("files must be a list")

    request_limits = lint.limits()
    if len(raw_files) > request_limits["max_files"]:
        raise RequestError(f"request exceeds {request_limits['max_files']} files")

    total_size = 0
    decoded: list[tuple[Path, bytes]] = []
    seen: set[str] = set()
    for item in raw_files:
        if not isinstance(item, dict):
            raise RequestError("file entries must be objects")
        path = request_path(item.get("path"))
        normalized = path.as_posix()
        if normalized in seen:
            raise RequestError(f"duplicate file path: {normalized}")
        seen.add(normalized)
        encoded = item.get("input_bytes_base64")
        if not isinstance(encoded, str):
            raise RequestError(f"input_bytes_base64 must be a string: {normalized}")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise RequestError(
                f"input_bytes_base64 is invalid: {normalized}"
            ) from error
        if len(content) > request_limits["max_file_bytes"]:
            raise RequestError(
                f"{normalized} exceeds " f"{request_limits['max_file_bytes']} bytes"
            )
        total_size += len(content)
        if total_size > request_limits["max_request_bytes"]:
            raise RequestError(
                "decoded files exceed " f"{request_limits['max_request_bytes']} bytes"
            )
        decoded.append((path, content))

    requested_languages = payload.get("languages", [])
    if not isinstance(requested_languages, list):
        raise RequestError("languages must be a list")
    language_ids: set[str] = set()
    for language_id in requested_languages:
        if not isinstance(language_id, str):
            raise RequestError("language ids must be strings")
        language_ids.add(language_id)

    with tempfile.TemporaryDirectory(prefix="lint-request-") as directory:
        cwd = Path(directory)
        paths: list[Path] = []
        for relative_path, content in decoded:
            path = cwd / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            paths.append(path)

        results, skipped = lint.prepare_results(
            cwd=cwd,
            paths=paths,
            requested_languages=frozenset(language_ids),
            use_docker=use_docker,
        )

    files: list[dict[str, Any]] = []
    changed = 0
    for result in results:
        file_status = "clean"
        if result.changed:
            changed += 1
            file_status = "changed"
        item = {
            "path": result.relative_path,
            "language": result.language.id,
            "status": file_status,
        }
        if result.changed:
            item["output_bytes_base64"] = base64.b64encode(result.formatted).decode(
                "ascii"
            )
        files.append(item)
    for finding in skipped:
        files.append(finding.as_dict())
    files.sort(key=lambda item: item["path"])

    status = "clean"
    if changed:
        status = "changed"
    response_mode = "read-only"
    if write:
        response_mode = "write"
    return {
        "schema_version": 1,
        "policy": "default",
        "mode": response_mode,
        "status": status,
        "files": files,
        "summary": {
            "selected": len(results),
            "changed": changed,
            "skipped": len(skipped),
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "lint/0.1.4"
    use_docker = False

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self.send_json(200, {"status": "ok"})
            return
        if self.path == "/v1/languages":
            self.send_json(200, lint.load_manifest())
            return
        self.send_json(404, {"status": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/v1/lint":
            self.send_json(404, {"status": "not_found"})
            return
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            self.send_json(411, {"status": "content_length_required"})
            return
        try:
            length = int(content_length)
        except ValueError:
            self.send_json(400, {"status": "invalid_content_length"})
            return
        if length < 0:
            self.send_json(400, {"status": "invalid_content_length"})
            return
        maximum = lint.limits()["max_request_bytes"]
        if length > maximum:
            self.send_json(413, {"status": "request_too_large"})
            return
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            self.send_json(
                400,
                {"status": "invalid_json", "message": str(error)},
            )
            return
        status_code, response = process_request(
            payload,
            use_docker=self.use_docker,
        )
        self.send_json(status_code, response)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_json(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(prog="lint-service")
    argument_parser.add_argument("--host", default="127.0.0.1")
    argument_parser.add_argument("--port", default=8080, type=int)
    argument_parser.add_argument("--docker", "-d", action="store_true")
    return argument_parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    Handler.use_docker = arguments.docker
    server = ThreadingHTTPServer(
        (arguments.host, arguments.port),
        Handler,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
