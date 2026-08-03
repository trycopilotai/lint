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
IMAGE_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"


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

    def encode(self, document) -> tuple[str, bytes]:
        payload = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest(), payload

    def image_blobs(
        self,
        operating_system: str = "linux",
        architecture: str = "amd64",
        layers: tuple[int, ...] = (10, 20),
    ) -> tuple[str, dict[str, bytes]]:
        """Build one image manifest and its config, keyed by digest."""
        config_digest, config_payload = self.encode(
            {
                "architecture": architecture,
                "os": operating_system,
                "rootfs": {"diff_ids": [], "type": "layers"},
            }
        )
        manifest_digest, manifest_payload = self.encode(
            {
                "schemaVersion": 2,
                "mediaType": IMAGE_MEDIA_TYPE,
                "config": {
                    "mediaType": "application/vnd.oci.image.config.v1+json",
                    "digest": f"sha256:{config_digest}",
                    "size": len(config_payload),
                },
                "layers": [{"size": size} for size in layers],
            }
        )
        blobs = {
            config_digest: config_payload,
            manifest_digest: manifest_payload,
        }
        return manifest_digest, blobs

    def image_descriptor(self, digest: str, platform=None) -> dict:
        """Build the descriptor Buildx writes for an exported image."""
        descriptor = {
            "mediaType": IMAGE_MEDIA_TYPE,
            "digest": f"sha256:{digest}",
            "size": 500,
            "annotations": {"org.opencontainers.image.created": "1980-01-01T00:00:00Z"},
        }
        if platform is not None:
            descriptor["platform"] = platform
        return descriptor

    def attestation_descriptor(self, subject: str) -> dict:
        """Build an attestation descriptor with no blob behind it.

        Leaving the blob out of the layout proves the verifier skips
        the descriptor instead of reading and rejecting it.
        """
        return {
            "mediaType": IMAGE_MEDIA_TYPE,
            "digest": "sha256:" + "e" * 64,
            "size": 300,
            "annotations": {
                "vnd.docker.reference.digest": f"sha256:{subject}",
                "vnd.docker.reference.type": "attestation-manifest",
            },
            "platform": {"architecture": "unknown", "os": "unknown"},
        }

    def write_layout(
        self,
        directory: str,
        manifests: list,
        blobs: dict[str, bytes],
    ) -> Path:
        """Write one OCI layout tar the way the image export does."""
        index = {
            "schemaVersion": 2,
            "mediaType": INDEX_MEDIA_TYPE,
            "manifests": manifests,
        }
        archive = Path(directory) / "image.oci.tar"
        members = [("index.json", json.dumps(index).encode("utf-8"))]
        for digest, payload in blobs.items():
            members.append((f"blobs/sha256/{digest}", payload))
        with tarfile.open(archive, "w") as handle:
            for name, payload in members:
                member = tarfile.TarInfo(name)
                member.size = len(payload)
                handle.addfile(member, io.BytesIO(payload))
        return archive

    def test_reads_compressed_layers_from_local_oci_layout(self) -> None:
        digest, blobs = self.image_blobs()

        with tempfile.TemporaryDirectory() as directory:
            archive = self.write_layout(
                directory,
                [self.image_descriptor(digest)],
                blobs,
            )

            result = self.module.verify_oci_layout(
                archive,
                "linux/amd64",
                1,
            )

        self.assertEqual(30, result["compressed_layer_bytes"])
        self.assertEqual(f"sha256:{digest}", result["platform_digest"])
        self.assertEqual("ok", result["status"])

    def test_local_oci_layout_skips_an_attestation_descriptor(self) -> None:
        digest, blobs = self.image_blobs()

        with tempfile.TemporaryDirectory() as directory:
            archive = self.write_layout(
                directory,
                [
                    self.image_descriptor(digest),
                    self.attestation_descriptor(digest),
                ],
                blobs,
            )

            result = self.module.verify_oci_layout(
                archive,
                "linux/amd64",
                1,
            )

        self.assertEqual(30, result["compressed_layer_bytes"])
        self.assertEqual(f"sha256:{digest}", result["platform_digest"])

    def index_blob(self, manifests: list) -> tuple[str, bytes]:
        """Build the image index Buildx nests under index.json."""
        return self.encode(
            {
                "schemaVersion": 2,
                "mediaType": INDEX_MEDIA_TYPE,
                "manifests": manifests,
            }
        )

    def index_descriptor(self, digest: str, size: int) -> dict:
        return {
            "mediaType": INDEX_MEDIA_TYPE,
            "digest": f"sha256:{digest}",
            "size": size,
            "annotations": {"org.opencontainers.image.ref.name": "lint-xml:ci"},
        }

    def test_local_oci_layout_descends_an_attested_index(self) -> None:
        digest, blobs = self.image_blobs()
        index_digest, index_payload = self.index_blob(
            [
                self.image_descriptor(
                    digest,
                    {"architecture": "amd64", "os": "linux"},
                ),
                self.attestation_descriptor(digest),
            ]
        )
        blobs[index_digest] = index_payload

        with tempfile.TemporaryDirectory() as directory:
            archive = self.write_layout(
                directory,
                [self.index_descriptor(index_digest, len(index_payload))],
                blobs,
            )

            result = self.module.verify_oci_layout(
                archive,
                "linux/amd64",
                1,
            )

        self.assertEqual(30, result["compressed_layer_bytes"])
        self.assertEqual(f"sha256:{digest}", result["platform_digest"])
        self.assertEqual("ok", result["status"])

    def test_local_oci_layout_rejects_a_nested_index_mismatch(self) -> None:
        digest, blobs = self.image_blobs()
        index_digest, index_payload = self.index_blob(
            [
                self.image_descriptor(digest),
                self.attestation_descriptor(digest),
            ]
        )
        blobs[index_digest] = index_payload + b" "

        with tempfile.TemporaryDirectory() as directory:
            archive = self.write_layout(
                directory,
                [self.index_descriptor(index_digest, len(index_payload))],
                blobs,
            )

            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                self.module.verify_oci_layout(
                    archive,
                    "linux/amd64",
                    1,
                )

    def test_local_oci_layout_keeps_an_annotated_platform(self) -> None:
        amd64_digest, amd64_blobs = self.image_blobs(architecture="amd64")
        arm64_digest, arm64_blobs = self.image_blobs(architecture="arm64")
        blobs = dict(amd64_blobs)
        blobs.update(arm64_blobs)

        with tempfile.TemporaryDirectory() as directory:
            archive = self.write_layout(
                directory,
                [
                    self.image_descriptor(
                        amd64_digest,
                        {"architecture": "amd64", "os": "linux"},
                    ),
                    self.image_descriptor(
                        arm64_digest,
                        {"architecture": "arm64", "os": "linux"},
                    ),
                ],
                blobs,
            )

            result = self.module.verify_oci_layout(
                archive,
                "linux/arm64",
                1,
            )

        self.assertEqual(f"sha256:{arm64_digest}", result["platform_digest"])

    def test_local_oci_layout_rejects_a_digest_mismatch(self) -> None:
        digest = "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            archive = self.write_layout(
                directory,
                [
                    self.image_descriptor(
                        digest,
                        {"architecture": "arm64", "os": "linux"},
                    )
                ],
                {digest: b"{}"},
            )

            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                self.module.verify_oci_layout(
                    archive,
                    "linux/arm64",
                    1,
                )

    def test_local_oci_layout_rejects_a_config_digest_mismatch(self) -> None:
        digest, blobs = self.image_blobs()
        tampered = {}
        for blob_digest, payload in blobs.items():
            if blob_digest == digest:
                tampered[blob_digest] = payload
            else:
                tampered[blob_digest] = payload + b" "

        with tempfile.TemporaryDirectory() as directory:
            archive = self.write_layout(
                directory,
                [self.image_descriptor(digest)],
                tampered,
            )

            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                self.module.verify_oci_layout(
                    archive,
                    "linux/amd64",
                    1,
                )

    def test_local_oci_layout_rejects_a_config_platform_mismatch(self) -> None:
        digest, blobs = self.image_blobs(architecture="arm64")

        with tempfile.TemporaryDirectory() as directory:
            archive = self.write_layout(
                directory,
                [self.image_descriptor(digest)],
                blobs,
            )

            with self.assertRaisesRegex(ValueError, "declares linux/arm64"):
                self.module.verify_oci_layout(
                    archive,
                    "linux/amd64",
                    1,
                )

    def test_local_oci_layout_rejects_zero_candidates(self) -> None:
        digest, blobs = self.image_blobs()

        with tempfile.TemporaryDirectory() as directory:
            archive = self.write_layout(
                directory,
                [self.attestation_descriptor(digest)],
                blobs,
            )

            with self.assertRaisesRegex(ValueError, "found 0"):
                self.module.verify_oci_layout(
                    archive,
                    "linux/amd64",
                    1,
                )

    def test_local_oci_layout_rejects_ambiguous_candidates(self) -> None:
        first_digest, first_blobs = self.image_blobs(layers=(10, 20))
        second_digest, second_blobs = self.image_blobs(layers=(30, 40))
        blobs = dict(first_blobs)
        blobs.update(second_blobs)

        with tempfile.TemporaryDirectory() as directory:
            archive = self.write_layout(
                directory,
                [
                    self.image_descriptor(first_digest),
                    self.image_descriptor(second_digest),
                ],
                blobs,
            )

            with self.assertRaisesRegex(ValueError, "found 2"):
                self.module.verify_oci_layout(
                    archive,
                    "linux/amd64",
                    1,
                )


if __name__ == "__main__":
    unittest.main()
