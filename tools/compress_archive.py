#!/usr/bin/env python3
"""Compress a source archive reproducibly.

The digest published in ``SHA256SUMS`` and in the release
manifest's ``source.sha256`` is the digest of the *compressed*
archive, so the compressor is part of the artifact. ``gzip -n``
and ``gzip -9 -n`` produce different bytes from the same tar,
and a recipient who rebuilds the tar from the signed tag has no
way to guess which one produced the published digest.

This pins the one compressor the release uses: gzip with an
empty filename field, a zero modification time, and the default
compression level. Given the same tar, it is byte-identical on
every platform, so ``git archive`` plus this tool reproduces the
published digest exactly.
"""

from __future__ import annotations

import argparse
import gzip
from pathlib import Path


def compress(source: Path, destination: Path) -> None:
    """Write ``source`` to ``destination`` as deterministic gzip."""

    payload = source.read_bytes()
    with destination.open("wb") as handle:
        with gzip.GzipFile(
            filename="",
            fileobj=handle,
            mode="wb",
            mtime=0,
        ) as compressed:
            compressed.write(payload)


def main() -> int:
    """Compress one archive and return a process exit code."""

    parser = argparse.ArgumentParser(
        prog="compress_archive.py",
        description="Compress a tar archive reproducibly.",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="path to the uncompressed tar archive",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="path to write the compressed archive to",
    )
    arguments = parser.parse_args()

    source = Path(arguments.input)
    if not source.is_file():
        parser.error(f"input archive not found: {source}")
    compress(source, Path(arguments.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
