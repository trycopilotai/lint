#!/usr/bin/env python3
"""Capture minimal dated evidence for the README comparison table."""

from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence"
EXCERPTS = EVIDENCE / "comparison-source-excerpts"
OUTPUT = EVIDENCE / "comparison-sources.json"
USER_AGENT = "trycopilotai-lint-comparison-evidence/0.1.6"
# Every comparison cell that describes another project needs
# its own first-party excerpt. Sourcing only the breadth
# column left the role and delivery columns as unsourced
# assertions about other people's work, under a preamble that
# said the opposite. A project's own documentation site counts
# as first-party even when the table links to its landing
# page, so each record carries the URL its excerpt came from.
SOURCES = [
    {
        "column": "breadth",
        "excerpt": "Supports 69 languages, 23 formats, 22 tooling formats",
        "filename": "megalinter-breadth.txt",
        "project": "MegaLinter",
        "url": "https://megalinter.io/latest/",
    },
    {
        "column": "role",
        "excerpt": (
            "At each pull request, it automatically analyzes "
            "all updated code across all languages."
        ),
        "filename": "megalinter-role.txt",
        "project": "MegaLinter",
        "url": "https://megalinter.io/latest/",
    },
    {
        "column": "delivery",
        "excerpt": (
            "we provide flavored MegaLinter images containing "
            "only the linters related to a project type"
        ),
        "filename": "megalinter-delivery.txt",
        "project": "MegaLinter",
        "url": "https://megalinter.io/latest/flavors/",
    },
    {
        "column": "breadth",
        "excerpt": "Trunk Code Quality runs 100+ tools",
        "filename": "trunk-breadth.txt",
        "project": "Trunk Code Quality",
        "url": ("https://marketplace.visualstudio.com/" "items?itemName=trunk.io"),
    },
    {
        "column": "role",
        "excerpt": (
            "Trunk consists of a C++ CLI that orchestrates the "
            "download, installation, and execution of "
            "third-party static analysis tools."
        ),
        "filename": "trunk-role.txt",
        "project": "Trunk Code Quality",
        "url": "https://docs.trunk.io/code-quality",
    },
    {
        "column": "delivery",
        "excerpt": "Trunk manages tools and their runtimes hermetically.",
        "filename": "trunk-delivery.txt",
        "project": "Trunk Code Quality",
        "url": "https://docs.trunk.io/code-quality",
    },
    {
        "column": "breadth",
        "excerpt": (
            "A framework for managing and maintaining "
            "multi-language pre-commit hooks."
        ),
        "filename": "pre-commit-breadth.txt",
        "project": "pre-commit",
        "url": "https://github.com/pre-commit/pre-commit",
    },
    {
        "column": "role",
        "excerpt": (
            "We run our hooks on every commit to automatically "
            "point out issues in code"
        ),
        "filename": "pre-commit-role.txt",
        "project": "pre-commit",
        "url": "https://pre-commit.com/",
    },
    {
        "column": "delivery",
        "excerpt": (
            "pre-commit manages the installation and execution "
            "of any hook written in any language"
        ),
        "filename": "pre-commit-delivery.txt",
        "project": "pre-commit",
        "url": "https://pre-commit.com/",
    },
]


def sha256_bytes(value: bytes) -> str:
    """Return the SHA-256 digest of one byte string."""

    return hashlib.sha256(value).hexdigest()


def timestamp() -> str:
    """Return a UTC timestamp without subsecond noise."""

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def page_text(body: bytes) -> str:
    """Return whitespace-normalized text from one HTML response."""

    value = body.decode("utf-8", errors="replace")
    value = re.sub(r"<script\b[^>]*>.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style\b[^>]*>.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return re.sub(r"\s+([,.;:!?])", r"\1", value)


CONTEXT_CHARACTERS = 240


def exact_excerpt(page: str, expected: str) -> tuple[str, str]:
    """Return the first-party quote and the text around it.

    Returning only the quote made each retained file the
    script's own search string echoed back, which evidences
    nothing a reader could not already see in the README.
    Keeping the surrounding sentences lets someone check the
    quote is not a fragment lifted out of a contradicting
    paragraph.
    """

    offset = page.casefold().find(expected.casefold())
    if offset < 0:
        raise RuntimeError("expected comparison excerpt is absent")
    excerpt = page[offset : offset + len(expected)]
    if excerpt.casefold() != expected.casefold():
        raise RuntimeError("comparison excerpt has an unexpected shape")
    start = max(0, offset - CONTEXT_CHARACTERS)
    end = min(len(page), offset + len(excerpt) + CONTEXT_CHARACTERS)
    context = page[start:end]
    return excerpt, context


def capture(source: dict[str, str]) -> tuple[dict[str, Any], bytes]:
    """Fetch one first-party page and return its source record."""

    request = urllib.request.Request(
        source["url"],
        headers={"User-Agent": USER_AGENT},
    )
    retrieved_at = timestamp()
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        final_url = response.geturl()
        status = response.status
    if status != 200:
        raise RuntimeError("comparison source did not return HTTP 200")
    excerpt, context = exact_excerpt(page_text(body), source["excerpt"])
    excerpt_bytes = (context + "\n").encode("utf-8")
    # No response hash is recorded. Several of these pages carry
    # a fixed-width per-response token, so two fetches one second
    # apart produce the same byte length and a different digest.
    # A hash that cannot stabilise is not evidence; it tells an
    # honest verifier the record has drifted when nothing has.
    record = {
        "column": source["column"],
        "capture": {
            "body_bytes": len(body),
            "context_path": (
                "evidence/comparison-source-excerpts/" + source["filename"]
            ),
            "context_sha256": sha256_bytes(excerpt_bytes),
            "final_url": final_url,
            "http_status": status,
            "retrieved_at": retrieved_at,
        },
        "project": source["project"],
        "readme_claim": excerpt,
        "source_url": source["url"],
    }
    return record, excerpt_bytes


def main() -> int:
    """Refresh all committed comparison-source evidence."""

    EXCERPTS.mkdir(parents=True, exist_ok=True)
    records = []
    checked_at = None
    for source in SOURCES:
        record, excerpt_bytes = capture(source)
        (EXCERPTS / source["filename"]).write_bytes(excerpt_bytes)
        records.append(record)
        retrieved_date = record["capture"]["retrieved_at"].split("T", 1)[0]
        if checked_at is None:
            checked_at = retrieved_date
        if checked_at != retrieved_date:
            raise RuntimeError("comparison captures crossed a UTC date boundary")
    evidence = {
        "checked_at": checked_at,
        "regeneration_command": "python3 tools/capture_comparison_sources.py",
        "schema_version": 2,
        "sources": records,
    }
    OUTPUT.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
