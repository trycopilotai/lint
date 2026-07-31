# Contributing

Changes should preserve the read-only default, stable JSON
schema, formatter isolation, and identical local and
container formatting behavior.

Before opening a pull request:

```sh
make test
make verify
python3 lint.py --read-only --all
```

Add a focused regression test for every behavior change.
Keep generated evidence separate from source changes.
Formatter versions belong in `languages.json`; container
source checksums belong in `images/sources.json`.

Use the `formatter` label for engine behavior and language
coverage. Use `supply-chain` for images, release workflows,
source checksums, SBOMs, attestations, and dependency
provenance. Issues scoped for a first contribution also use
`good first issue`.

Do not add lint-only checks to formatter runners. This
project reports canonical formatting differences and
formatter failures.
