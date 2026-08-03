#!/usr/bin/env python3
"""HTTP service for the lint content API."""

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


POLICY_ID = "default"
READ_MODES = frozenset({"check", "read-only", "readonly"})
WRITE_MODES = frozenset({"fix", "write", "apply"})
MAX_PATH_CHARACTERS = 512
FAMILY_TOOLS = {
    "prettier": ("prettier",),
    "buildifier": ("buildifier",),
    "black": ("black",),
    "requirements": (),
    "shfmt": ("shfmt",),
    "clang": ("clang-format",),
    "java": ("google-java-format",),
    "go": ("go",),
    "rust": ("rust",),
    "kotlin": ("ktlint",),
    "toml": ("taplo",),
    "xml": ("libxml2",),
    "swift": ("swift-format",),
    "csharp": ("csharpier",),
    "julia": ("julia", "juliaformatter"),
}


class RequestError(Exception):
    """A client request violates the service schema."""

    def __init__(self, message: str, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.code = code


def request_path(value: Any) -> Path:
    if not isinstance(value, str):
        raise RequestError("path must be a string", "invalid_path")
    if value == "":
        raise RequestError("path must not be empty", "invalid_path")
    if "\x00" in value:
        raise RequestError("path must not contain NUL bytes", "invalid_path")
    if len(value) > MAX_PATH_CHARACTERS:
        raise RequestError(
            f"path must not exceed {MAX_PATH_CHARACTERS} characters",
            "invalid_path",
        )

    normalized = value.replace("\\", "/")
    if normalized.startswith("/"):
        raise RequestError("path must be relative", "invalid_path")
    for part in normalized.split("/"):
        if part == "":
            raise RequestError(
                "path must not contain empty components",
                "invalid_path",
            )
        if part == ".":
            raise RequestError(
                "path must not contain . components",
                "invalid_path",
            )
        if part == "..":
            raise RequestError(
                "path must not contain .. components",
                "invalid_path",
            )
    return Path(normalized)


def request_mode(value: Any) -> tuple[bool, str]:
    if not isinstance(value, str):
        raise RequestError(
            'mode must be "check" or "fix"',
            "invalid_mode",
        )
    if value in READ_MODES:
        return False, "check"
    if value in WRITE_MODES:
        return True, "fix"
    raise RequestError(
        'mode must be "check" or "fix"',
        "invalid_mode",
    )


def request_policy(payload: dict[str, Any]) -> str:
    policy = payload.get("policy")
    policy_id = payload.get("policy_id")
    if policy is None and policy_id is None:
        raise RequestError(
            "policy_id is not supported",
            "unsupported_policy",
        )
    if policy is not None and not isinstance(policy, str):
        raise RequestError("policy must be a string", "unsupported_policy")
    if policy_id is not None and not isinstance(policy_id, str):
        raise RequestError("policy_id must be a string", "unsupported_policy")
    if policy is not None and policy_id is not None and policy != policy_id:
        raise RequestError("policy and policy_id must match", "unsupported_policy")

    selected = POLICY_ID
    if policy_id is not None:
        selected = policy_id
    elif policy is not None:
        selected = policy
    if selected != POLICY_ID:
        raise RequestError(f"unsupported policy_id: {selected}", "unsupported_policy")
    return selected


def diagnostic(message: str, code: str) -> dict[str, Any]:
    return {
        "severity": "error",
        "message": message,
        "code": code,
    }


def error_response(
    message: str,
    code: str,
    mode: str = "unknown",
    policy_id: str = POLICY_ID,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "status": "error",
        "mode": mode,
        "policy_id": policy_id,
        "policy": policy_id,
        "limits": lint.limits(),
        "files": [],
        "diagnostics": [diagnostic(message, code)],
        "message": message,
    }


def process_request(
    payload: Any,
    use_docker: bool = False,
) -> tuple[int, dict[str, Any]]:
    response_mode = "unknown"
    response_policy_id = POLICY_ID
    if isinstance(payload, dict):
        raw_mode = payload.get("mode")
        if isinstance(raw_mode, str):
            if raw_mode in READ_MODES:
                response_mode = "check"
            elif raw_mode in WRITE_MODES:
                response_mode = "fix"
        raw_policy_id = payload.get("policy_id")
        raw_policy = payload.get("policy")
        if isinstance(raw_policy_id, str):
            response_policy_id = raw_policy_id
        elif isinstance(raw_policy, str):
            response_policy_id = raw_policy
    try:
        response = _process_request(payload, use_docker=use_docker)
        status_code = 200
        if response["status"] == "error":
            status_code = 400
        return status_code, response
    except RequestError as error:
        return 400, error_response(
            str(error),
            error.code,
            mode=response_mode,
            policy_id=response_policy_id,
        )
    except lint.SelectionError as error:
        return 400, error_response(
            str(error),
            "invalid_request",
            mode=response_mode,
            policy_id=response_policy_id,
        )
    except lint.FormatterError as error:
        return 400, error_response(
            str(error),
            "formatter_error",
            mode=response_mode,
            policy_id=response_policy_id,
        )
    except (OSError, ValueError, KeyError) as error:
        return 500, error_response(
            str(error),
            "internal_error",
            mode=response_mode,
            policy_id=response_policy_id,
        )


def _process_request(
    payload: Any,
    use_docker: bool,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RequestError("request must be an object")
    policy_id = request_policy(payload)
    write, mode = request_mode(payload.get("mode"))
    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        raise RequestError("files must be a list", "invalid_files")

    request_limits = lint.limits()
    if len(raw_files) > request_limits["max_files"]:
        raise RequestError("request contains too many files", "too_many_files")

    known_languages = lint.load_languages()
    languages_by_id = {language.id: language for language in known_languages}
    known_language_ids = frozenset(languages_by_id)
    total_size = 0
    decoded: list[tuple[Path, str, bytes, Any]] = []
    seen: set[str] = set()
    for item in raw_files:
        if not isinstance(item, dict):
            raise RequestError("file entries must be objects", "invalid_file_entry")
        path = request_path(item.get("path"))
        normalized = path.as_posix()
        if normalized in seen:
            raise RequestError(f"duplicate file path: {normalized}")
        seen.add(normalized)
        encoded = item.get("input_bytes_base64")
        if not isinstance(encoded, str):
            raise RequestError(
                f"input_bytes_base64 must be a string: {normalized}",
                "invalid_base64",
            )
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise RequestError(
                f"input_bytes_base64 is invalid: {normalized}",
                "invalid_base64",
            ) from error
        if len(content) > request_limits["max_file_bytes"]:
            raise RequestError(
                f"{normalized} exceeds {request_limits['max_file_bytes']} bytes",
                "file_too_large",
            )
        total_size += len(content)
        if total_size > request_limits["max_request_bytes"]:
            raise RequestError(
                f"decoded files exceed {request_limits['max_request_bytes']} bytes",
                "request_too_large",
            )
        original_path = item["path"]
        decoded.append((path, original_path, content, item.get("language")))

    requested_languages = payload.get("languages", [])
    if not isinstance(requested_languages, list):
        raise RequestError("languages must be a list")
    language_ids: set[str] = set()
    for language_id in requested_languages:
        if not isinstance(language_id, str):
            raise RequestError("language ids must be strings")
        if language_id not in known_language_ids:
            raise RequestError(
                f"unsupported language: {language_id}",
                "unsupported_language",
            )
        language_ids.add(language_id)

    unsupported: list[dict[str, Any]] = []
    display_paths: dict[str, str] = {}
    failures: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="lint-request-") as directory:
        cwd = Path(directory)
        paths: list[Path] = []
        overrides: dict[Path, str] = {}
        contents: dict[Path, bytes] = {}
        for relative_path, original_path, content, explicit_language in decoded:
            path = cwd / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            display_paths[relative_path.as_posix()] = original_path
            if explicit_language is not None:
                if not isinstance(explicit_language, str):
                    unsupported.append(
                        unsupported_file(original_path, explicit_language)
                    )
                    continue
                if explicit_language not in known_language_ids:
                    unsupported.append(
                        unsupported_file(original_path, explicit_language)
                    )
                    continue
                overrides[path] = explicit_language
            paths.append(path)
            contents[path] = content

        results: list[lint.FormatResult] = []
        skipped: list[lint.Finding] = []
        for path in paths:
            language = language_for_path(
                path,
                overrides,
                known_languages,
                languages_by_id,
            )
            path_overrides: dict[Path, str] = {}
            if path in overrides:
                path_overrides[path] = overrides[path]
            try:
                path_results, path_skipped = lint.prepare_results(
                    cwd=cwd,
                    paths=[path],
                    requested_languages=frozenset(language_ids),
                    use_docker=use_docker,
                    language_overrides=path_overrides,
                )
            except lint.EngineError as error:
                failures.append(
                    failed_file(
                        display_paths[path.relative_to(cwd).as_posix()],
                        language,
                        "error",
                        [diagnostic(str(error), "runner_error")],
                    )
                )
                continue
            except lint.FormatterError as error:
                failures.append(
                    failed_file(
                        display_paths[path.relative_to(cwd).as_posix()],
                        language,
                        "fail",
                        formatter_diagnostics(
                            language,
                            contents[path],
                            error,
                        ),
                    )
                )
                continue
            results.extend(path_results)
            skipped.extend(path_skipped)

    files: list[dict[str, Any]] = []
    for result in results:
        files.append(result_file(result, display_paths[result.relative_path]))
    for finding in skipped:
        display_path = display_paths[finding.path]
        files.append(unsupported_file(display_path, None))
    files.extend(failures)
    files.extend(unsupported)
    files.sort(key=lambda item: item["path"])

    backend = "local"
    if use_docker:
        backend = "docker"
    changed = 0
    for result in results:
        if result.changed:
            changed += 1
    summary: dict[str, int] = {
        "selected": len(results) + len(failures),
        "skipped": len(skipped) + len(unsupported),
    }
    if write:
        summary["changed"] = changed
    else:
        summary["would_change"] = changed

    return {
        "schema_version": 2,
        "status": aggregate_status(files, mode),
        "mode": mode,
        "policy_id": policy_id,
        "policy": policy_id,
        "limits": request_limits,
        "backend": backend,
        "files": files,
        "diagnostics": [],
        "summary": summary,
    }


def language_for_path(
    path: Path,
    overrides: dict[Path, str],
    known_languages: Sequence[lint.Language],
    languages_by_id: dict[str, lint.Language],
) -> lint.Language | None:
    language_id = overrides.get(path)
    if language_id is not None:
        return languages_by_id[language_id]
    return lint.detect_language(path, known_languages)


def formatter_diagnostics(
    language: lint.Language | None,
    content: bytes,
    error: lint.FormatterError,
) -> list[dict[str, Any]]:
    if language is not None and language.id == "json":
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return [diagnostic("input is not valid UTF-8", "utf8_decode_error")]
        try:
            json.loads(text)
        except json.JSONDecodeError as parse_error:
            item = diagnostic(parse_error.msg, "json_parse_error")
            item["line"] = parse_error.lineno
            item["column"] = parse_error.colno
            return [item]
    return [diagnostic(str(error), "formatter_error")]


def failed_file(
    path: str,
    language: lint.Language | None,
    status: str,
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    if language is None:
        return unsupported_file(path, None)
    return {
        "path": path,
        "status": status,
        "language": language.id,
        "runner_id": language.family,
        "image": lint.docker_image(language),
        "tool_versions": tools_for_language(language),
        "changed": False,
        "diagnostics": diagnostics,
    }


def aggregate_status(files: Sequence[dict[str, Any]], mode: str) -> str:
    if not files:
        return "pass"

    has_error = False
    has_success = False
    has_failure = False
    for item in files:
        status = item.get("status")
        if status == "error":
            has_error = True
        if status == "pass":
            has_success = True
        elif status == "changed" and mode == "fix":
            has_success = True
        else:
            has_failure = True
    if has_error:
        return "error"
    if has_success and not has_failure:
        return "pass"
    if has_success and has_failure:
        return "partial"
    return "fail"


def tools_for_language(language: lint.Language) -> dict[str, str]:
    versions = lint.tool_versions()
    selected: dict[str, str] = {}
    for tool in FAMILY_TOOLS[language.family]:
        selected[tool] = versions[tool]
    if language.family == "requirements":
        selected["requirements-sort"] = "0.1.6"
    return selected


def result_file(result: lint.FormatResult, display_path: str) -> dict[str, Any]:
    status = "pass"
    if result.changed:
        status = "changed"
    item: dict[str, Any] = {
        "path": display_path,
        "status": status,
        "language": result.language.id,
        "runner_id": result.language.family,
        "image": lint.docker_image(result.language),
        "tool_versions": tools_for_language(result.language),
        "changed": result.changed,
        "diagnostics": [],
    }
    if result.changed:
        item["output_bytes_base64"] = base64.b64encode(result.formatted).decode("ascii")
    return item


def unsupported_file(path: str, language: Any) -> dict[str, Any]:
    return {
        "path": path,
        "status": "unsupported",
        "language": language,
        "runner_id": None,
        "image": None,
        "tool_versions": {},
        "changed": False,
        "diagnostics": [
            diagnostic("file language is unsupported", "unsupported_language")
        ],
    }


def image_targets() -> dict[str, str]:
    path = lint.ROOT / "images" / "matrix.json"
    with path.open("r", encoding="utf-8") as handle:
        matrix = json.load(handle)
    targets: dict[str, str] = {}
    for entry in matrix["images"]:
        target = entry["target"]
        for language_id in entry["languages"]:
            targets[language_id] = target
    return targets


def languages_response() -> dict[str, Any]:
    targets = image_targets()
    languages: list[dict[str, Any]] = []
    for language in lint.load_languages():
        languages.append(
            {
                "id": language.id,
                "runner_id": language.family,
                "image": lint.docker_image(language),
                "image_target": targets[language.id],
                "tool_versions": tools_for_language(language),
                "extensions": list(language.extensions),
                "filenames": list(language.filenames),
            }
        )
    return {
        "policy_id": POLICY_ID,
        "policy_ids": [POLICY_ID],
        "limits": lint.limits(),
        "languages": languages,
    }


def health_response() -> dict[str, str]:
    return {"status": "pass"}


def max_request_body_bytes() -> int:
    request_limits = lint.limits()
    encoded_bytes = ((request_limits["max_request_bytes"] + 2) // 3) * 4
    file_metadata_bytes = request_limits["max_files"] * (MAX_PATH_CHARACTERS + 256)
    return encoded_bytes + file_metadata_bytes + 4096


class Handler(BaseHTTPRequestHandler):
    server_version = "lint/0.1.6"
    use_docker = False

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self.send_json(200, health_response())
            return
        if self.path == "/v1/languages":
            self.send_json(200, languages_response())
            return
        self.send_json(404, {"status": "error", "diagnostics": []})

    def do_POST(self) -> None:
        if self.path != "/v1/lint":
            self.send_json(404, {"status": "error", "diagnostics": []})
            return
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            self.send_json(
                411,
                error_response(
                    "Content-Length is required",
                    "missing_content_length",
                ),
            )
            return
        try:
            length = int(content_length)
        except ValueError:
            self.send_json(
                400,
                error_response(
                    "Content-Length must be an integer",
                    "invalid_content_length",
                ),
            )
            return
        if length < 0:
            self.send_json(
                400,
                error_response(
                    "Content-Length must not be negative",
                    "invalid_content_length",
                ),
            )
            return
        if length > max_request_body_bytes():
            self.send_json(
                413,
                error_response(
                    "request body is too large",
                    "request_too_large",
                ),
            )
            return
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except UnicodeDecodeError as error:
            self.send_json(
                400,
                error_response(str(error), "json_parse_error"),
            )
            return
        except json.JSONDecodeError as error:
            response = error_response(error.msg, "json_parse_error")
            response["diagnostics"][0]["line"] = error.lineno
            response["diagnostics"][0]["column"] = error.colno
            self.send_json(400, response)
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
