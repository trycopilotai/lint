from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_DOWNLOADS = {
    "black": (
        "https://files.pythonhosted.org/packages/b1/28/"
        "9dd29175c1db777e6189e2a0bb5f37101adf19c3e509b236c8920f778d0b/"
        "black-26.10.1-py3-none-any.whl",
        "28842f9a8207cc1df6eb983a35a14c5a0dfcd603d214fe82d84bef552afd2e3a",
    ),
    "black_click": (
        "https://files.pythonhosted.org/packages/00/2e/"
        "d53fa4befbf2cfa713304affc7ca780ce4fc1fd8710527771b58311a3229/"
        "click-8.1.7-py3-none-any.whl",
        "ae74fb96c20a0277a1d615f1e4d73c8414f5a98db8b799a7931d1582f3390c28",
    ),
    "black_mypy_extensions": (
        "https://files.pythonhosted.org/packages/2a/e2/"
        "5d3f6ada4297caebe1a2add3b126fe800c96f56dbe5d1988a2cbe0b267aa/"
        "mypy_extensions-1.0.0-py3-none-any.whl",
        "4392f6c0eb8a5668a69e23d168ffa70f0be9ccfd32b5cc2d26a34ae5b844552d",
    ),
    "black_packaging": (
        "https://files.pythonhosted.org/packages/08/aa/"
        "cc0199a5f0ad350994d660967a8efb233fe0416e4639146c089643407ce6/"
        "packaging-24.1-py3-none-any.whl",
        "5b8f2217dbdbd2f7f384c41c628544e6d52f2d0f53c6d0c3ea61aa5d1d7ff124",
    ),
    "black_pathspec": (
        "https://files.pythonhosted.org/packages/f1/d9/"
        "7fb5aa316bc299258e68c73ba3bddbc499654a07f151cba08f6153988714/"
        "pathspec-1.1.1-py3-none-any.whl",
        "a00ce642f577bf7f473932318056212bc4f8bfdf53128c78bbd5af0b9b20b189",
    ),
    "black_platformdirs": (
        "https://files.pythonhosted.org/packages/3c/a6/"
        "bc1012356d8ece4d66dd75c4b9fc6c1f6650ddd5991e421177d9f8f671be/"
        "platformdirs-4.3.6-py3-none-any.whl",
        "73e575e1408ab8103900836b97580d5307456908a03e92031bab39e4554cc3fb",
    ),
    "black_pytokens": (
        "https://files.pythonhosted.org/packages/7c/3c/"
        "6941a82f4f130af6e1c68c076b6789069ef10c04559bd4733650f902fd3b/"
        "pytokens-0.4.0-py3-none-any.whl",
        "0508d11b4de157ee12063901603be87fb0253e8f4cb9305eb168b1202ab92068",
    ),
    "julia_formatter_source": (
        "https://pkg.julialang.org/package/"
        "98e50ef6-434e-11e9-1051-2b60c6c9e899/"
        "e421b4f670555ff650f1b77df47cd42c806d8372",
        "6db065256d064086edc3519aa55937505180c7183a9ffbe68814fff49f0022cc",
    ),
    "prettier": (
        "https://registry.npmjs.org/prettier/-/prettier-3.7.4.tgz",
        "7e63a83230572391c5b1af4fcbac7273850c898652e680b6824b41e8f64aa4e6",
    ),
    "swift_format_source": (
        "https://github.com/swiftlang/swift-format/archive/refs/tags/603.0.0.tar.gz",
        "39732a76ba0d86dca799b8c92f3516398aaa712e1ec489c9e439219e72feac1f",
    ),
}

BLACK_NOTICE_LINES = (
    "Click 8.1.7: [BSD-3-Clause][click-license]",
    "mypy-extensions 1.0.0: [MIT][mypy-extensions-license]",
    "packaging 24.1: [Apache-2.0 OR",
    "pathspec 1.1.1: [MPL-2.0][pathspec-license]",
    "platformdirs 4.3.6: [MIT][platformdirs-license]",
    "pytokens 0.4.0: [MIT][pytokens-license]",
)

JULIA_RUNTIME_NOTICES = {
    "CommonMark": "CommonMark 1.0.3: [MIT][commonmark-license]",
    "Glob": "Glob 1.5.0: [MIT][glob-license]",
    "JuliaSyntax": "JuliaSyntax 1.0.2: [MIT][juliasyntax-license]",
    "PrecompileTools": ("PrecompileTools 1.3.4: [MIT][precompiletools-license]"),
    "Preferences": "Preferences 1.5.2: [MIT][preferences-license]",
}

