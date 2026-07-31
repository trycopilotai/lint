# lint

`lint` is one read-only-by-default formatting interface for
local tools and independently addressable per-language
Docker images. It uses an explicit working directory and
applies writes only after every selected formatter succeeds.

`lint` is read-only by default.

Each language has an independently addressable Docker image.

![Animated lint command demo](assets/demo.svg)

The static reduced-motion poster is available at
[`assets/poster.svg`](assets/poster.svg). The reconstructed
demo derives from
[`evidence/demo-transcript.txt`](evidence/demo-transcript.txt);
its generation record is
[`evidence/demo-manifest.json`](evidence/demo-manifest.json).
The upload-ready 1280×640 social preview is
[`assets/social-preview.png`](assets/social-preview.png).

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
make lint ARGS=--modified
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

Use the composite GitHub Action:

```yaml
steps:
  - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09
  - uses: trycopilotai/lint@v0.1.3
    with:
      mode: read-only
      cwd: .
      docker: "true"
```

The Action uses Docker by default, so the example does not
depend on formatter versions installed on the runner. Set
`docker: "false"` only after provisioning every pinned local
formatter.

`--read-only`, `--readonly`, and `-ro` are equivalent.
`--write`, `--apply`, and `-w` are equivalent. `--docker`
and `-d` select the Docker backend. Output is human-readable
by default; `--json` emits the stable result object.

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
- requirements files with deterministic sorting that keeps
  backslash continuation blocks intact
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
`ghcr.io/trycopilotai/lint-<language>:0.1.3`. Each final
image is checked against its compressed-size budget and
rejects shell, package-manager, and standalone compiler
executables.

Prettier always uses `printWidth: 60`, `proseWrap: always`,
and `trailingComma: none`. It does not load repository
Prettier configuration, EditorConfig files, or plugins, so
local and Docker runs use the same policy.

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
mode, and file objects with `path` and `input_bytes_base64`.
For example:

```sh
curl --fail-with-body \
  --header 'Content-Type: application/json' \
  --data '{"policy":"default","mode":"read-only","files":[{"path":"requirements.txt","input_bytes_base64":"emV0YT09MQphbHBoYT09MQo="}]}' \
  http://127.0.0.1:8080/v1/lint
```

Limits are 64 files, 2 MiB per file, 16 MiB per request, and
30 seconds per formatter. The service returns formatted
bytes without modifying caller files.

## Development

```sh
make test
make verify
```

Release tags build Linux AMD64 and ARM64 images, record
source checksums and software bills of materials, and stage
a draft GitHub release. GitHub artifact attestations are
added when the repository is public; the publishing
procedure reruns the release after the visibility change.

See [CONTRIBUTING.md](CONTRIBUTING.md) for changes and
[SECURITY.md](SECURITY.md) for vulnerability reports. The
[publishing procedure](docs/publishing.md) covers the
deferred public transition for packages and the draft
release.

Public issues use the `formatter` and `supply-chain` domain
labels. Starter tasks use `good first issue`; the launch
procedure creates them from the committed issue forms only
after a public maintainer identity is available.

## Claude Code

After public launch, install the pinned standalone skill
without authentication:

```sh
archive="$(mktemp -d)"
target="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/lint"
install -d "$target"
curl --fail --location \
  https://github.com/trycopilotai/lint/archive/refs/tags/v0.1.3.tar.gz \
  >"$archive/lint.tar.gz"
tar -xzf "$archive/lint.tar.gz" \
  --strip-components=1 \
  -C "$target"
cp "$target/skills/lint/SKILL.md" "$target/SKILL.md"
cp "$target/skills/lint/run.py" "$target/run.py"
```

The standalone invocation is `/lint`. A Claude marketplace
distribution uses `/lint:lint`.

## Codex

After public launch, install the same pinned skill into the
Codex skill store without authentication:

```sh
archive="$(mktemp -d)"
target="${CODEX_HOME:-$HOME/.codex}/skills/lint"
install -d "$target"
curl --fail --location \
  https://github.com/trycopilotai/lint/archive/refs/tags/v0.1.3.tar.gz \
  >"$archive/lint.tar.gz"
tar -xzf "$archive/lint.tar.gz" \
  --strip-components=1 \
  -C "$target"
cp "$target/skills/lint/SKILL.md" "$target/SKILL.md"
cp "$target/skills/lint/run.py" "$target/run.py"
```

Invoke it as `$lint`.

The local backend reads selected repository files and
executes formatter processes. Its pinned Prettier and
Buildifier commands use `npx`, which can access the network
and update the user's package cache. The Docker backend runs
with networking off. Read-only mode means source files are
not changed; it does not mean the local package cache is
untouched.
