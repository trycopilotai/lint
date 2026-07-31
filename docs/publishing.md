# Publishing

The signed release tag stages versioned GHCR packages and a
draft GitHub Release while the repository remains private.
GitHub does not copy repository visibility to existing
container packages. The private stage skips GitHub artifact
attestations because GitHub Free limits them to public
repositories.

Public launch is a separate, irreversible procedure:

1. Complete the disclosure review against the pushed Git
   history and make the repository public.
2. In **Actions**, open the tag-triggered **Release**
   workflow and choose **Re-run all jobs**. The workflow
   reads the repository's current visibility, adds image and
   source-archive attestations, and replaces the existing
   draft assets. Require a successful run before continuing.
3. In **Settings**, under **Social preview**, choose
   **Edit**, upload `assets/social-preview.png`, and verify
   from an unauthenticated preview client that a shared
   repository link renders the custom image.
4. In **Settings**, under **Security**, open **Advanced
   Security** and enable **Private vulnerability
   reporting**. From an unauthenticated session, open
   **Security**, then **Advisories**, and verify that
   **Report a vulnerability** is available.
5. Open each `lint-<language>` package under the
   `trycopilotai` organization. In **Package settings**,
   choose **Change visibility**, select **Public**, and
   confirm the package name. Repeat for all 26 package
   names.
6. Verify an anonymous pull of every versioned package in a
   clean Docker credential directory. Each manifest must
   expose both Linux AMD64 and ARM64.
7. Verify the source archive, checksums, release manifest,
   attestations, and software bills of materials from an
   unauthenticated client.
8. In the draft GitHub Release, choose **Publish release**
   only after every package and artifact passes the
   unauthenticated checks.

Do not create a `latest` alias as part of the initial
release. Making a GHCR package public cannot be reversed.
