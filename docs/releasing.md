# Releasing lint

`tools/release.py` owns the mechanical half of a release.
This document owns the half that needs judgement, a built
image, or a browser.

## Order of operations

1. `python3 tools/release.py bump --version <version>`
2. Any other source change the release needs.
3. Commit the source changes.
4. `python3 tools/release.py refresh-evidence`
5. Commit the evidence change by itself.

Step 4 must follow step 3. `scripts/verify_demo.py` requires
the demo evidence commit to be the repository tip and to
contain evidence paths only, so refreshing evidence while
source edits are still uncommitted fails with
`demo evidence commit contains unrelated paths`.

## What `bump` does

It reads the current version from `images/matrix.json`,
rewrites it across every tracked file, re-syncs the packaged
skill copies, and then verifies its own work:

- `images/license_sources.json` and `images/licenses/` are
  never rewritten, and every third-party version recorded
  there is compared before and after. The `anes` crate
  records a version of its own that has already collided
  exactly with a lint version being replaced; rewriting it
  would record a release upstream never made.
- `evidence/` and `images/inventories/` are never rewritten.
  Both are generated against a built artifact.
- `lint.py`, `languages.json`, and `images/matrix.json` are
  copied onto their `skills/lint/` counterparts and compared
  byte for byte.
- The old version must survive nowhere, which is the
  in-process form of:

```sh
git grep -n "<old-version>" -- . ':!images/licenses' \
    ':!images/license_sources.json' ':!evidence'
```

Missing a site is the failure mode that has actually
happened. The previous hand-cut bump left six files behind
and only the test suite caught them.

## Vulnerability gate

Trivy runs in both `images.yml` and `release.yml` with
`--severity HIGH,CRITICAL --exit-code 1 --ignorefile .trivyignore.yaml`.
A finding blocks the release.

Prefer moving the pinned upstream toolchain over suppressing
the finding: `ARG GO_IMAGE` and `ARG DOTNET_RUNTIME` in
`images/Dockerfile`, plus the matching entries under `tools`
in `languages.json`.

If a suppression is genuinely unavoidable, follow the entry
shape already in `.trivyignore.yaml`: `id`, `purls`,
`expired_at`, and a `statement` that says why the vulnerable
path is unreachable here and what would make it reachable
again.

## Third-party notices reach every image

`images/THIRD_PARTY_NOTICES.md` is `COPY`'d into every image
(`images/Dockerfile:85`). Editing it changes the recorded
digest of `licenses/lint/THIRD_PARTY_NOTICES.md` in all
fifteen canonical inventories under `images/inventories/`,
not only in the images that were rebuilt.

Regenerating four of the fifteen and leaving the rest is
what turned CI red on the previous release. Touch the
notices file and the whole inventory set is stale.

## Inventory regeneration needs Docker

```sh
python3 images/generate_image_inventory.py \
    --image lint-<language>:ci \
    --target <target> \
    --architecture amd64
```

This reads a real built image, so it cannot run without
Docker.

Only AMD64 has a checked-in canonical inventory. ARM64 jobs
therefore pass while AMD64 jobs fail on a stale inventory,
which is a useful diagnostic: an inventory failure on one
architecture and not the other points at the checked-in set
rather than at the image.

## Go build-info receipts

`images/closures/buildifier-go-build-info.txt` and
`images/closures/shfmt-go-build-info.txt` are normalized
`go version -m` output. Regenerate them by extracting the
binary from the built image and running `go version -m`
inside the pinned `golang` image, then update
`source_sha256` and `receipt.go_version` for those entries
in `images/dependency_closures.json`.

## Signed tag

The release tag is annotated and SSH-signed so it verifies
against `.github/release-allowed-signers`. The tagger
identity is
`Pramod Kotipalli <trycopilotai@users.noreply.github.com>`.

## GHCR package visibility is a manual step

A newly published container package starts out visible only
to the organization. There is no API that changes it: REST
`PATCH /orgs/{org}/packages/container/{name}` answers 404,
and the GraphQL schema exposes only `deletePackageVersion`.
Each new package has to be switched over by hand in its own
package settings page.
