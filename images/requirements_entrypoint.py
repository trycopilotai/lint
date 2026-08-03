#!/usr/bin/env python3
"""Format one requirements file in place."""

from __future__ import annotations

import re
import sys
from pathlib import Path


REQUIREMENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
REQUIREMENT_SEPARATOR_PATTERN = re.compile(r"[-_.]+")


def requirement_records(section: list[str]) -> list[str]:
    records: list[str] = []
    current: list[str] = []
    for line in section:
        current.append(line)
        if line.endswith("\\"):
            continue
        records.append("\n".join(current))
        current = []
    if current:
        records.append("\n".join(current))
    return records


def canonical_requirement_name(record: str) -> str:
    """Return the normalized distribution name used for sorting."""

    first_line = record.splitlines()[0].strip()
    match = REQUIREMENT_NAME_PATTERN.match(first_line)
    if match is None:
        return first_line.casefold()
    return REQUIREMENT_SEPARATOR_PATTERN.sub("-", match.group(0)).casefold()


def formatted(text: str) -> str:
    sections: list[list[str]] = [[]]
    for line in text.splitlines():
        if line.strip() == "":
            if sections[-1]:
                sections.append([])
            continue
        sections[-1].append(line.rstrip())

    output: list[str] = []
    for section in sections:
        if not section:
            continue
        comments: list[str] = []
        requirements: list[str] = []
        for record in requirement_records(section):
            if record.lstrip().startswith(("#", "-", "http://", "https://")):
                comments.append(record)
            else:
                requirements.append(record)
        requirements.sort(key=canonical_requirement_name)
        output.append("\n".join(comments + requirements))
    if not output:
        return ""
    return "\n\n".join(output) + "\n"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: requirements-format FILE", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")
    path.write_text(formatted(text), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
