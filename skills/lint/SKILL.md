---
name: lint
description: >-
  Check or apply canonical formatting across a
  mixed-language repository with a read-only default and
  optional versioned language-specific containers. Use when
  user asks to lint, format, check formatting, apply
  formatting, inspect modified files, or add a repository
  lint command.
---

# lint

Use this skill to run the repository's formatter interface
without broadening the requested scope.

## Choose the mode

- For review, diagnosis, CI, or an unspecified request, run
  read-only mode.
- Run write mode only when the user asks to format, fix, or
  apply changes.
- Use Docker when the local pinned formatter is unavailable
  or when the user asks for a reproducible container run.
- Use `--modified` only when the user requests changed
  files. The default is all supported files below the
  working directory.

## Run

Run the launcher from this skill directory:

```sh
python3 <skill-directory>/run.py --cwd /absolute/project/path
python3 <skill-directory>/run.py --cwd /absolute/project/path --modified
python3 <skill-directory>/run.py --cwd /absolute/project/path --write
python3 <skill-directory>/run.py --cwd /absolute/project/path --docker
```

In a consumer repository with the Make adapter:

```sh
make lint
make lint_ts
make lint ARGS=--modified
make lint ARGS=--write
```

## Interpret the result

The CLI prints a human-readable result. Add `--json` for one
stable JSON object.

- Exit 0 means every selected file was clean, or every
  requested write completed.
- Exit 1 means formatting is required, an input file is
  invalid, or a formatter failed.
- Exit 2 means the requested selection or CLI use is
  invalid.
- Exit 3 means the engine failed internally.

Report the exact command, mode, selected count, and any
files that still need formatting. Read-only runs report
`would_change`, which counts files whose formatting differs
without rewriting any of them; write runs report `changed`,
which counts files actually rewritten. Never describe a
read-only count as files that were changed. Do not call
write mode to make a read-only request pass.

## Safety

Keep `--cwd` at the intended repository or subdirectory. Do
not follow symbolic links or send paths outside it. Preserve
repository-specific checks as a separate command; this tool
is a formatter interface.

The local backend reads the selected files and executes
formatter processes. Pinned Prettier and Buildifier runs use
`npx`, which can access the network and update the user's
package cache. Docker mode runs with networking off.
Read-only mode protects source files, not the local package
cache.

## Reference

This skill can be installed on its own, so every link below
is absolute rather than relative to a checkout.

- Canonical repository:
  <https://github.com/trycopilotai/lint>
- Command documentation:
  <https://github.com/trycopilotai/lint/blob/main/README.md#quick-start>
- Report a formatter result or request language coverage:
  <https://github.com/trycopilotai/lint/issues>
- Security policy and vulnerability reporting:
  <https://github.com/trycopilotai/lint/blob/main/SECURITY.md>
