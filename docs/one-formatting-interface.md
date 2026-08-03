# One formatting interface for mixed-language repositories

Most repositories do not have one formatter. They have a
collection of language tools, configuration files, CI
commands, local aliases, and container assumptions. The
result is often a gap between what a contributor runs and
what CI enforces.

`lint` presents those formatters through one read-only
default:

```sh
python3 lint.py
```

The same interface can select modified tracked files, use an
explicit working directory, apply a complete formatting
batch, or run a pinned language-specific image. A write
starts only after every selected file has formatted
successfully in a temporary mirror.

The container model is language-specific. Pulling the
Markdown formatter does not require a compiler toolchain;
pulling the Go formatter does not require a JVM. Languages
that use the same formatter may share a digest while keeping
distinct image names.

The command emits a human-readable result by default and
stable JSON with `--json` for scripts and CI. The HTTP
surface uses the same policy and accepts only bounded,
base64-encoded relative paths. This keeps the local CLI,
containers, and service on one observable formatting
contract.
