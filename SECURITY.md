# Security policy

Report a vulnerability with GitHub private vulnerability
reporting for this repository. Do not include credentials,
private source files, or other secrets in a public issue.

The CLI rejects paths outside its working directory and
formats copies before applying changes. Container runs
remove network access and Linux capabilities, use a
read-only root filesystem, and run as a non-root user.

Security fixes are supported for the latest tagged release.
