from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "images" / "verify_registry_size.py"
    specification = importlib.util.spec_from_file_location(
        "registry_size_under_test",
        path,
    )
    if specification is None:
        raise RuntimeError("could not create registry size specification")
    if specification.loader is None:
        raise RuntimeError("registry size specification has no loader")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


class RegistryImageSizeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def index(self):
        return {
            "schemaVersion": 2,
            "manifests": [
                {
                    "digest": "sha256:" + "a" * 64,
                    "platform": {
                        "architecture": "amd64",
                        "os": "linux",
                    },
                },
                {
                    "digest": "sha256:" + "b" * 64,
                    "platform": {
                        "architecture": "arm64",
                        "os": "linux",
                    },
                },
                {
                    "digest": "sha256:" + "c" * 64,
                    "platform": {
                        "architecture": "unknown",
                        "os": "unknown",
                    },
                },
            ],
        }

    def test_selects_the_exact_platform_manifest(self) -> None:
        digest = self.module.platform_manifest_digest(
            self.index(),
            "linux/arm64",
        )

        self.assertEqual("sha256:" + "b" * 64, digest)

    def test_missing_or_duplicate_platform_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "found 0"):
            self.module.platform_manifest_digest(
                self.index(),
                "linux/s390x",
            )

        duplicate = self.index()
        duplicate["manifests"].append(duplicate["manifests"][0])
        with self.assertRaisesRegex(ValueError, "found 2"):
            self.module.platform_manifest_digest(
                duplicate,
                "linux/amd64",
            )

    def test_sums_only_compressed_registry_layers(self) -> None:
        manifest = {
            "schemaVersion": 2,
            "config": {"size": 99},
            "layers": [
                {"size": 10},
                {"size": 20},
                {"size": 30},
            ],
        }

        self.assertEqual(60, self.module.compressed_layer_bytes(manifest))

    def test_malformed_layer_sizes_are_rejected(self) -> None:
        for value in (-1, "10", None):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError,
                "layer size",
            ):
                self.module.compressed_layer_bytes(
                    {"schemaVersion": 2, "layers": [{"size": value}]}
                )

    def test_budget_is_a_hard_upper_bound(self) -> None:
        self.module.require_budget(1024 * 1024, 1)

        with self.assertRaisesRegex(ValueError, "budget"):
            self.module.require_budget(1024 * 1024 + 1, 1)

    def test_reads_compressed_layers_from_local_oci_layout(self) -> None:
        manifest = {
            "schemaVersion": 2,
            "config": {"size": 99},
            "layers": [{"size": 10}, {"size": 20}],
        }
        manifest_payload = json.dumps(
            manifest,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(manifest_payload).hexdigest()
        index = {
            "schemaVersion": 2,
            "manifests": [
                {
                    "digest": f"sha256:{digest}",
                    "platform": {
                        "architecture": "amd64",
                        "os": "linux",
                    },
                }
            ],
        }
        index_payload = json.dumps(index).encode("utf-8")

        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "image.oci.tar"
            with tarfile.open(archive, "w") as handle:
                for name, payload in (
                    ("index.json", index_payload),
                    (f"blobs/sha256/{digest}", manifest_payload),
                ):
                    member = tarfile.TarInfo(name)
                    member.size = len(payload)
                    handle.addfile(member, io.BytesIO(payload))

            result = self.module.verify_oci_layout(
                archive,
                "linux/amd64",
                1,
            )

        self.assertEqual(30, result["compressed_layer_bytes"])
        self.assertEqual(f"sha256:{digest}", result["platform_digest"])
        self.assertEqual("ok", result["status"])

    def test_local_oci_layout_rejects_a_digest_mismatch(self) -> None:
        digest = "a" * 64
        index = {
            "schemaVersion": 2,
            "manifests": [
                {
                    "digest": f"sha256:{digest}",
                    "platform": {
                        "architecture": "arm64",
                        "os": "linux",
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "image.oci.tar"
            with tarfile.open(archive, "w") as handle:
                for name, payload in (
                    ("index.json", json.dumps(index).encode("utf-8")),
                    (f"blobs/sha256/{digest}", b"{}"),
                ):
                    member = tarfile.TarInfo(name)
                    member.size = len(payload)
                    handle.addfile(member, io.BytesIO(payload))

            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                self.module.verify_oci_layout(
                    archive,
                    "linux/arm64",
                    1,
                )


if __name__ == "__main__":
    unittest.main()
