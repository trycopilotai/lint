from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_lint():
    specification = importlib.util.spec_from_file_location(
        "lint_host_ensure_under_test",
        ROOT / "lint.py",
    )
    if specification is None:
        raise RuntimeError("could not create lint module specification")
    if specification.loader is None:
        raise RuntimeError("lint module specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


LINT = load_lint()


def python_language():
    for language in LINT.load_languages():
        if language.family == "black":
            return language
    raise RuntimeError("python/black language missing")


def markdown_language():
    for language in LINT.load_languages():
        if language.family == "prettier" and language.id == "markdown":
            return language
    raise RuntimeError("markdown language missing")


class ResolveInstallPolicyTest(unittest.TestCase):
    def test_explicit_values(self) -> None:
        self.assertEqual(
            LINT.resolve_install_policy({"LINT_INSTALL": "always"}),
            "always",
        )
        self.assertEqual(
            LINT.resolve_install_policy({"LINT_INSTALL": "NEVER"}),
            "never",
        )

    def test_ci_defaults_to_never(self) -> None:
        self.assertEqual(
            LINT.resolve_install_policy(
                {"CI": "true"},
                stdin_isatty=True,
            ),
            "never",
        )

    def test_tty_defaults_to_prompt(self) -> None:
        self.assertEqual(
            LINT.resolve_install_policy({}, stdin_isatty=True),
            "prompt",
        )

    def test_non_tty_defaults_to_never(self) -> None:
        self.assertEqual(
            LINT.resolve_install_policy({}, stdin_isatty=False),
            "never",
        )

    def test_invalid_policy(self) -> None:
        with self.assertRaises(LINT.SelectionError):
            LINT.resolve_install_policy({"LINT_INSTALL": "sometimes"})


class HostEnsureTest(unittest.TestCase):
    def test_doctor_ok_when_probe_passes(self) -> None:
        language = python_language()
        with mock.patch.object(LINT, "verify_formatter_version"):
            report = LINT.doctor_host_formatters([language])
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["recovery"]["missing"], [])

    def test_doctor_reports_missing(self) -> None:
        language = python_language()

        def fail(language_arg):
            del language_arg
            raise LINT.EngineError(
                "black is not installed; "
                "use --docker or run `pipx install --force black==24.10.0`"
            )

        with mock.patch.object(
            LINT,
            "verify_formatter_version",
            side_effect=fail,
        ):
            report = LINT.doctor_host_formatters([language])
        self.assertEqual(report["status"], "missing")
        missing = report["recovery"]["missing"]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["family"], "black")
        self.assertTrue(missing[0]["allowlisted"])
        self.assertEqual(
            missing[0]["install_argv"][:3],
            ["pipx", "install", "--force"],
        )

    def test_ensure_never_raises_host_tool_error(self) -> None:
        language = python_language()

        def fail(language_arg):
            del language_arg
            raise LINT.EngineError("black is not installed")

        with mock.patch.object(
            LINT,
            "verify_formatter_version",
            side_effect=fail,
        ):
            with self.assertRaises(LINT.HostToolError) as raised:
                LINT.ensure_host_formatters(
                    [language],
                    policy="never",
                )
        self.assertIn("black", str(raised.exception))
        self.assertEqual(
            raised.exception.recovery["install_policy"],
            "never",
        )

    def test_ensure_always_runs_install_and_succeeds(self) -> None:
        language = python_language()
        calls: list[list[str]] = []

        def probe(language_arg):
            del language_arg
            if calls:
                return
            raise LINT.EngineError("black is not installed")

        def runner(argv, **kwargs):
            del kwargs
            calls.append(list(argv))

            class Result:
                returncode = 0
                stdout = ""
                stderr = ""

            return Result()

        with mock.patch.object(
            LINT,
            "verify_formatter_version",
            side_effect=probe,
        ):
            remaining = LINT.ensure_host_formatters(
                [language],
                policy="always",
                runner=runner,
            )
        self.assertEqual(remaining, [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "pipx")

    def test_prompt_no_skips_install(self) -> None:
        language = python_language()
        calls: list[list[str]] = []

        def fail(language_arg):
            del language_arg
            raise LINT.EngineError("black is not installed")

        def runner(argv, **kwargs):
            del kwargs
            calls.append(list(argv))

            class Result:
                returncode = 0
                stdout = ""
                stderr = ""

            return Result()

        with mock.patch.object(
            LINT,
            "verify_formatter_version",
            side_effect=fail,
        ):
            with self.assertRaises(LINT.HostToolError):
                LINT.ensure_host_formatters(
                    [language],
                    policy="prompt",
                    input_func=lambda _prompt: "n",
                    runner=runner,
                )
        self.assertEqual(calls, [])

    def test_non_allowlisted_never_auto_installed(self) -> None:
        language = None
        for item in LINT.load_languages():
            if item.family == "go":
                language = item
                break
        self.assertIsNotNone(language)
        assert language is not None
        calls: list[list[str]] = []

        def fail(language_arg):
            del language_arg
            raise LINT.EngineError("gofmt is not installed")

        def runner(argv, **kwargs):
            del kwargs
            calls.append(list(argv))

            class Result:
                returncode = 0
                stdout = ""
                stderr = ""

            return Result()

        with mock.patch.object(
            LINT,
            "verify_formatter_version",
            side_effect=fail,
        ):
            with self.assertRaises(LINT.HostToolError):
                LINT.ensure_host_formatters(
                    [language],
                    policy="always",
                    runner=runner,
                )
        self.assertEqual(calls, [])

    def test_main_doctor_json(self) -> None:
        language = python_language()
        with mock.patch.object(LINT, "verify_formatter_version"):
            with mock.patch.object(LINT, "print_response"):
                code = LINT.main(
                    [
                        "doctor",
                        "--json",
                        "--language",
                        language.id,
                    ]
                )
        self.assertEqual(code, 0)

    def test_main_ensure_blocked_when_never(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {"LINT_INSTALL": "never", "CI": "true"},
            clear=False,
        ):
            with mock.patch.object(LINT, "print_response"):
                code = LINT.main(["ensure", "--json", "--language", "python"])
        self.assertEqual(code, LINT.EXIT_SELECTION)


if __name__ == "__main__":
    unittest.main()