JULIA_STDLIB_PACKAGES = {
    "Base64",
    "Dates",
    "InteractiveUtils",
    "JuliaSyntaxHighlighting",
    "Logging",
    "Markdown",
    "Printf",
    "Random",
    "SHA",
    "Serialization",
    "StyledStrings",
    "TOML",
    "Test",
    "Unicode",
}


def stage(dockerfile: str, name: str) -> str:
    pattern = rf"^FROM [^\n]+ AS {re.escape(name)}\n(?P<body>.*?)(?=^FROM |\Z)"
    matched = re.search(pattern, dockerfile, flags=re.MULTILINE | re.DOTALL)
    if matched is None:
        raise AssertionError(f"Dockerfile stage is missing: {name}")
    return matched.group("body")


@unittest.skipUnless(
    shutil.which("dpkg-deb") and shutil.which("sh"),
    "Debian package tools are unavailable",
)
class DebianPackageLayerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="lint-debian-layer-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def run_layer(
        self,
        *,
        version: str = "1:1.3.dfsg+really1.3.1-1+deb13u1",
        architecture: str = "amd64",
        corrupt_archive_checksum: bool = False,
        corrupt_file_checksum: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        package = self.root / "package"
        control = package / "DEBIAN"
        control.mkdir(parents=True)
        (control / "control").write_text(
            "Package: zlib1g\n"
            f"Version: {version}\n"
            f"Architecture: {architecture}\n"
            "Maintainer: Test <test@example.invalid>\n"
            "Description: synthetic package fixture\n",
            encoding="utf-8",
        )
        relative = Path("usr/lib/x86_64-linux-gnu/libz.so.1.3.1")
        payload = package / relative
        payload.parent.mkdir(parents=True)
        payload.write_bytes(b"synthetic library fixture\n")
        payload.with_name("libz.so.1").symlink_to(payload.name)
        digest = hashlib.md5(payload.read_bytes(), usedforsecurity=False).hexdigest()
        if corrupt_file_checksum:
            digest = "0" * 32
        (control / "md5sums").write_text(f"{digest}  {relative}\n", encoding="utf-8")
        archive = self.root / "fixture.deb"
        subprocess.run(
            ["dpkg-deb", "--build", "--root-owner-group", str(package), str(archive)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        if corrupt_archive_checksum:
            checksum = "0" * 64
        tools = self.root / "bin"
        tools.mkdir()
        curl = tools / "curl"
        curl.write_text(
            "#!/bin/sh\ncp " + shlex.quote(str(archive)) + ' "$4"\n',
            encoding="utf-8",
        )
        curl.chmod(0o755)
        body = stage((ROOT / "images/Dockerfile").read_text(), "distroless-zlib")
        command = body.split("RUN set -eu;", 1)[1]
        command = "set -eu;" + command
        for pinned in (
            "8c27c342230503b73dfde629642c3b352e87b554e06d60cf58772f236a305ceb",
            "0e41b3d7ad8ad7fcd36dd4d61ca91a70018478fc4af652e044f4be770784ddff",
        ):
            command = command.replace(pinned, checksum)
        for path in ("/zlib.deb", "/runtime", "/zlib-control"):
            command = command.replace(path, str(self.root) + path)
        environment = dict(os.environ)
        environment["TARGETARCH"] = "amd64"
        environment["PATH"] = str(tools) + os.pathsep + environment["PATH"]
        return subprocess.run(
            ["sh", "-c", command],
            env=environment,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )

    def test_package_files_and_original_metadata_are_kept_together(self) -> None:
        result = self.run_layer()
        self.assertEqual(0, result.returncode, result.stderr)
        database = self.root / "runtime/var/lib/dpkg/status.d"
        expected = subprocess.check_output(
            ["dpkg-deb", "--field", str(self.root / "fixture.deb")]
        )
        self.assertEqual(expected, (database / "zlib1g").read_bytes())
        self.assertEqual(
            (self.root / "package/DEBIAN/md5sums").read_bytes(),
            (database / "zlib1g.md5sums").read_bytes(),
        )
        library = self.root / "runtime/usr/lib/x86_64-linux-gnu/libz.so.1"
        self.assertTrue(library.is_symlink())
        self.assertEqual(b"synthetic library fixture\n", library.read_bytes())

    def test_obsolete_owned_changelogs_are_removed_without_extra_tools(self) -> None:
        body = stage((ROOT / "images/Dockerfile").read_text(), "distroless-runtime")
        self.assertIn("USER 0:0", body)
        self.assertIn(
            "--mount=from=distroless-zlib,source=/bin/busybox.static,target=/busybox",
            body,
        )
        command_line = next(line.strip() for line in body.splitlines() if "[" in line)
        command = json.loads(command_line)
        self.assertEqual(
            [
                "/busybox",
                "rm",
                "-f",
                "/usr/share/doc/zlib1g/changelog.Debian.amd64.gz",
                "/usr/share/doc/zlib1g/changelog.Debian.arm64.gz",
            ],
            command,
        )
        directory = self.root / "usr/share/doc/zlib1g"
        directory.mkdir(parents=True)
        for name in ("changelog.Debian.amd64.gz", "changelog.Debian.arm64.gz"):
            (directory / name).write_bytes(b"obsolete package file\n")
        (directory / "copyright").write_bytes(b"keep\n")
        # Exercise the unchanged rm arguments with the host tool; the build's
        # temporary static BusyBox mount itself is qualified in Docker CI.
        subprocess.run(
            ["rm", *command[2:3], *(str(self.root) + path for path in command[3:])],
            check=True,
        )
        self.assertEqual(
            ["copyright"], sorted(path.name for path in directory.iterdir())
        )
        self.assertIn("USER 65532:65532", body)
        self.assertNotIn("COPY --from=distroless-zlib /bin", body)

    def test_corrupt_archive_is_rejected_before_extraction(self) -> None:
        result = self.run_layer(corrupt_archive_checksum=True)
        self.assertNotEqual(0, result.returncode)
        self.assertFalse((self.root / "runtime").exists())

    def test_old_vulnerable_package_version_is_rejected(self) -> None:
        result = self.run_layer(version="1:1.3.dfsg+really1.3.1-1+b1")
        self.assertNotEqual(0, result.returncode)
        self.assertFalse((self.root / "runtime").exists())

    def test_wrong_package_architecture_is_rejected(self) -> None:
        result = self.run_layer(architecture="arm64")
        self.assertNotEqual(0, result.returncode)
        self.assertFalse((self.root / "runtime").exists())

    def test_inconsistent_owned_file_checksum_is_rejected(self) -> None:
        result = self.run_layer(corrupt_file_checksum=True)
        self.assertNotEqual(0, result.returncode)


class SourceIntegrityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dockerfile = (ROOT / "images" / "Dockerfile").read_text(encoding="utf-8")
        cls.sources = json.loads(
            (ROOT / "images" / "sources.json").read_text(encoding="utf-8")
        )
        cls.legal_sources = json.loads(
            (ROOT / "images" / "license_sources.json").read_text(encoding="utf-8")
        )

    def test_formatter_archives_and_wheels_are_hash_bound(self) -> None:
        downloads = self.sources["downloads"]
        for name, (url, sha256) in EXPECTED_DOWNLOADS.items():
            with self.subTest(name=name):
                self.assertEqual({"url": url, "sha256": sha256}, downloads[name])
                self.assertIn(url, self.dockerfile)
                self.assertIn(sha256, self.dockerfile)

    def test_distroless_zlib_package_layer_keeps_authentic_metadata(self) -> None:
        body = stage(self.dockerfile, "distroless-zlib")
        for architecture, checksum in (
            (
                "amd64",
                "8c27c342230503b73dfde629642c3b352e87b554e06d60cf58772f236a305ceb",
            ),
            (
                "arm64",
                "0e41b3d7ad8ad7fcd36dd4d61ca91a70018478fc4af652e044f4be770784ddff",
            ),
        ):
            with self.subTest(architecture=architecture):
                record = self.sources["downloads"][
                    f"distroless_zlib_linux_{architecture}"
                ]
                self.assertEqual(checksum, record["sha256"])
                self.assertIn(checksum, body)
                self.assertTrue(record["url"].endswith(f"_{architecture}.deb"))
        self.assertIn("sha256sum -c", body)
        self.assertIn("dpkg-deb --field /zlib.deb Package", body)
        self.assertIn("dpkg-deb --field /zlib.deb Architecture", body)
        self.assertIn("1:1.3.dfsg+really1.3.1-1+deb13u1", body)
        self.assertIn("dpkg-deb --extract /zlib.deb /runtime", body)
        self.assertIn("dpkg-deb --field /zlib.deb >", body)
        self.assertIn("dpkg-deb --control /zlib.deb /zlib-control", body)
        self.assertIn("md5sum -c var/lib/dpkg/status.d/zlib1g.md5sums", body)
        runtime = stage(self.dockerfile, "distroless-runtime")
        self.assertIn("COPY --from=distroless-zlib /runtime/ /", runtime)
        for target in ("java", "swift"):
            self.assertIn(f"FROM distroless-runtime AS {target}", self.dockerfile)

    def test_csharp_runtime_security_pins_match_its_alpine_branch(self) -> None:
        base = self.sources["base_images"]["dotnet_runtime"]
        self.assertEqual(
            "mcr.microsoft.com/dotnet/runtime:10.0.14-alpine3.24",
            base["image"],
        )
        self.assertEqual(
            "sha256:8ef0660a5ae5877915bc868a27aabf334c8b14c60aadbdd0fa058d81b87c52b6",
            base["digest"],
        )
        body = stage(self.dockerfile, "csharp-runtime")
        for pin in (
            "libcrypto3=3.5.9-r0",
            "libssl3=3.5.9-r0",
            "musl=1.2.6-r3",
            "musl-utils=1.2.6-r3",
            "zlib=1.4-r0",
        ):
            self.assertIn(pin, body)
        self.assertNotIn("musl=1.2.5", body)

    def test_lockfiles_are_bound_to_the_build(self) -> None:
        for name, record in self.sources["lockfiles"].items():
            path = ROOT / record["path"]
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.subTest(name=name):
                self.assertEqual(record["sha256"], actual)
                self.assertIn(record["sha256"], self.dockerfile)

    def test_swift_uses_verified_source_and_locked_resolution(self) -> None:
        body = stage(self.dockerfile, "swift-build")

        self.assertIn("/refs/tags/603.0.0.tar.gz", self.dockerfile)
        self.assertIn("images/swift/Package.resolved", body)
        self.assertEqual(2, body.count("599a6beab32b6e9164edaf89903fd30d5c4b9dcc"))
        self.assertIn("swift package", body)
        self.assertIn("resolve", body)
        self.assertIn("--product swift-format", body)
        self.assertEqual(1, body.count("ENV SWIFTC_MAXIMUM_DETERMINISM=1"))
        self.assertEqual(1, body.count("--remove-section=.note.gnu.build-id"))
        self.assertEqual(1, body.count("--remove-section=.swift_modhash"))
        self.assertIn('= "603.0.0"', body)

    def test_shfmt_module_checksum_is_bound_to_the_build(self) -> None:
        module = self.sources["go_modules"]["shfmt"]
        body = stage(self.dockerfile, "go-tools-build")

        self.assertEqual("mvdan.cc/sh/v3", module["path"])
        self.assertEqual("v3.13.1", module["version"])
        self.assertEqual(
            "h1:DP3TfgZhDkT7lerUdnp6PTGKyxxzz6T+cOlY/xEvfWk=",
            module["sum"],
        )
        self.assertEqual(
            "h1:lXJ8SexMvEVcHCoDvAGLZgFJ9Wsm2sulmoNEXGhYZD0=",
            module["go_mod_sum"],
        )
        for value in module.values():
            self.assertIn(value, body)
        self.assertIn('test "$(/shfmt --version)" = "v3.13.1"', body)

    def test_go_runtime_terms_reach_every_go_built_target(self) -> None:
        entries = {
            source["component"]: source
            for source in self.legal_sources["sources"]
            if source["component"] in {"Go", "Go patents grant"}
        }
        expected = ["buildifier", "go", "shfmt"]
        self.assertEqual(expected, entries["Go"]["targets"])
        self.assertEqual(expected, entries["Go patents grant"]["targets"])

    def test_shfmt_compiled_module_legal_set_includes_x_sys(self) -> None:
        matches = [
            source
            for source in self.legal_sources["sources"]
            if source["component"] == "golang.org/x/sys"
        ]
        self.assertEqual(1, len(matches))
        self.assertEqual("0.42.0", matches[0]["version"])
        self.assertEqual(["shfmt"], matches[0]["targets"])

    def test_ktlint_slf4j_terms_match_the_resolved_version(self) -> None:
        matches = [
            source
            for source in self.legal_sources["sources"]
            if source["component"] == "SLF4J"
        ]
        self.assertEqual(1, len(matches))
        self.assertEqual("2.0.7", matches[0]["version"])
        self.assertIn("v_2.0.7", matches[0]["url"])

    def test_notice_describes_hash_binding_without_immutable_url_claim(self) -> None:
        notice = (ROOT / "images" / "THIRD_PARTY_NOTICES.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("immutable source URL", notice)
        self.assertIn("the required SHA-256 binds the accepted bytes", notice)

    def test_black_runtime_dependencies_have_license_notices(self) -> None:
        notice = (ROOT / "images" / "THIRD_PARTY_NOTICES.md").read_text(
            encoding="utf-8"
        )
        for line in BLACK_NOTICE_LINES:
            with self.subTest(line=line):
                self.assertIn(line, notice)

    def test_rust_toolchain_and_formatter_versions_are_distinct(self) -> None:
        manifest = json.loads((ROOT / "languages.json").read_text(encoding="utf-8"))
        notice = (ROOT / "images" / "THIRD_PARTY_NOTICES.md").read_text(
            encoding="utf-8"
        )

        self.assertEqual("1.97.1", manifest["tools"]["rust"])
        self.assertEqual("1.9.0", manifest["tools"]["rustfmt"])
        self.assertIn("rustfmt 1.9.0 from Rust 1.97.1", notice)
        self.assertIn(
            "rust/blob/1.97.1/src/tools/rustfmt/LICENSE-APACHE",
            notice,
        )

    def test_julia_runtime_dependencies_have_license_notices(self) -> None:
        manifest = tomllib.loads(
            (ROOT / "images" / "julia" / "Manifest.toml").read_text(encoding="utf-8")
        )
        notice = (ROOT / "images" / "THIRD_PARTY_NOTICES.md").read_text(
            encoding="utf-8"
        )
        third_party = set(manifest["deps"]) - JULIA_STDLIB_PACKAGES
        third_party.remove("JuliaFormatter")
        self.assertEqual(set(JULIA_RUNTIME_NOTICES), third_party)
        for package, line in JULIA_RUNTIME_NOTICES.items():
            records = manifest["deps"][package]
            with self.subTest(package=package):
                self.assertEqual(1, len(records))
                self.assertIn(records[0]["version"], line)
                self.assertIn(line, notice)

    def test_prettier_install_is_offline_from_verified_archive(self) -> None:
        body = stage(self.dockerfile, "prettier-build")

        self.assertIn("npm install", body)
        self.assertIn("--offline", body)
        self.assertIn("/downloads/prettier-3.7.4.tgz", body)
        self.assertNotIn("prettier@3.7.4", body)

    def test_black_install_is_offline_from_verified_wheels(self) -> None:
        body = stage(self.dockerfile, "black-build")

        self.assertIn("pip install", body)
        self.assertIn("--no-compile", body)
        self.assertIn("--no-index", body)
        self.assertIn("/black-wheels/*.whl", body)
        self.assertIn("PYTHONDONTWRITEBYTECODE=1 python3", body)
        self.assertNotIn("black==26.10.1", body)

    def test_julia_uses_verified_source_and_locked_resolution(self) -> None:
        project = tomllib.loads(
            (ROOT / "images" / "julia" / "Project.toml").read_text(encoding="utf-8")
        )
        manifest = tomllib.loads(
            (ROOT / "images" / "julia" / "Manifest.toml").read_text(encoding="utf-8")
        )
        formatter = manifest["deps"]["JuliaFormatter"][0]

        self.assertEqual(
            "98e50ef6-434e-11e9-1051-2b60c6c9e899",
            project["deps"]["JuliaFormatter"],
        )
        self.assertEqual("=2.12.3", project["compat"]["JuliaFormatter"])
        self.assertEqual("2.12.3", formatter["version"])
        self.assertEqual(
            "/opt/julia-depot/dev/JuliaFormatter",
            formatter["path"],
        )
        for dependency in (
            "CommonMark",
            "Glob",
            "JuliaSyntax",
            "PrecompileTools",
        ):
            with self.subTest(dependency=dependency):
                record = manifest["deps"][dependency][0]
                self.assertRegex(record["git-tree-sha1"], r"^[0-9a-f]{40}$")
                self.assertRegex(record["version"], r"^\d+\.\d+\.\d+")

        body = stage(self.dockerfile, "julia-build")
        self.assertIn("Pkg.instantiate", body)
        self.assertNotIn("Pkg.add", body)
        self.assertNotIn("Pkg.resolve", body)

    def test_julia_omits_nondeterministic_precompile_caches(self) -> None:
        build = stage(self.dockerfile, "julia-build")
        final = stage(self.dockerfile, "julia")

        self.assertNotIn("Pkg.precompile", build)
        self.assertIn("julia --compiled-modules=no", build)
        self.assertIn("/opt/julia-depot/compiled", build)
        self.assertNotIn("/opt/julia-depot/compiled/v1.12/Pkg", build)
        self.assertIn('"--compiled-modules=no"', final)

    def test_builds_do_not_upgrade_mutable_package_indexes(self) -> None:
        self.assertNotIn("apk upgrade", self.dockerfile)


if __name__ == "__main__":
    unittest.main()
