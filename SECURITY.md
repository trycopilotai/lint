# Security policy

Report a vulnerability through the repository's **Security**
tab with GitHub private vulnerability reporting. The release
gate requires that reporting path to be active and verified
before a supported release is published.

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

## Scanner exceptions

The release scan carries one time-bounded exception for
Black 24.10.0 under `CVE-2026-32274`. The release requires
that version for output compatibility. The image forces
`--no-config`, formats isolated temporary mirrors, and does
not expose project plugin or cache-path inputs.

The exception is scoped to the Black 24.10.0 package URL and
expires on 2026-10-31. A release after that date must update
the pin or record a newly reviewed disposition.
