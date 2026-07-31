# Publishing

The signed release tag stages versioned GHCR packages and a
draft GitHub Release while the repository remains private.
GitHub does not copy repository visibility to existing
container packages. The private stage skips GitHub artifact
attestations because GitHub Free limits them to public
repositories.

This private repository is a staging repository. Never make
this repository object public directly. Its prelaunch
workflow runs and withdrawn `v0.1.0` and `v0.1.1` staging
tags are private evidence, not public release history.

Public launch is a separate, irreversible procedure:

1. Complete private validation and disclosure review. Save
   the repository metadata, labels, issue forms, successful
   check receipts, package digests, draft release assets,
   and the exact final `main` and signed `v0.1.4` objects.
2. Obtain fresh signed authorization to delete and recreate
   `trycopilotai/lint`. Recreate it as a private repository
   with no initial content. Push only `refs/heads/main` and
   `refs/tags/v0.1.4`; do not push the withdrawn staging
   tags or any other ref.
3. Verify the new repository ID differs from the staging
   repository ID. Confirm that `v0.1.0`, `v0.1.1`, every
   staging workflow run, and the old repository ID no longer
   resolve. Run the disclosure scanner against a fresh
   authenticated clone.
4. Restore the description, topics, label definitions, and
   issue forms. Run every check and the tag-triggered
   **Release** workflow while the repository is private.
   Verify all packages remain private and the GitHub Release
   remains a draft.
5. Retain the successful private receipts outside the
   repository, then delete every Actions workflow run.
   Require the Actions runs API to report zero runs before
   changing visibility. Workflow logs name the triggering
   account and are publication surfaces.
6. Make the repository public. In **Actions**, run the
   tag-triggered **Release** workflow for `v0.1.4`. The
   workflow reads the repository's current visibility, adds
   image and source-archive attestations, and replaces the
   existing draft assets. Choose **Re-run all jobs** if the
   release workflow is already present. Require a successful
   run before continuing.
7. Create the launch issues through the committed issue
   forms with the designated public maintainer identity.
   Apply `formatter` or `supply-chain` and attach
   `good first issue` to the starter tasks. Verify each
   label is attached to a real open issue.
8. In **Settings**, under **Social preview**, choose
   **Edit**, upload `assets/social-preview.png`, and verify
   from an unauthenticated preview client that a shared
   repository link renders the custom image.
9. In **Settings**, under **Security**, open **Advanced
   Security** and enable **Private vulnerability
   reporting**. From an unauthenticated session, open
   **Security**, then **Advisories**, and verify that
   **Report a vulnerability** is available.
10. Open each `lint-<language>` package under the
    `trycopilotai` organization. In **Package settings**,
    choose **Change visibility**, select **Public**, and
    confirm the package name. Repeat for all 26 package
    names.
11. Verify an anonymous pull of every versioned package in a
    clean Docker credential directory. Each manifest must
    expose both Linux AMD64 and ARM64.
12. Verify the source archive, checksums, release manifest,
    attestations, and software bills of materials from an
    unauthenticated client.
13. In the draft GitHub Release, choose **Publish release**
    only after every package and artifact passes the
    unauthenticated checks.

Do not create a `latest` alias as part of the initial
release. Making a GHCR package public cannot be reversed.
