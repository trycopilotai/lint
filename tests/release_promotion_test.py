from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(relative_path: str, name: str):
    path = ROOT / relative_path
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None:
        raise RuntimeError(f"could not load {relative_path}")
    if specification.loader is None:
        raise RuntimeError(f"could not load {relative_path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


PROMOTION = load_module(
    "tools/promote_image.py",
    "promote_image_under_test",
)


IMAGE = "ghcr.io/trycopilotai/lint-python"
VERSION = "0.1.7"
STAGING_TAG = "0.1.7-deadbeef"
DIGEST = "sha256:" + "1" * 64
OTHER_DIGEST = "sha256:" + "2" * 64
ALIAS = f"{IMAGE}:{VERSION}"
STAGING = f"{IMAGE}:{STAGING_TAG}@{DIGEST}"
INSPECT = [
    "docker",
    "buildx",
    "imagetools",
    "inspect",
    ALIAS,
    "--format",
    "{{json .Manifest.Digest}}",
]
CREATE = [
    "docker",
    "buildx",
    "imagetools",
    "create",
    "--tag",
    ALIAS,
    STAGING,
]


def completed(
    command: list[str],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        command,
        returncode,
        stdout=stdout,
        stderr=stderr,
    )


class ImagePromotionTest(unittest.TestCase):
    @mock.patch.object(PROMOTION.subprocess, "run")
    def test_matching_alias_is_left_unchanged(self, run: mock.Mock) -> None:
        run.return_value = completed(INSPECT, stdout=f'"{DIGEST}"\n')

        PROMOTION.promote(IMAGE, VERSION, STAGING_TAG, DIGEST)

        run.assert_called_once_with(
            INSPECT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    @mock.patch.object(PROMOTION.subprocess, "run")
    def test_conflicting_alias_stops_before_mutation(self, run: mock.Mock) -> None:
        run.return_value = completed(INSPECT, stdout=f'"{OTHER_DIGEST}"\n')

        with self.assertRaisesRegex(ValueError, "already resolves"):
            PROMOTION.promote(IMAGE, VERSION, STAGING_TAG, DIGEST)

        run.assert_called_once()

    @mock.patch.object(PROMOTION.subprocess, "run")
    def test_absent_alias_is_created_then_verified(self, run: mock.Mock) -> None:
        run.side_effect = [
            completed(
                INSPECT,
                returncode=1,
                stderr=f"ERROR: {ALIAS}: not found\n",
            ),
            completed(CREATE),
            completed(INSPECT, stdout=f'"{DIGEST}"\n'),
        ]

        PROMOTION.promote(IMAGE, VERSION, STAGING_TAG, DIGEST)

        self.assertEqual(
            [INSPECT, CREATE, INSPECT], [call.args[0] for call in run.call_args_list]
        )
        self.assertTrue(run.call_args_list[1].kwargs["check"])

    @mock.patch.object(PROMOTION.subprocess, "run")
    def test_ambiguous_inspection_failure_stops_before_mutation(
        self,
        run: mock.Mock,
    ) -> None:
        run.return_value = completed(
            INSPECT,
            returncode=1,
            stderr="ERROR: registry request timed out\n",
        )

        with self.assertRaisesRegex(RuntimeError, "could not inspect"):
            PROMOTION.promote(IMAGE, VERSION, STAGING_TAG, DIGEST)

        run.assert_called_once()

    def test_workflow_delegates_each_verified_record_to_guarded_promotion(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("python3 tools/promote_image.py", workflow)
        self.assertNotIn("docker buildx imagetools create", workflow)
        self.assertLess(
            workflow.index("--verify-only"),
            workflow.index("python3 tools/promote_image.py"),
        )


if __name__ == "__main__":
    unittest.main()
