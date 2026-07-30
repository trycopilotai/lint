#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  printf 'usage: kotlin-format FILE\n' >&2
  exit 2
fi

ktlint --format "$1"
ktlint --format "$1"
