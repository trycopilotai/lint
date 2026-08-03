#!/usr/bin/env python3
"""Generate one deterministic final-image contents manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import verify_images


def write_inventory(
    image: str,
    target: str,
    architecture: str,
    root: Path | None = None,
) -> tuple[Path, bytes]:
    """Write the canonical inventory for one exact final image."""
    inventory = verify_images.export_image_inventory(
        image,
        target,
        architecture,
    )
    verify_images.validate_required_notices(image, inventory)
    payload = verify_images.render_inventory(inventory)
    path = verify_images.inventory_path(target, architecture, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path, payload


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--image", required=True)
    argument_parser.add_argument("--target", required=True)
    argument_parser.add_argument(
        "--architecture",
        choices=sorted(verify_images.ARCHITECTURES),
        required=True,
    )
    return argument_parser


def main() -> int:
    arguments = parser().parse_args()
    path, payload = write_inventory(
        arguments.image,
        arguments.target,
        arguments.architecture,
    )
    print(
        json.dumps(
            {
                "architecture": arguments.architecture,
                "entries": len(json.loads(payload)["entries"]),
                "path": path.relative_to(verify_images.ROOT).as_posix(),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "status": "written",
                "target": arguments.target,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
