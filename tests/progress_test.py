from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_lint():
    specification = importlib.util.spec_from_file_location(
        "lint_progress_under_test",
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


class ProgressModeTest(unittest.TestCase):
    def test_always_and_never(self) -> None:
        self.assertTrue(LINT.resolve_progress_mode("always"))
        self.assertFalse(LINT.resolve_progress_mode("never"))

    def test_auto_respects_ci(self) -> None:
        self.assertFalse(
            LINT.resolve_progress_mode(
                "auto",
                env={"CI": "true"},
                stderr_isatty=True,
            )
        )

    def test_auto_tty(self) -> None:
        self.assertTrue(
            LINT.resolve_progress_mode(
                "auto",
                env={},
                stderr_isatty=True,
            )
        )

    def test_env_override(self) -> None:
        self.assertTrue(
            LINT.resolve_progress_mode(
                "auto",
                env={"LINT_PROGRESS": "always"},
                stderr_isatty=False,
            )
        )
        self.assertFalse(
            LINT.resolve_progress_mode(
                "auto",
                env={"LINT_PROGRESS": "never"},
                stderr_isatty=True,
            )
        )


class ProgressEmitTest(unittest.TestCase):
    def test_events_when_always(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "note.md"
            target.write_text("# Title\n\nhello\n", encoding="utf-8")
            stderr = io.StringIO()
            with mock.patch.object(LINT, "ensure_host_formatters"):
                with mock.patch.object(LINT, "run_formatter"):
                    with mock.patch.object(
                        LINT,
                        "verify_formatter_version",
                    ):
                        LINT.set_progress_enabled(True)
                        with redirect_stderr(stderr):
                            results, skipped = LINT.prepare_results(
                                cwd=root,
                                paths=[target],
                                requested_languages=frozenset(),
                                use_docker=False,
                            )
            LINT.set_progress_enabled(False)
        self.assertEqual(len(results), 1)
        self.assertEqual(skipped, [])
        events = [
            json.loads(line) for line in stderr.getvalue().splitlines() if line.strip()
        ]
        names = [event["event"] for event in events]
        self.assertEqual(names[0], "start")
        self.assertIn("begin", names)
        self.assertIn("end", names)
        self.assertEqual(names[-1], "done")
        self.assertEqual(events[0]["total"], 1)

    def test_no_events_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "note.md"
            target.write_text("# Title\n\nhello\n", encoding="utf-8")
            stderr = io.StringIO()
            with mock.patch.object(LINT, "ensure_host_formatters"):
                with mock.patch.object(LINT, "run_formatter"):
                    with mock.patch.object(
                        LINT,
                        "verify_formatter_version",
                    ):
                        LINT.set_progress_enabled(False)
                        with redirect_stderr(stderr):
                            LINT.prepare_results(
                                cwd=root,
                                paths=[target],
                                requested_languages=frozenset(),
                                use_docker=False,
                            )
        self.assertEqual(stderr.getvalue().strip(), "")


if __name__ == "__main__":
    unittest.main()
