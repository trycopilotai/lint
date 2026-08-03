from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None:
        raise RuntimeError(f"could not create specification for {path}")
    if specification.loader is None:
        raise RuntimeError(f"specification has no loader for {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


VERIFIER = load_module(
    "image_contents_verifier_under_test",
    ROOT / "images" / "verify_images.py",
)
sys.path.insert(0, str(ROOT / "images"))
GENERATOR = load_module(
    "image_contents_generator_under_test",
    ROOT / "images" / "generate_image_inventory.py",
)


def regular_entry(
    path: str,
    mode: str,
    payload: bytes,
    role: str,
) -> dict[str, object]:
    return {
        "path": path,
        "type": "regular",
        "mode": mode,
        "link_target": None,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "role": role,
    }


def sample_inventory() -> dict[str, object]:
    entries = [
        regular_entry("julia-format.jl", "0555", b"entrypoint\n", "entrypoint"),
        regular_entry("licenses/lint/LICENSE", "0444", b"license\n", "license"),
        regular_entry(
            "licenses/lint/THIRD_PARTY_NOTICES.md",
            "0444",
            b"notices\n",
            "license",
        ),
        regular_entry(
            "licenses/julia/manifest.json",
            "0644",
            b"{}\n",
            "license",
        ),
        {
            "path": "opt",
            "type": "directory",
            "mode": "0755",
            "link_target": None,
            "sha256": None,
            "role": "formatter",
        },
        {
            "path": "usr",
            "type": "directory",
            "mode": "0755",
            "link_target": None,
            "sha256": None,
            "role": "runtime",
        },
        {
            "path": "usr/local/julia/lib/current",
            "type": "symlink",
            "mode": "0777",
            "link_target": "libjulia.so",
            "sha256": None,
            "role": "runtime",
        },
        regular_entry(
            "usr/local/julia/lib/libjulia.so",
            "0555",
            b"runtime\n",
            "runtime",
        ),
    ]
    entries.sort(key=lambda entry: entry["path"])
    return {
        "schema_version": 1,
        "target": "julia",
        "architecture": "amd64",
        "entries": entries,
    }


def add_tar_entry(
    handle: tarfile.TarFile,
    path: str,
    entry_type: bytes = tarfile.REGTYPE,
    mode: int = 0o644,
    payload: bytes = b"",
    link_target: str = "",
) -> None:
    member = tarfile.TarInfo(path)
    member.type = entry_type
    member.mode = mode
    member.linkname = link_target
    if entry_type in {tarfile.REGTYPE, tarfile.AREGTYPE}:
        member.size = len(payload)
        handle.addfile(member, io.BytesIO(payload))
        return
    handle.addfile(member)


def layer_payload(entries: list[dict[str, object]]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as handle:
        for entry in entries:
            add_tar_entry(handle, **entry)
    return output.getvalue()


def write_saved_image(path: Path, layers: list[bytes]) -> None:
    records = []
    with tarfile.open(path, "w") as handle:
        for index, payload in enumerate(layers):
            name = f"layer-{index}/layer.tar"
            records.append(name)
            add_tar_entry(handle, name, payload=payload)
        manifest = json.dumps([{"Config": "config.json", "Layers": records}]).encode(
            "utf-8"
        )
        add_tar_entry(handle, "manifest.json", payload=manifest)


class LicenseNoticeTest(unittest.TestCase):
    def test_every_final_stage_copies_the_license_notices(self) -> None:
        matrix = json.loads(
            (ROOT / "images" / "matrix.json").read_text(encoding="utf-8")
        )
        dockerfile = (ROOT / "images" / "Dockerfile").read_text(encoding="utf-8")
        for row in matrix["images"]:
            target = row["target"]
            pattern = (
                rf"^FROM [^\n]+ AS {re.escape(target)}\n" rf"(?P<body>.*?)(?=^FROM |\Z)"
            )
            matched = re.search(pattern, dockerfile, flags=re.MULTILINE | re.DOTALL)
            with self.subTest(target=target):
                self.assertIsNotNone(matched)
                assert matched is not None
                self.assertIn(
                    "COPY --from=license-notices /licenses /licenses",
                    matched.group("body"),
                )

    def test_notice_names_every_final_target(self) -> None:
        matrix = json.loads(
            (ROOT / "images" / "matrix.json").read_text(encoding="utf-8")
        )
        notice = (ROOT / "images" / "THIRD_PARTY_NOTICES.md").read_text(
            encoding="utf-8"
        )
        for row in matrix["images"]:
            with self.subTest(target=row["target"]):
                self.assertIn(f"## {row['target']}\n", notice)


class StrictInventoryTest(unittest.TestCase):
    def test_role_schema_and_policy_cover_every_image_target(self) -> None:
        matrix = json.loads(
            (ROOT / "images" / "matrix.json").read_text(encoding="utf-8")
        )
        targets = {row["target"] for row in matrix["images"]}

        self.assertEqual(
            {"entrypoint", "formatter", "license", "runtime"},
            VERIFIER.ENTRY_ROLES,
        )
        self.assertEqual(targets, set(VERIFIER.PATH_ROLE_POLICY))
        VERIFIER.validate_coverage()

    def test_distroless_root_paths_are_reviewed_runtime(self) -> None:
        for target in ("java", "swift"):
            with self.subTest(target=target):
                for path in (".", "boot", "boot/runtime", "dev", "dev/null"):
                    with self.subTest(target=target, path=path):
                        self.assertEqual(
                            "runtime",
                            VERIFIER.classify_path_role(target, path),
                        )

    def test_tool_identity_has_one_common_exact_formatter_role(self) -> None:
        for target, rules in VERIFIER.PATH_ROLE_POLICY.items():
            with self.subTest(target=target):
                matching = [rule for rule in rules if "lint-tool-version" in rule.exact]
                self.assertEqual([VERIFIER.TOOL_VERSION_RULE], matching)
                self.assertEqual(
                    "formatter",
                    VERIFIER.classify_path_role(target, "lint-tool-version"),
                )

    def test_black_inventory_rejects_generated_package_bytecode(self) -> None:
        inventory = {
            "schema_version": 1,
            "target": "black",
            "architecture": "amd64",
            "entries": [
                regular_entry(
                    "usr/local/lib/python3.13/site-packages/black/"
                    "__pycache__/cache.cpython-313.pyc",
                    "0644",
                    b"generated\n",
                    "formatter",
                )
            ],
        }

        with self.assertRaisesRegex(
            ValueError,
            "generated package bytecode",
        ):
            VERIFIER.validate_inventory(inventory)

    def test_xml_formatter_parent_is_reviewed(self) -> None:
        for path in ("opt", "opt/libxml2", "opt/libxml2/lib/libxml2.so"):
            with self.subTest(path=path):
                self.assertEqual(
                    "formatter",
                    VERIFIER.classify_path_role("xml", path),
                )

    def test_arbitrary_tool_metadata_paths_remain_unclassified(self) -> None:
        for target in VERIFIER.PATH_ROLE_POLICY:
            with self.subTest(target=target):
                with self.assertRaisesRegex(ValueError, "no reviewed role"):
                    VERIFIER.classify_path_role(
                        target,
                        "lint-tool-version-extra",
                    )

    def test_every_bound_entry_field_rejects_a_mutation(self) -> None:
        expected = sample_inventory()
        mutations = {
            "path": (
                "usr/local/julia/lib/libjulia.so",
                "path",
                "usr/local/julia/lib/renamed.so",
            ),
            "type": ("usr", "type", "fifo"),
            "mode": ("usr/local/julia/lib/libjulia.so", "mode", "0755"),
            "link_target": (
                "usr/local/julia/lib/current",
                "link_target",
                "other.so",
            ),
            "sha256": (
                "usr/local/julia/lib/libjulia.so",
                "sha256",
                "f" * 64,
            ),
        }
        for field, (path, key, value) in mutations.items():
            with self.subTest(field=field):
                actual = copy.deepcopy(expected)
                entry = next(item for item in actual["entries"] if item["path"] == path)
                entry[key] = value
                actual["entries"].sort(key=lambda entry: entry["path"])
                with self.assertRaisesRegex(ValueError, "image inventory"):
                    VERIFIER.compare_inventories(expected, actual)

    def test_altered_invalid_and_unclassified_roles_are_rejected(self) -> None:
        expected = sample_inventory()
        cases = (
            ("usr/local/julia/lib/libjulia.so", "formatter", "wrong role"),
            ("usr/local/julia/lib/libjulia.so", "unknown", "invalid role"),
            ("unreviewed/bin/tool", "runtime", "no reviewed role"),
        )
        for path, role, message in cases:
            with self.subTest(message=message):
                actual = copy.deepcopy(expected)
                if path == "unreviewed/bin/tool":
                    actual["entries"].append(
                        regular_entry(path, "0755", b"tool\n", role)
                    )
                else:
                    entry = next(
                        item for item in actual["entries"] if item["path"] == path
                    )
                    entry["role"] = role
                actual["entries"].sort(key=lambda entry: entry["path"])
                with self.assertRaisesRegex(ValueError, "role"):
                    VERIFIER.compare_inventories(expected, actual)

    def test_missing_entry_is_rejected(self) -> None:
        expected = sample_inventory()
        actual = copy.deepcopy(expected)
        actual["entries"].pop()

        with self.assertRaisesRegex(ValueError, "missing="):
            VERIFIER.compare_inventories(expected, actual)

    def test_arbitrary_executable_is_rejected_as_an_extra_entry(self) -> None:
        expected = sample_inventory()
        actual = copy.deepcopy(expected)
        actual["entries"].append(
            regular_entry(
                "usr/local/julia/bin/arbitrary",
                "0755",
                b"unexpected\n",
                "runtime",
            )
        )
        actual["entries"].sort(key=lambda entry: entry["path"])

        with self.assertRaisesRegex(ValueError, "usr/local/julia/bin/arbitrary"):
            VERIFIER.compare_inventories(expected, actual)

    def test_target_and_architecture_are_bound(self) -> None:
        expected = sample_inventory()
        for field, value in (("target", "black"), ("architecture", "arm64")):
            with self.subTest(field=field):
                actual = copy.deepcopy(expected)
                actual[field] = value
                with self.assertRaises(ValueError):
                    VERIFIER.compare_inventories(expected, actual)

    def test_manifest_requires_canonical_bytes(self) -> None:
        inventory = sample_inventory()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = VERIFIER.inventory_path("julia", "amd64", root=root)
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(VERIFIER.render_inventory(inventory) + b"\n")
            with mock.patch.object(VERIFIER, "INVENTORY_ROOT", root):
                with self.assertRaisesRegex(ValueError, "canonically encoded"):
                    VERIFIER.load_inventory("julia", "amd64")

    def test_required_notices_remain_an_independent_invariant(self) -> None:
        inventory = sample_inventory()
        VERIFIER.validate_required_notices("fixture:1", inventory)
        required = set(VERIFIER.REQUIRED_LICENSE_PATHS)
        required.add("licenses/julia/manifest.json")
        for notice in required:
            with self.subTest(notice=notice):
                changed = copy.deepcopy(inventory)
                changed["entries"] = [
                    entry for entry in changed["entries"] if entry["path"] != notice
                ]
                with self.assertRaisesRegex(ValueError, "license notices"):
                    VERIFIER.validate_required_notices("fixture:1", changed)


class InventoryGenerationTest(unittest.TestCase):
    def test_saved_layers_bind_type_mode_link_digest_and_role(self) -> None:
        layer = layer_payload(
            [
                {
                    "path": "./usr",
                    "entry_type": tarfile.DIRTYPE,
                    "mode": 0o755,
                },
                {
                    "path": "./usr/local/julia/lib/tool",
                    "mode": 0o555,
                    "payload": b"tool payload",
                },
                {
                    "path": "./usr/local/julia/lib/tool-link",
                    "entry_type": tarfile.SYMTYPE,
                    "mode": 0o777,
                    "link_target": "tool",
                },
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "image.tar"
            write_saved_image(archive, [layer])
            inventory = VERIFIER.inventory_from_saved_image(
                archive,
                "julia",
                "amd64",
            )

        entries = {entry["path"]: entry for entry in inventory["entries"]}
        self.assertEqual(
            ["usr", "usr/local/julia/lib/tool", "usr/local/julia/lib/tool-link"],
            sorted(entries),
        )
        self.assertEqual("directory", entries["usr"]["type"])
        self.assertEqual("0555", entries["usr/local/julia/lib/tool"]["mode"])
        self.assertEqual(
            hashlib.sha256(b"tool payload").hexdigest(),
            entries["usr/local/julia/lib/tool"]["sha256"],
        )
        self.assertEqual(
            "tool",
            entries["usr/local/julia/lib/tool-link"]["link_target"],
        )
        self.assertEqual("runtime", entries["usr/local/julia/lib/tool"]["role"])

    def test_layer_whiteouts_and_opaque_directories_remove_lower_entries(
        self,
    ) -> None:
        base = layer_payload(
            [
                {"path": "usr", "entry_type": tarfile.DIRTYPE},
                {"path": "usr/local/julia/delete-me", "payload": b"old"},
                {
                    "path": "usr/local/julia/opaque",
                    "entry_type": tarfile.DIRTYPE,
                },
                {"path": "usr/local/julia/opaque/old", "payload": b"old"},
            ]
        )
        upper = layer_payload(
            [
                {"path": "usr/local/julia/.wh.delete-me"},
                {"path": "usr/local/julia/opaque/.wh..wh..opq"},
                {"path": "usr/local/julia/opaque/new", "payload": b"new"},
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "image.tar"
            write_saved_image(archive, [base, upper])
            inventory = VERIFIER.inventory_from_saved_image(
                archive,
                "julia",
                "amd64",
            )

        paths = {entry["path"] for entry in inventory["entries"]}
        self.assertNotIn("usr/local/julia/delete-me", paths)
        self.assertNotIn("usr/local/julia/opaque/old", paths)
        self.assertIn("usr/local/julia/opaque/new", paths)
        self.assertFalse(any(".wh." in path for path in paths))

    def test_independent_container_state_cannot_affect_layer_inventory(self) -> None:
        layer = layer_payload(
            [
                {"path": "usr", "entry_type": tarfile.DIRTYPE},
                {"path": "usr/local/julia/bin/tool", "payload": b"tool"},
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            saved = Path(directory) / "saved.tar"
            write_saved_image(saved, [layer])
            commands: list[list[str]] = []

            def run(command: list[str], **kwargs):
                commands.append(command)
                if command[:3] == ["docker", "image", "inspect"]:
                    return subprocess.CompletedProcess(
                        args=command,
                        returncode=0,
                        stdout="amd64\n",
                        stderr="",
                    )
                if command[:3] == ["docker", "image", "save"]:
                    shutil.copyfile(saved, command[4])
                    return subprocess.CompletedProcess(
                        args=command,
                        returncode=0,
                        stdout="",
                        stderr="",
                    )
                raise AssertionError(f"unexpected Docker command: {command}")

            with mock.patch.object(VERIFIER.subprocess, "run", side_effect=run):
                first = VERIFIER.export_image_inventory(
                    "fixture:first-container",
                    "julia",
                    "amd64",
                )
                second = VERIFIER.export_image_inventory(
                    "fixture:second-container",
                    "julia",
                    "amd64",
                )

        self.assertEqual(first, second)
        self.assertFalse(any("container" in command for command in commands))

    def test_generator_writes_only_the_canonical_target_architecture_path(self) -> None:
        inventory = sample_inventory()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(
                GENERATOR.verify_images,
                "export_image_inventory",
                return_value=inventory,
            ):
                path, payload = GENERATOR.write_inventory(
                    "fixture:1",
                    "julia",
                    "amd64",
                    root=root,
                )

            self.assertEqual(root / "julia-amd64.json", path)
            self.assertEqual(VERIFIER.render_inventory(inventory), payload)
            self.assertEqual(payload, path.read_bytes())
            self.assertEqual(payload, VERIFIER.render_inventory(json.loads(payload)))


class WorkflowInventoryTest(unittest.TestCase):
    # One content check per canonical architecture, in the order
    # the inventories are checked in. Listing the architectures
    # here instead would let the workflows ask for an inventory
    # the repository does not ship, which is a failure the
    # release job only reaches after it has pushed the images.
    def test_image_jobs_bind_the_exact_target_and_architecture(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "images.yml").read_text(
            encoding="utf-8"
        )
        architectures = sorted(VERIFIER.CANONICAL_INVENTORY_ARCHITECTURES)

        blocks = workflow.split("python3 images/verify_images.py")[1:]
        self.assertEqual(len(architectures), len(blocks))
        for architecture, block in zip(architectures, blocks, strict=True):
            with self.subTest(architecture=architecture):
                invocation = "\n".join(block.splitlines()[:4])
                self.assertIn('matrix.target\n          }}"', invocation)
                self.assertIn(f'--architecture "{architecture}"', invocation)

    def test_release_jobs_bind_the_exact_target_and_architecture(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        architectures = sorted(VERIFIER.CANONICAL_INVENTORY_ARCHITECTURES)

        blocks = workflow.split("python3 images/verify_images.py")[1:]
        self.assertEqual(len(architectures), len(blocks))
        for architecture, block in zip(architectures, blocks, strict=True):
            with self.subTest(architecture=architecture):
                invocation = "\n".join(block.splitlines()[:6])
                self.assertIn('--target "$TARGET"', invocation)
                self.assertIn(f'--architecture "{architecture}"', invocation)


if __name__ == "__main__":
    unittest.main()
