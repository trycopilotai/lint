#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  printf 'usage: xml-format FILE\n' >&2
  exit 2
fi

temporary="${1}.lint-xml"
/opt/libxml2/bin/xmllint --format --output "$temporary" "$1"
mv "$temporary" "$1"
