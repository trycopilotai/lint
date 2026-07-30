#!/usr/bin/env python3
"""Generate the cumulative terminal demo from its transcript."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def result_from_transcript(text: str, name: str) -> dict[str, Any]:
    marker = f"$ cat demo-work/{name}.json\n"
    start = text.find(marker)
    if start < 0:
        raise ValueError(f"transcript is missing {name}.json")
    payload = text[start + len(marker) :]
    value, _ = json.JSONDecoder().raw_decode(payload)
    if not isinstance(value, dict):
        raise ValueError(f"{name}.json result must be an object")
    return value


def first_file(result: dict[str, Any]) -> dict[str, Any]:
    files = result.get("files")
    if not isinstance(files, list):
        raise ValueError("demo result is missing files")
    if len(files) != 1:
        raise ValueError("demo result must contain one file")
    value = files[0]
    if not isinstance(value, dict):
        raise ValueError("demo file result must be an object")
    return value


def status_line(result: dict[str, Any]) -> str:
    value = first_file(result)
    path = value.get("path")
    status = value.get("status")
    if not isinstance(path, str):
        raise ValueError("demo file path must be a string")
    if not isinstance(status, str):
        raise ValueError("demo file status must be a string")
    return f"{path} {status.replace('_', ' ')}"


def summary_line(result: dict[str, Any]) -> str:
    summary = result.get("summary")
    if not isinstance(summary, dict):
        raise ValueError("demo result is missing its summary")
    selected = summary.get("selected")
    changed = summary.get("changed")
    status = result.get("status")
    if not isinstance(selected, int):
        raise ValueError("demo selected count must be an integer")
    if not isinstance(changed, int):
        raise ValueError("demo changed count must be an integer")
    if not isinstance(status, str):
        raise ValueError("demo status must be a string")
    return f"{status.replace('_', ' ')} · {selected} selected · " f"{changed} changed"


def render_svg(transcript: str) -> str:
    read_only = result_from_transcript(transcript, "read-only")
    write = result_from_transcript(transcript, "write")
    final = result_from_transcript(transcript, "final")
    first = html.escape(status_line(read_only))
    second = html.escape(status_line(write))
    third = html.escape(summary_line(final))
    return f"""<svg
  xmlns="http://www.w3.org/2000/svg"
  width="1200"
  height="700"
  viewBox="0 0 1200 700"
  role="img"
  aria-labelledby="title description"
>
  <title id="title">Animated lint command demo</title>
  <desc id="description">
    A terminal checks a requirements file without writing,
    applies the formatting, and confirms that the result is
    clean.
  </desc>
  <style>
    .step-one {{
      opacity: 0;
      animation: reveal-one 9s infinite;
    }}
    .step-two {{
      opacity: 0;
      animation: reveal-two 9s infinite;
    }}
    .step-three {{
      opacity: 0;
      animation: reveal-three 9s infinite;
    }}
    @keyframes reveal-one {{
      0%,
      10% {{
        opacity: 0;
      }}
      14%,
      94% {{
        opacity: 1;
      }}
      100% {{
        opacity: 0;
      }}
    }}
    @keyframes reveal-two {{
      0%,
      42% {{
        opacity: 0;
      }}
      46%,
      94% {{
        opacity: 1;
      }}
      100% {{
        opacity: 0;
      }}
    }}
    @keyframes reveal-three {{
      0%,
      74% {{
        opacity: 0;
      }}
      78%,
      94% {{
        opacity: 1;
      }}
      100% {{
        opacity: 0;
      }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      .step-one,
      .step-two,
      .step-three {{
        opacity: 1;
        animation: none;
      }}
    }}
  </style>
  <rect width="1200" height="700" rx="28" fill="#101418" />
  <circle cx="48" cy="48" r="10" fill="#ff6b6b" />
  <circle cx="80" cy="48" r="10" fill="#ffd166" />
  <circle cx="112" cy="48" r="10" fill="#55d6be" />
  <g
    fill="#f7fbff"
    font-family="ui-monospace, SFMono-Regular, monospace"
    font-size="28"
  >
    <text x="48" y="130">
      $ python3 lint.py --cwd demo-work
    </text>
    <g class="step-one">
      <text x="72" y="185" fill="#ffd166">
        {first}
      </text>
      <text x="72" y="225" fill="#a7bac5">
        read-only · source unchanged
      </text>
    </g>
    <g class="step-two">
      <text x="48" y="330">
        $ python3 lint.py --cwd demo-work --write
      </text>
      <text x="72" y="385" fill="#55d6be">
        {second}
      </text>
    </g>
    <g class="step-three">
      <text x="48" y="490">
        $ python3 lint.py --cwd demo-work
      </text>
      <text x="72" y="545" fill="#55d6be">
        {third}
      </text>
    </g>
  </g>
  <text
    x="48"
    y="640"
    fill="#a7bac5"
    font-family="ui-monospace, SFMono-Regular, monospace"
    font-size="22"
  >
    Reconstructed from evidence/demo-transcript.txt
  </text>
</svg>
"""


def main() -> int:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument(
        "--transcript",
        type=Path,
        default=ROOT / "evidence" / "demo-transcript.txt",
    )
    argument_parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "assets" / "demo.svg",
    )
    arguments = argument_parser.parse_args()
    transcript = arguments.transcript.read_text(encoding="utf-8")
    arguments.output.write_text(render_svg(transcript), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
