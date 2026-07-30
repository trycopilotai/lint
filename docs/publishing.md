# Publishing

The signed release tag stages versioned GHCR packages and a
draft GitHub Release while the repository remains private.
GitHub does not copy repository visibility to existing
container packages.

Public launch is a separate, irreversible procedure:

1. Complete the disclosure review against the pushed Git
   history and make the repository public.
2. Open each `lint-<language>` package under the
   `trycopilotai` organization. In **Package settings**,
   choose **Change visibility**, select **Public**, and
   confirm the package name. Repeat for all 26 package
   names.
3. Verify an anonymous pull of every versioned package in a
   clean Docker credential directory. Each manifest must
   expose both Linux AMD64 and ARM64.
4. Verify the source archive, checksums, release manifest,
   attestations, and software bills of materials from an
   unauthenticated client.
5. In the draft GitHub Release, choose **Publish release**
   only after every package and artifact passes the
   unauthenticated checks.

Do not create a `latest` alias as part of the initial
release. Making a GHCR package public cannot be reversed.
