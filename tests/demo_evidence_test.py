from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_verifier():
    path = ROOT / "scripts" / "verify_demo.py"
    specification = importlib.util.spec_from_file_location(
        "demo_evidence_verifier_under_test",
        path,
    )
    if specification is None:
        raise RuntimeError("could not create demo verifier specification")
    if specification.loader is None:
        raise RuntimeError("demo verifier specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


VERIFIER = load_verifier()


class DemoEvidenceTest(unittest.TestCase):
    def transcript(self) -> str:
        sections = ["$ ./scripts/demo.sh demo-work\n"]
        for name in VERIFIER.RAW_RESULTS:
            sections.append(f"\n$ cat demo-work/{name}.json\n")
            sections.append(json.dumps({"name": name}, indent=2) + "\n")
        return "".join(sections)

    def fixture(self, root: Path):
        sources: dict[str, bytes] = {}
        for path in VERIFIER.SOURCE_PATHS:
            payload = f"source:{path}\n".encode("utf-8")
            sources[path] = payload
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)

        transcript = self.transcript()
        transcript_path = root / "evidence" / "demo-transcript.txt"
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(transcript, encoding="utf-8")
        demo_path = root / "assets" / "demo.svg"
        demo_path.parent.mkdir(parents=True, exist_ok=True)
        demo_path.write_bytes(b"<svg/>\n")

        source_hashes = {
            path: hashlib.sha256(payload).hexdigest()
            for path, payload in sources.items()
        }
        artifact_hashes = {
            path: hashlib.sha256((root / path).read_bytes()).hexdigest()
            for path in VERIFIER.ARTIFACT_PATHS
        }
        raw_hashes = {
            name: hashlib.sha256(
                VERIFIER.raw_result_bytes(transcript, name)
            ).hexdigest()
            for name in VERIFIER.RAW_RESULTS
        }
        manifest = {
            "schema_version": 2,
            "agent": "Codex",
            "agent_version": "codex-cli 0.146.0",
            "input_commit": "a" * 40,
            "input_tree": "b" * 40,
            "invocation": "./scripts/demo.sh demo-work",
            "invocation_date": "2026-08-02",
            "transcript_edited": False,
            "output_path": VERIFIER.OUTPUT_PATH,
            "output_sha256": artifact_hashes[VERIFIER.OUTPUT_PATH],
            "protocol_path": VERIFIER.PROTOCOL_PATH,
            "protocol_sha256": source_hashes[VERIFIER.PROTOCOL_PATH],
            "source_sha256": source_hashes,
            "artifact_sha256": artifact_hashes,
            "raw_result_sha256": raw_hashes,
        }
        return sources, transcript_path, manifest

    def verify_fixture(
        self,
        root: Path,
        sources: dict[str, bytes],
        transcript_path: Path,
        manifest,
        changed: str = "",
        replayed: bytes | None = None,
    ) -> None:
        use_git = False
        if changed != "":
            use_git = True
        if replayed is None:
            replayed = transcript_path.read_bytes()
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=b"",
            stderr=b"",
        )

        def git_text(*arguments: str) -> str:
            if arguments[0] == "rev-parse":
                return "b" * 40
            if arguments[0] == "diff":
                return changed
            raise AssertionError(f"unexpected git text arguments: {arguments}")

        generator = mock.Mock()
        generator.render_svg.return_value = "<svg/>\n"

        with mock.patch.object(
            VERIFIER,
            "ROOT",
            root,
        ), mock.patch.object(
            VERIFIER,
            "TRANSCRIPT_PATH",
            transcript_path,
        ), mock.patch.object(
            VERIFIER,
            "is_git_root",
            return_value=use_git,
        ), mock.patch.object(
            VERIFIER,
            "run_demo",
            return_value=replayed,
        ), mock.patch.object(
            VERIFIER,
            "load_demo_generator",
            return_value=generator,
        ), mock.patch.object(
            VERIFIER,
            "git",
            return_value=completed,
        ), mock.patch.object(
            VERIFIER,
            "git_text",
            side_effect=git_text,
        ), mock.patch.object(
            VERIFIER,
            "commit_bytes",
            side_effect=lambda _commit, path: sources[path],
        ):
            VERIFIER.verify_manifest(manifest)

    def test_complete_manifest_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources, transcript_path, manifest = self.fixture(root)

            self.verify_fixture(root, sources, transcript_path, manifest)

    def test_canonical_json_round_trip_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources, transcript_path, manifest = self.fixture(root)
            encoded = json.dumps(manifest, sort_keys=True)
            reloaded = json.loads(encoded)

            self.verify_fixture(root, sources, transcript_path, reloaded)

    def test_source_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources, transcript_path, manifest = self.fixture(root)
            (root / "lint.py").write_bytes(b"changed\n")

            with self.assertRaisesRegex(ValueError, "source hash differs"):
                self.verify_fixture(root, sources, transcript_path, manifest)

    def test_raw_result_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources, transcript_path, manifest = self.fixture(root)
            manifest["raw_result_sha256"]["write"] = "0" * 64

            with self.assertRaisesRegex(ValueError, "raw result hash differs"):
                self.verify_fixture(root, sources, transcript_path, manifest)

    def test_fabricated_transcript_is_rejected_by_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources, transcript_path, manifest = self.fixture(root)

            with self.assertRaisesRegex(ValueError, "clean replay"):
                self.verify_fixture(
                    root,
                    sources,
                    transcript_path,
                    manifest,
                    replayed=b"different output\n",
                )

    def test_demo_script_requires_a_fresh_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "demo"
            command = ["./scripts/demo.sh", str(workspace)]

            first = subprocess.run(
                command,
                cwd=ROOT,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            second = subprocess.run(
                command,
                cwd=ROOT,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(0, first.returncode)
            self.assertEqual(2, second.returncode)
            self.assertIn(b"workspace already exists", second.stderr)

    def test_unrelated_evidence_commit_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources, transcript_path, manifest = self.fixture(root)

            with self.assertRaisesRegex(ValueError, "unrelated paths"):
                self.verify_fixture(
                    root,
                    sources,
                    transcript_path,
                    manifest,
                    changed="lint.py",
                )


if __name__ == "__main__":
    unittest.main()
