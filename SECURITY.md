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

## Release signatures

Release tags are SSH-signed. Release automation verifies a
candidate against the allowed-signers file from the
published, immutable `main` commit
`3d5d4ee7b83b2c6442039b8a72a571c729ffcead`, not against the
copy in the candidate. A candidate therefore cannot
authorize an additional release key by changing its own
allowed-signers file. The trusted ED25519 fingerprint is
`SHA256:DfKMRhe4zXosajTxEcDqVDi7dnQ/pwtvj/lLBDn7a9k`.

```sh
git -c gpg.format=ssh \
  -c gpg.ssh.allowedSignersFile=.github/release-allowed-signers \
  verify-tag v0.1.8
```

This local command proves that the tag matches the key in
the checked-out allowed-signers file. Release automation
instead fetches the published commit above into an isolated
checkout and passes that file's absolute path to Git.
Because the key, commit identifier, and instructions ship in
the same repository, this is not independent identity proof.

## Scanner exceptions

The release scan has no vulnerability exceptions. Black
26.10.1 replaces the expired Black 24.10.0 exception; the
existing formatter isolation checks remain in place.

Public follow-up work about formatter isolation uses the
`formatter` label. Public follow-up work about images,
checksums, SBOMs, attestations, or release provenance uses
`supply-chain`. Never place vulnerability details in either
public label.
