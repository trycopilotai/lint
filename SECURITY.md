# Security policy

Report a vulnerability through the repository's **Security**
tab with GitHub private vulnerability reporting. That path
is a repository setting a maintainer turns on, and no
automated release check inspects the repository's advisory
settings, so confirm the form is present before relying on
it.

If the private form is unavailable, open a public issue that
contains no vulnerability details and requests activation of
the private reporting path. Do not include credentials,
private source files, exploit details, or other secrets in a
public issue.

The CLI rejects paths outside its working directory and
formats copies before applying changes. Container runs
remove network access and Linux capabilities, use a
read-only root filesystem, and run as a non-root user.

Security fixes are supported for the latest tagged release.

## Release tag policy

The `v0.1.9` release is the single unsigned exception. The
release workflow still requires its tag to be annotated and
to point to the exact checked-out commit; it also binds the
workflow controller to that same commit. All other release
tags remain SSH-signed and are verified against the
allowed-signers file from the published, immutable `main`
commit `3d5d4ee7b83b2c6442039b8a72a571c729ffcead`. The
v0.1.9 tagger identity is self-asserted metadata, not
independent identity proof. The trusted ED25519 fingerprint
for those signed tags is
`SHA256:DfKMRhe4zXosajTxEcDqVDi7dnQ/pwtvj/lLBDn7a9k`.

To verify a signed tag from another release:

```sh
git -c gpg.format=ssh \
  -c gpg.ssh.allowedSignersFile=.github/release-allowed-signers \
  verify-tag v0.1.8
```

The v0.1.9 exception does not change image build, scan,
checksum, SBOM, or provenance verification. The pinned
signer root remains the trust policy for every other release
tag.

## Scanner exceptions

The release scan has no vulnerability exceptions. Black
26.10.1 replaces the expired Black 24.10.0 exception; the
existing formatter isolation checks remain in place.

Public follow-up work about formatter isolation uses the
`formatter` label. Public follow-up work about images,
checksums, SBOMs, attestations, or release provenance uses
`supply-chain`. Never place vulnerability details in either
public label.
