"""Tests for the release automation in `tools/release.py`.

Every version literal here is deliberately outside the range
lint releases from. This file is itself rewritten by a bump, so
a fixture pinned to the repository's real version would be
edited into disagreement with its own assertions the first time
the script it tests is used.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

STALE = "77.7.6"
RELEASED = "77.7.7"
CANDIDATE = "77.7.8"

# The third-party crate whose recorded version collides with the
# one a bump replaces. A rewrite that reaches it records a
# release the crate never made.
COLLIDING_CRATE = "anes"


def load_release():
    path = ROOT / "tools" / "release.py"
    specification = importlib.util.spec_from_file_location(
        "release_under_test",
        path,
    )
    if specification is None:
        raise RuntimeError("could not create release specification")
    if specification.loader is None:
        raise RuntimeError("release specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


RELEASE = load_release()


class VersionExpressionTest(unittest.TestCase):
    def test_matches_every_shape_a_release_site_uses(self) -> None:
        expression = RELEASE.version_expression(RELEASED)
        sites = (
            f'"version": "{RELEASED}"',
            f"placeholder: v{RELEASED}",
            f"uses: trycopilotai/lint@v{RELEASED}",
            f"--image-manifest release-manifest-{RELEASED}.json",
            f"ghcr.io/trycopilotai/lint-go:{RELEASED}",
            f"release=v{RELEASED}",
            f"verify-tag v{RELEASED}",
            f'server_version = "lint/{RELEASED}"',
            f"trycopilotai-lint-comparison-evidence/{RELEASED}",
            f"lint-{RELEASED}.tar.gz",
            f'"staging_tag": "{RELEASED}-deadbeef"',
        )
        for site in sites:
            with self.subTest(site=site):
                self.assertIsNotNone(expression.search(site))

    def test_never_matches_a_longer_dotted_number(self) -> None:
        expression = RELEASE.version_expression(RELEASED)
        # A plain substring rewrite turns 177.7.7 into 177.7.8
        # and 77.7.70 into 77.7.80, so the boundary is part of
        # the contract rather than an implementation detail.
        neighbours = (f"1{RELEASED}", f"{RELEASED}0", f"{RELEASED}.2", "77.7.71")
        for neighbour in neighbours:
            with self.subTest(neighbour=neighbour):
                self.assertIsNone(expression.search(neighbour))


class ExclusionTest(unittest.TestCase):
    def test_legal_and_generated_paths_are_excluded(self) -> None:
        excluded = (
            "images/license_sources.json",
            "images/licenses/anes-COMMIT.txt",
            "evidence/demo-manifest.json",
            "images/inventories/go-amd64.json",
        )
        for path in excluded:
            with self.subTest(path=path):
                self.assertTrue(RELEASE.is_excluded(path))

    def test_release_surfaces_are_not_excluded(self) -> None:
        included = (
            "images/matrix.json",
            "images/Dockerfile",
            "README.md",
            "service.py",
            "skills/lint/images/matrix.json",
            "tools/verify_repo.py",
        )
        for path in included:
            with self.subTest(path=path):
                self.assertFalse(RELEASE.is_excluded(path))


class RepositoryFixture:
    """A miniature repository carrying every site shape lint has."""

    def __init__(self, directory: Path) -> None:
        self.root = directory
        self.write("images/matrix.json", self.matrix(RELEASED))
        # The packaged copy is one release behind, which is the
        # drift `action_test.py` fails on. Only a re-sync after
        # the rewrite repairs it, because the stale number is
        # not the number the rewrite looks for.
        self.write("skills/lint/images/matrix.json", self.matrix(STALE))
        self.write("languages.json", '{\n  "languages": []\n}\n')
        self.write("skills/lint/languages.json", '{\n  "languages": []\n}\n')
        self.write("lint.py", "ENGINE = 1\n")
        self.write("skills/lint/lint.py", "ENGINE = 1\n")
        self.write("README.md", self.readme())
        self.write("service.py", f'server_version = "lint/{RELEASED}"\n')
        self.write("images/license_sources.json", self.license_sources())
        self.write("images/licenses/anes-COMMIT.txt", f"anes {RELEASED}\n")
        self.write("evidence/demo-manifest.json", f'{{"note": "{RELEASED}"}}\n')
        self.write("images/inventories/go-amd64.json", f'{{"tag": "{RELEASED}"}}\n')
        self.write_bytes("assets/demo.bin", b"\x89PNG\r\n\x1a\n\x00\x01")
        self.commit()

    def matrix(self, version: str) -> str:
        document = {"images": [{"target": "go"}], "version": version}
        return json.dumps(document, indent=2, sort_keys=True) + "\n"

    def readme(self) -> str:
        return (
            f"Pinned to `v{RELEASED}`.\n"
            f"  - uses: trycopilotai/lint@v{RELEASED}\n"
            f"  --image-manifest release-manifest-{RELEASED}.json\n"
            f"release=v{RELEASED}\n"
            f"release=v{RELEASED}\n"
            f"An unrelated dependency pinned to 1{RELEASED} stays put.\n"
        )

    def license_sources(self) -> str:
        document = {
            "cargo_supplements": [
                {"name": COLLIDING_CRATE, "version": RELEASED},
                {"name": "crunchy", "version": "0.2.2"},
            ],
            "schema_version": 1,
        }
        return json.dumps(document, indent=2, sort_keys=True) + "\n"

    def write(self, path: str, text: str) -> None:
        self.write_bytes(path, text.encode("utf-8"))

    def write_bytes(self, path: str, payload: bytes) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    def read(self, path: str) -> str:
        return (self.root / path).read_text(encoding="utf-8")

    def track(self, path: str, payload: bytes) -> None:
        self.write_bytes(path, payload)
        self.git("add", path)

    def git(self, *arguments: str) -> None:
        subprocess.run(
            ["git", "-C", str(self.root), *arguments],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def commit(self) -> None:
        self.git("init", "--initial-branch", "main")
        self.git("config", "user.name", "Release Test")
        self.git("config", "user.email", "release@example.invalid")
        self.git("add", "--all")
        self.git("commit", "--message", "fixture")


class BumpTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repository = RepositoryFixture(Path(directory.name))

    def test_bump_moves_every_owned_site(self) -> None:
        summary = RELEASE.bump(self.repository.root, CANDIDATE)

        self.assertEqual("ok", summary["status"])
        self.assertEqual(RELEASED, summary["old_version"])
        self.assertEqual(CANDIDATE, summary["new_version"])
        readme = self.repository.read("README.md")
        self.assertIn(f"uses: trycopilotai/lint@v{CANDIDATE}", readme)
        self.assertIn(f"release-manifest-{CANDIDATE}.json", readme)
        self.assertEqual(2, readme.count(f"release=v{CANDIDATE}"))
        self.assertIn(f"lint/{CANDIDATE}", self.repository.read("service.py"))
        self.assertEqual(CANDIDATE, RELEASE.read_version(self.repository.root))

    def test_bump_changes_nothing_but_the_version(self) -> None:
        # `write_text` would translate every newline to the host
        # separator, so a rewrite run on Windows would reformat
        # every file it touches while moving one number.
        before = (self.repository.root / "README.md").read_bytes()

        RELEASE.bump(self.repository.root, CANDIDATE)

        after = (self.repository.root / "README.md").read_bytes()
        self.assertNotIn(b"\r", after)
        self.assertEqual(before.count(b"\n"), after.count(b"\n"))
        substituted = RELEASE.version_expression_bytes(RELEASED).sub(
            CANDIDATE.encode("ascii"),
            before,
        )
        self.assertEqual(substituted, after)

    def test_bump_leaves_an_unrelated_longer_version_alone(self) -> None:
        RELEASE.bump(self.repository.root, CANDIDATE)

        self.assertIn(
            f"pinned to 1{RELEASED} stays put",
            self.repository.read("README.md"),
        )

    def test_bump_never_rewrites_third_party_provenance(self) -> None:
        # The crate records exactly the version being replaced,
        # so a rewrite that ignores the exclusions corrupts a
        # legal record instead of failing visibly.
        RELEASE.bump(self.repository.root, CANDIDATE)

        payload = self.repository.read("images/license_sources.json")
        sources = json.loads(payload)
        recorded = {row["name"]: row["version"] for row in sources["cargo_supplements"]}
        self.assertEqual(RELEASED, recorded[COLLIDING_CRATE])
        self.assertEqual(
            f"anes {RELEASED}\n",
            self.repository.read("images/licenses/anes-COMMIT.txt"),
        )

    def test_bump_leaves_generated_receipts_alone(self) -> None:
        RELEASE.bump(self.repository.root, CANDIDATE)

        for path in ("evidence/demo-manifest.json", "images/inventories/go-amd64.json"):
            with self.subTest(path=path):
                self.assertIn(RELEASED, self.repository.read(path))

    def test_bump_reports_a_corrupted_third_party_record(self) -> None:
        # The exclusions are the defence; this assertion is the
        # alarm behind them. Widening the rewrite has to fail the
        # release rather than pass silently.
        recorded = RELEASE.third_party_versions(self.repository.root)
        document = json.loads(self.repository.read("images/license_sources.json"))
        document["cargo_supplements"][0]["version"] = CANDIDATE
        self.repository.write(
            "images/license_sources.json",
            json.dumps(document, indent=2, sort_keys=True) + "\n",
        )

        with self.assertRaises(RELEASE.ReleaseError) as caught:
            RELEASE.verify_third_party_versions(self.repository.root, recorded)
        message = str(caught.exception)
        self.assertIn(COLLIDING_CRATE, message)
        self.assertIn(f"{RELEASED} -> {CANDIDATE}", message)

    def test_bump_syncs_the_packaged_skill_copies(self) -> None:
        summary = RELEASE.bump(self.repository.root, CANDIDATE)

        self.assertIn(
            "skills/lint/images/matrix.json",
            summary["packaged_copies_synced"],
        )
        self.assertNotIn(
            STALE,
            self.repository.read("skills/lint/images/matrix.json"),
        )
        for source, packaged in RELEASE.PACKAGED_COPIES:
            with self.subTest(packaged=packaged):
                self.assertEqual(
                    (self.repository.root / source).read_bytes(),
                    (self.repository.root / packaged).read_bytes(),
                )

    def test_bump_fails_on_a_site_the_rewrite_cannot_edit(self) -> None:
        # A hand-cut bump missed six files and only the test
        # suite noticed, so the completeness check is what has to
        # refuse. A tracked payload the rewrite will not open is
        # the case the rewrite cannot fix by itself.
        self.repository.track(
            "assets/pinned.bin",
            b"\x00tag=" + RELEASED.encode("ascii") + b"\n",
        )

        with self.assertRaises(RELEASE.ReleaseError) as caught:
            RELEASE.bump(self.repository.root, CANDIDATE)
        message = str(caught.exception)
        self.assertIn(f"version {RELEASED} survives the bump", message)
        self.assertIn("assets/pinned.bin", message)

    def test_bump_ignores_a_tracked_symbolic_link(self) -> None:
        # `skill` is a tracked link to `skills/lint`, so reading
        # it reads a directory and rewriting through it edits
        # the same bytes twice.
        (self.repository.root / "skill").symlink_to("skills/lint")
        self.repository.git("add", "skill")

        summary = RELEASE.bump(self.repository.root, CANDIDATE)

        self.assertEqual("ok", summary["status"])
        self.assertTrue((self.repository.root / "skill").is_symlink())

    def test_bump_rejects_a_version_that_is_not_a_release(self) -> None:
        for candidate in (f"v{CANDIDATE}", "77.7", f"{CANDIDATE}-rc1", "07.7.8", ""):
            with self.subTest(candidate=candidate):
                with self.assertRaises(RELEASE.ReleaseError):
                    RELEASE.bump(self.repository.root, candidate)

    def test_bump_rejects_the_version_already_released(self) -> None:
        with self.assertRaises(RELEASE.ReleaseError) as caught:
            RELEASE.bump(self.repository.root, RELEASED)
        self.assertIn(f"already at {RELEASED}", str(caught.exception))


class PackagedCopyDriftTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repository = RepositoryFixture(Path(directory.name))
        self.repository.write("lint.py", "ENGINE = 2\n")

    def test_drift_is_reported_before_it_is_repaired(self) -> None:
        with self.assertRaises(RELEASE.ReleaseError) as caught:
            RELEASE.verify_packaged_copies(self.repository.root)
        self.assertIn("packaged copy differs from lint.py", str(caught.exception))

    def test_sync_restores_a_drifted_packaged_copy(self) -> None:
        synced = RELEASE.sync_packaged_copies(self.repository.root)

        self.assertIn("skills/lint/lint.py", synced)
        RELEASE.verify_packaged_copies(self.repository.root)


class EvidenceOrderingTest(unittest.TestCase):
    """The evidence refresh has to run after the source commit."""

    ALLOWED = ("evidence/demo-manifest.json",)

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repository = RepositoryFixture(Path(directory.name))

    def test_a_clean_tree_is_ready_for_the_evidence_refresh(self) -> None:
        RELEASE.require_committed_source(self.repository.root, self.ALLOWED)

    def test_an_evidence_only_change_is_ready(self) -> None:
        self.repository.write("evidence/demo-manifest.json", '{"note": "new"}\n')

        RELEASE.require_committed_source(self.repository.root, self.ALLOWED)

    def test_a_pending_source_change_stops_the_refresh(self) -> None:
        # Refreshing first is what produces "demo evidence
        # commit contains unrelated paths" from
        # scripts/verify_demo.py, after the manifest has already
        # been overwritten. Refusing up front leaves nothing to
        # undo.
        self.repository.write("service.py", "server_version = 1\n")

        with self.assertRaises(RELEASE.ReleaseError) as caught:
            RELEASE.require_committed_source(self.repository.root, self.ALLOWED)
        message = str(caught.exception)
        self.assertIn("commit the source changes", message)
        self.assertIn("service.py", message)

    def test_a_pending_untracked_file_stops_the_refresh(self) -> None:
        self.repository.write("tools/new_helper.py", "HELPER = 1\n")

        with self.assertRaises(RELEASE.ReleaseError) as caught:
            RELEASE.require_committed_source(self.repository.root, self.ALLOWED)
        self.assertIn("tools/new_helper.py", str(caught.exception))


class RepositorySurfaceTest(unittest.TestCase):
    """The checked-in repository, not the fixture."""

    def test_the_checked_in_repository_is_internally_consistent(self) -> None:
        version = RELEASE.read_version(ROOT)

        self.assertIsNotNone(RELEASE.RELEASE_VERSION.fullmatch(version))
        RELEASE.verify_packaged_copies(ROOT)

    def test_every_live_site_sits_inside_the_rewrite_set(self) -> None:
        # A bump only rewrites what `git ls-files` reports minus
        # the exclusions. A real version site outside that set
        # would leave the completeness check reporting success
        # over a stale file.
        version = RELEASE.read_version(ROOT)
        sites = RELEASE.remaining_sites(ROOT, version)
        paths = {site.split(":", 1)[0] for site in sites}

        self.assertIn("images/matrix.json", paths)
        self.assertIn("skills/lint/images/matrix.json", paths)
        self.assertIn("README.md", paths)
        self.assertIn("tools/verify_repo.py", paths)
        for path in paths:
            with self.subTest(path=path):
                self.assertFalse(RELEASE.is_excluded(path))

    def test_third_party_versions_survive_the_current_release(self) -> None:
        recorded = RELEASE.third_party_versions(ROOT)
        names = {location.rsplit(" ", 1)[-1] for location in recorded}

        self.assertIn(COLLIDING_CRATE, names)
        RELEASE.verify_third_party_versions(ROOT, recorded)

    def test_the_documentation_states_no_version_of_its_own(self) -> None:
        # The checklist is rewritten by the bump like any other
        # tracked file, so a version literal in its prose would
        # be edited into a claim about a release that never
        # happened.
        document = (ROOT / RELEASE.CHECKLIST_DOCUMENT).read_text(encoding="utf-8")
        version = RELEASE.read_version(ROOT)

        self.assertIsNone(RELEASE.version_expression(version).search(document))
        self.assertIsNone(RELEASE.version_expression(version).search(RELEASE.__doc__))

    def test_checklist_prints_the_documented_release_steps(self) -> None:
        document = (ROOT / RELEASE.CHECKLIST_DOCUMENT).read_text(encoding="utf-8")

        self.assertEqual(document, RELEASE.checklist(ROOT))
        # Each phrase belongs to a step the script cannot take:
        # a judgement call, a built image, or a browser.
        required = (
            "--severity HIGH,CRITICAL --exit-code 1",
            ".trivyignore.yaml",
            "ARG GO_IMAGE",
            "ARG DOTNET_RUNTIME",
            "images/THIRD_PARTY_NOTICES.md",
            "images/Dockerfile:85",
            "images/generate_image_inventory.py",
            "go version -m",
            "images/dependency_closures.json",
            ".github/release-allowed-signers",
            "deletePackageVersion",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, document)

    def test_checklist_documents_the_evidence_commit_ordering(self) -> None:
        document = (ROOT / RELEASE.CHECKLIST_DOCUMENT).read_text(encoding="utf-8")
        normalized = " ".join(document.split())
        module_help = " ".join(RELEASE.__doc__.split())

        self.assertIn("demo evidence commit contains unrelated paths", normalized)
        self.assertIn("demo evidence commit contains unrelated paths", module_help)
        self.assertLess(
            normalized.index("tools/release.py bump"),
            normalized.index("tools/release.py refresh-evidence"),
        )


if __name__ == "__main__":
    unittest.main()
