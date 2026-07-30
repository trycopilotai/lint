# lint

`lint` is one read-only-by-default formatting interface for
local tools and independently addressable per-language
Docker images. It uses an explicit working directory and
applies writes only after every selected formatter succeeds.

![Animated lint command demo](assets/demo.svg)

The static reduced-motion poster is available at
[`assets/poster.svg`](assets/poster.svg). The reconstructed
demo derives from
[`evidence/demo-transcript.txt`](evidence/demo-transcript.txt);
its generation record is
[`evidence/demo-manifest.json`](evidence/demo-manifest.json).

## Quick start

Check every supported file under the current directory:

```sh
python3 lint.py
make lint
```

Apply all formatting only after every formatter succeeds:

```sh
python3 lint.py --write
make lint ARGS=--write
```

Check only modified Git-tracked files:

```sh
python3 lint.py --modified
```

Run from another directory or select a language:

```sh
python3 lint.py --cwd ../project
make lint_ts
make lint_python
```

Use the language-specific Docker images:

```sh
python3 dlint.py
make dlint_markdown
```

`--read-only`, `--readonly`, and `-ro` are equivalent.
`--write`, `--apply`, and `-w` are equivalent. `--docker`
and `-d` select the Docker backend.

## Selection

With no paths or selection flag, `lint.py` behaves as
`--all`. In a Git worktree, that means tracked files plus
nonignored untracked files below `--cwd`. Outside Git, lint
uses a pruned directory walk. `--modified` includes staged
and unstaged modified tracked files and excludes untracked
files.

Explicit paths and NUL-delimited `--files-from0` input are
also supported. Paths must stay below `--cwd`; symbolic
links and parent traversal are rejected.

## Formatters

The default policy covers:

- Markdown, HTML, YAML, JSON, JavaScript, TypeScript, TSX,
  CSS, SCSS, and Less with Prettier
- Bazel files with Buildifier
- Python with Black
- requirements files with deterministic sorting
- shell files with shfmt
- C, C++, Objective-C, and Objective-C++ with clang-format
- Java with google-java-format
- Go with gofmt
- Rust with rustfmt
- Kotlin with ktlint
- TOML with Taplo
- XML and plist files with xmllint
- Swift with swift-format
- C# with CSharpier
- Julia with JuliaFormatter

Pinned versions are recorded in `languages.json`. Local runs
report an install hint when a required executable is absent.
Docker runs use
`ghcr.io/trycopilotai/lint-<language>:0.1.0`.

Prettier always uses `printWidth: 60`, `proseWrap: always`,
and `trailingComma: none`. A project configuration can add
nonconflicting native options. Prettier plugins are not part
of the default policy.

## HTTP API

Run the loopback-only service:

```sh
python3 service.py
```

It exposes:

- `GET /healthz`
- `GET /v1/languages`
- `POST /v1/lint`

`POST /v1/lint` accepts `policy: "default"`, a read or write
mode, and base64-encoded file objects. Limits are 64 files,
2 MiB per file, 16 MiB per request, and 30 seconds per
formatter. The service returns formatted bytes without
modifying caller files.

## Development

```sh
make test
make verify
```

Release tags build Linux AMD64 and ARM64 images, record
source checksums and software bills of materials, produce
attestations, and stage a draft GitHub release.

See [CONTRIBUTING.md](CONTRIBUTING.md) for changes and
[SECURITY.md](SECURITY.md) for vulnerability reports.

## Claude Code

Install the pinned standalone skill after repository access
is available:

```sh
archive="$(mktemp -d)"
target="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/lint"
install -d "$target"
gh api repos/trycopilotai/lint/tarball/v0.1.0 \
  >"$archive/lint.tar.gz"
tar -xzf "$archive/lint.tar.gz" \
  --strip-components=3 \
  -C "$target" \
  '*/skills/lint'
```

The standalone invocation is `/lint`. A Claude marketplace
distribution uses `/lint:lint`.

## Codex

Install the same pinned skill into the Codex skill store:

```sh
archive="$(mktemp -d)"
target="${CODEX_HOME:-$HOME/.codex}/skills/lint"
install -d "$target"
gh api repos/trycopilotai/lint/tarball/v0.1.0 \
  >"$archive/lint.tar.gz"
tar -xzf "$archive/lint.tar.gz" \
  --strip-components=3 \
  -C "$target" \
  '*/skills/lint'
```

Invoke it as `$lint`.
