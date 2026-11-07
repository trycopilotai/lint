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
        # A transcript is captured command output, so its bytes
        # are the evidence. Writing it as text lets a platform
        # that translates newlines replace every canonical LF
        # marker with CRLF, which hides the raw results the
        # manifest hashes behind a transcript nothing can find
        # them in.
        transcript_path.write_bytes(transcript.encode("utf-8"))
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
        committed_manifest: bool = False,
        evidence_commit: str = "c" * 40,
        evidence_parent: str = "d" * 40,
        evidence_tree: str = "b" * 40,
    ) -> None:
        use_git = committed_manifest or changed != ""
        if replayed is None:
            replayed = transcript_path.read_bytes()
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=b"",
            stderr=b"",
        )

        def git_text(*arguments: str) -> str:
            if arguments[0] == "log":
                return evidence_commit
            if arguments[0] == "rev-parse":
                revision = arguments[1]
                if revision == f"{evidence_commit}^1":
                    return evidence_parent
                if revision == f"{evidence_parent}^{{tree}}":
                    return evidence_tree
                if not committed_manifest and revision == f"{'a' * 40}^{{tree}}":
                    return evidence_tree
                raise AssertionError(f"unexpected revision: {revision}")
            if arguments[0] == "diff":
                return changed
            raise AssertionError(f"unexpected git text arguments: {arguments}")

        def git_result(*arguments: str, check: bool = True):
            if arguments[:2] == ("diff", "--quiet"):
                returncode = 0
                if not committed_manifest:
                    returncode = 1
                return subprocess.CompletedProcess(
                    args=[],
                    returncode=returncode,
                    stdout=b"",
                    stderr=b"",
                )
            if arguments[:2] == ("merge-base", "--is-ancestor"):
                if committed_manifest:
                    raise AssertionError(
                        "committed verification must not resolve input_commit"
                    )
            return completed

        def commit_bytes(ref: str, path: str) -> bytes:
            expected_ref = manifest["input_tree"]
            if ref != expected_ref:
                raise AssertionError(f"unexpected source ref: {ref}")
            return sources[path]

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
            "MANIFEST_PATH",
            root / "evidence" / "demo-manifest.json",
        ), mock.patch.object(
            VERIFIER,
            "is_git_root",
            return_value=use_git,
        ), mock.patch.object(
            VERIFIER,
            "run_demo",
            return_value=replayed,
        ) as run_demo_mock, mock.patch.object(
            VERIFIER,
            "load_demo_generator",
            return_value=generator,
        ), mock.patch.object(
            VERIFIER,
            "git",
            side_effect=git_result,
        ), mock.patch.object(
            VERIFIER,
            "git_text",
            side_effect=git_text,
        ), mock.patch.object(
            VERIFIER,
            "commit_bytes",
            side_effect=commit_bytes,
        ) as commit_bytes_mock:
            VERIFIER.verify_manifest(manifest)
            run_demo_mock.assert_called_once_with(manifest["input_tree"], use_git)
            if committed_manifest:
                self.assertEqual(
                    [mock.call(manifest["input_tree"], path) for path in sources],
                    commit_bytes_mock.call_args_list,
                )

    def test_rewritten_input_commit_with_same_tree_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources, transcript_path, manifest = self.fixture(root)

            self.verify_fixture(
                root,
                sources,
                transcript_path,
                manifest,
                committed_manifest=True,
            )

    def test_rewritten_input_commit_can_be_missing_from_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources, transcript_path, manifest = self.fixture(root)

            subprocess.run(
                ["git", "-C", str(root), "init", "--quiet"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(root), "config", "user.name", "Demo Test"],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "config",
                    "user.email",
                    "demo-test@example.invalid",
                ],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(root), "add", *VERIFIER.SOURCE_PATHS],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(root), "commit", "--quiet", "-m", "Input"],
                check=True,
            )
            input_tree = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD^{tree}"],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout.strip()
            manifest["input_commit"] = "a" * 40
            manifest["input_tree"] = input_tree
            (root / "evidence" / "demo-manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "add",
                    "assets/demo.svg",
                    "evidence/demo-manifest.json",
                    "evidence/demo-transcript.txt",
                ],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "commit",
                    "--quiet",
                    "-m",
                    "Evidence",
                ],
                check=True,
            )
            missing_commit = subprocess.run(
                ["git", "-C", str(root), "cat-file", "-e", "a" * 40],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(0, missing_commit.returncode)

            generator = mock.Mock()
            generator.render_svg.return_value = "<svg/>\n"
            with mock.patch.object(VERIFIER, "ROOT", root), mock.patch.object(
                VERIFIER,
                "MANIFEST_PATH",
                root / "evidence" / "demo-manifest.json",
            ), mock.patch.object(
                VERIFIER,
                "TRANSCRIPT_PATH",
                transcript_path,
            ), mock.patch.object(
                VERIFIER,
                "run_demo",
                return_value=transcript_path.read_bytes(),
            ) as run_demo_mock, mock.patch.object(
                VERIFIER,
                "load_demo_generator",
                return_value=generator,
            ):
                VERIFIER.verify_manifest(manifest)

            run_demo_mock.assert_called_once_with(input_tree, True)

    def test_manifest_builder_hashes_sources_from_input_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources, transcript_path, _ = self.fixture(root)
            expected_tree = "b" * 40

            def commit_bytes(ref: str, path: str) -> bytes:
                self.assertEqual(expected_tree, ref)
                return sources[path]

            with mock.patch.object(VERIFIER, "ROOT", root), mock.patch.object(
                VERIFIER,
                "TRANSCRIPT_PATH",
                transcript_path,
            ), mock.patch.object(
                VERIFIER,
                "git_text",
                return_value=expected_tree,
            ), mock.patch.object(
                VERIFIER,
                "commit_bytes",
                side_effect=commit_bytes,
            ) as commit_bytes_mock:
                manifest = VERIFIER.build_manifest(
                    input_commit="a" * 40,
                    agent="Codex",
                    agent_version="codex-cli fixture",
                    invocation_date="2026-08-02",
                )

            self.assertEqual(expected_tree, manifest["input_tree"])
            self.assertEqual(
                [mock.call(expected_tree, path) for path in VERIFIER.SOURCE_PATHS],
                commit_bytes_mock.call_args_list,
            )

    def test_rewritten_input_commit_with_mismatched_tree_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources, transcript_path, manifest = self.fixture(root)
            manifest["input_tree"] = "e" * 40

            with self.assertRaisesRegex(ValueError, "input tree"):
                self.verify_fixture(
                    root,
                    sources,
                    transcript_path,
                    manifest,
                    committed_manifest=True,
                )

    def test_allowed_evidence_only_changes_after_source_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources, transcript_path, manifest = self.fixture(root)

            self.verify_fixture(
                root,
                sources,
                transcript_path,
                manifest,
                changed="assets/demo.svg",
                committed_manifest=True,
            )

    def test_missing_or_malformed_input_tree_is_rejected(self) -> None:
        invalid_trees = (None, "not-a-tree", "b" * 39, "B" * 40)
        for invalid_tree in invalid_trees:
            with self.subTest(invalid_tree=invalid_tree):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    sources, transcript_path, manifest = self.fixture(root)
                    if invalid_tree is None:
                        manifest.pop("input_tree")
                    else:
                        manifest["input_tree"] = invalid_tree

                    with self.assertRaisesRegex(ValueError, "input_tree"):
                        self.verify_fixture(
                            root,
                            sources,
                            transcript_path,
                            manifest,
                        )

    def test_unrelated_commit_after_evidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources, transcript_path, manifest = self.fixture(root)

            with self.assertRaisesRegex(ValueError, "unrelated paths"):
                self.verify_fixture(
                    root,
                    sources,
                    transcript_path,
                    manifest,
                    changed="scripts/demo.sh",
                    committed_manifest=True,
                )

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

    def test_transcript_fixture_keeps_canonical_marker_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, transcript_path, _ = self.fixture(root)

            payload = transcript_path.read_bytes()

            self.assertEqual(self.transcript().encode("utf-8"), payload)
            self.assertNotIn(b"\r", payload)
            for name in VERIFIER.RAW_RESULTS:
                marker = f"$ cat demo-work/{name}.json\n".encode("utf-8")
                self.assertIn(marker, payload)

    def test_published_demo_paths_keep_canonical_newlines(self) -> None:
        # Every path below is hashed by the demo manifest, so a
        # working tree that translated its newlines could never
        # satisfy the recorded digest. `.gitattributes` keeps
        # each one out of end-of-line conversion.
        for path in VERIFIER.SOURCE_PATHS + VERIFIER.ARTIFACT_PATHS:
            payload = (ROOT / path).read_bytes()

            self.assertNotIn(b"\r", payload, path)

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

    def test_demo_command_runs_the_script_directly_on_posix(self) -> None:
        with mock.patch.object(VERIFIER, "WINDOWS", False):
            command = VERIFIER.demo_command("demo-work")

        self.assertEqual(["./scripts/demo.sh", "demo-work"], command)

    def test_windows_demo_command_names_an_explicit_shell(self) -> None:
        shell = "C:\\Program Files\\Git\\bin\\bash.exe"
        replacement = mock.Mock()
        replacement.which.return_value = shell

        with mock.patch.object(VERIFIER, "WINDOWS", True), mock.patch.object(
            VERIFIER,
            "shutil",
            replacement,
        ):
            command = VERIFIER.demo_command("demo-work")

        self.assertEqual([shell, "./scripts/demo.sh", "demo-work"], command)
        replacement.which.assert_called_once_with("bash")

    def test_windows_demo_command_fails_without_a_shell(self) -> None:
        replacement = mock.Mock()
        replacement.which.return_value = None

        with mock.patch.object(VERIFIER, "WINDOWS", True), mock.patch.object(
            VERIFIER,
            "shutil",
            replacement,
        ):
            with self.assertRaisesRegex(ValueError, "bash or sh"):
                VERIFIER.demo_command("demo-work")

    def test_demo_script_requires_a_fresh_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "demo"
            # A POSIX path keeps the workspace argument readable
            # to the shell that runs the script on every
            # platform.
            command = VERIFIER.demo_command(workspace.as_posix())

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
