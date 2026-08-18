# Third-party notices

Every formatter image carries the repository's MIT terms at
`/licenses/lint/LICENSE`, this notice at
`/licenses/lint/THIRD_PARTY_NOTICES.md`, and a target-local
legal payload at `/licenses/<target>/`.

Each target-local `manifest.json` names every payload file,
component, version, license, declared source URL, source
SHA-256, and payload SHA-256. Some upstream URLs are release
tags or content endpoints rather than immutable identifiers;
the required SHA-256 binds the accepted bytes. The
checked-in payloads are generated and verified with:

```sh
python3 tools/generate_legal_payloads.py --write
python3 tools/generate_legal_payloads.py
```

Generation downloads every declared source before replacing
the existing payload directory. A source, archive member,
lockfile, package inventory, Cargo supplement, or
derived-payload mismatch fails without replacing the
checked-in payloads.

`images/dependency_closures.json` maps independently derived
Go build information, Gradle runtimeClasspath, Swift
Package.resolved, Julia Manifest.toml, .NET deps, and Cargo
lock receipts to the exact legal files carried by each
target. Repository verification rejects missing, extra,
duplicate, or version-drifted closure entries.

## Image inventories

### prettier

The payload covers Prettier 3.7.4, Node.js 24.18.0 and its
bundled notices, plus the copied Alpine musl, GCC,
apk-tools, OpenSSL, and zlib runtime families.

### buildifier

The payload covers Bazel Buildtools 8.2.1 and the two
external Go modules in the pinned Buildifier build: the
legacy Go protobuf module and the current Go protobuf
module.

### black

The payload covers Black 24.10.0, its six pinned runtime
wheels, CPython 3.13.14, and the copied Alpine runtime
families. Component copyright text is retained where an
upstream package supplies it.

- Click 8.1.7: [BSD-3-Clause][click-license]
- mypy-extensions 1.0.0: [MIT][mypy-extensions-license]
- packaging 24.1: [Apache-2.0 OR
  BSD-2-Clause][packaging-license]
- pathspec 0.12.1: [MPL-2.0][pathspec-license]
- platformdirs 4.3.6: [MIT][platformdirs-license]

### requirements

The payload covers CPython 3.13.14 and the copied Alpine
runtime families used by the requirements formatter image.
The requirements sorter itself is governed by this
repository's MIT license.

### shfmt

The payload covers shfmt 3.13.1 and every external module in
its pinned non-test Go dependency graph.

### clang

The payload covers clang-format 18.1.8 and musl. The wheel's
own legal directory is also copied to
`/licenses/clang-format` in the final image.

### java

The payload covers google-java-format 1.35.0, its Guava
runtime closure, annotation artifacts, GraalVM Community
Edition 25.0.2, OpenJDK 25.0.2, and GraalVM's complete
third-party license file.

### go

The payload covers Go and gofmt 1.26.6, including the Go
patent grant.

### rust

The payload covers Rust and rustfmt 1.97.1, the complete
legal corpus shipped in that Rust distribution, and the
copied musl and GCC runtime terms.

- rustfmt 1.9.0 from Rust 1.97.1: [Apache-2.0 OR
  MIT][rustfmt-license]

### kotlin

The payload covers ktlint 1.3.0, its exact Gradle-resolved
runtime dependency families, the legal files embedded in the
pinned 1.3.0 application archive, OpenJDK 21.0.10, and the
copied Alpine runtime families. The `jlink` output also
keeps OpenJDK's module-specific legal directory at
`/opt/ktlint-java/legal`.

### taplo

The payload covers Taplo CLI 0.10.0, musl, Rust 1.87.0, and
all 309 registry packages in Taplo 0.10.0's pinned
`Cargo.lock`. The derived Cargo payload records each crate's
name, version, archive SHA-256, authors, license expression,
repository, and every LICENSE, COPYING, NOTICE, COPYRIGHT,
or UNLICENSE file shipped in the crate archive. A crate with
an absent, empty, or pointer-only legal file is accepted
only through an exact supplement keyed by name, version, and
crate checksum. Supplements bind their repository commit,
source URL and SHA-256, evidence, and rendered payload
SHA-256.

### xml

The payload covers libxml2 2.15.3 and musl. The installed
libxml2 tree also retains its upstream documentation and
copyright file.

### swift

The payload covers swift-format 603.0.0, the Swift 6.3
runtime, and every dependency pinned by the resolved package
graph: swift-argument-parser 1.8.2, swift-markdown 0.8.0,
swift-syntax 603.0.2, and swift-cmark 0.8.0. Versions in the
target manifest include the exact resolved Git revisions.

### csharp

The payload covers CSharpier 1.3.0, the exact package
inventory extracted from the pinned net10.0 application
archive, the .NET 10.0.11 license and third-party notices,
and legal texts for each distinct upstream dependency family
in that inventory. It also covers the copied Alpine runtime
families.

### julia

The payload covers Julia 1.12.6 and its bundled notices plus
every external package pinned by the formatter's checked-in
Julia manifest: JuliaFormatter, CommonMark, Glob,
JuliaSyntax, PrecompileTools, and Preferences.

- CommonMark 1.0.3: [MIT][commonmark-license]
- Glob 1.5.0: [MIT][glob-license]
- JuliaSyntax 1.0.2: [MIT][juliasyntax-license]
- PrecompileTools 1.3.4: [MIT][precompiletools-license]
- Preferences 1.5.2: [MIT][preferences-license]

[click-license]:
  https://github.com/pallets/click/blob/8.1.7/LICENSE.rst
[commonmark-license]:
  https://github.com/MichaelHatherly/CommonMark.jl/blob/v1.0.3/LICENSE
[glob-license]:
  https://github.com/vtjnash/Glob.jl/blob/v1.5.0/LICENSE.md
[juliasyntax-license]:
  https://github.com/JuliaLang/JuliaSyntax.jl/blob/v1.0.2/LICENSE.md
[mypy-extensions-license]:
  https://github.com/python/mypy_extensions/blob/1.0.0/LICENSE
[packaging-license]:
  https://github.com/pypa/packaging/blob/24.1/LICENSE
[pathspec-license]:
  https://github.com/cpburnz/python-pathspec/blob/v0.12.1/LICENSE
[platformdirs-license]:
  https://github.com/tox-dev/platformdirs/blob/4.3.6/LICENSE
[precompiletools-license]:
  https://github.com/JuliaLang/PrecompileTools.jl/blob/v1.3.4/LICENSE
[preferences-license]:
  https://github.com/JuliaPackaging/Preferences.jl/blob/v1.5.2/LICENSE.md
[rustfmt-license]:
  https://github.com/rust-lang/rust/blob/1.97.1/src/tools/rustfmt/LICENSE-APACHE
