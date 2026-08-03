#!/bin/sh
set -eu

workspace="${1:-demo-work}"
if [ -e "$workspace" ]; then
  printf 'demo workspace already exists: %s\n' "$workspace" >&2
  exit 2
fi
printf '$ ./scripts/demo.sh %s\n' "$workspace"
mkdir -p "$workspace"
printf 'zeta==1\nalpha==1\n' >"$workspace/requirements.txt"

read_only_status=0
python3 lint.py --json --cwd "$workspace" requirements.txt \
  >"$workspace/read-only.json" || read_only_status=$?
if [ "$read_only_status" -ne 1 ]; then
  printf 'read-only demo returned %s, expected 1\n' \
    "$read_only_status" >&2
  exit 1
fi
python3 lint.py --json --cwd "$workspace" --write requirements.txt \
  >"$workspace/write.json"
python3 lint.py --json --cwd "$workspace" requirements.txt \
  >"$workspace/final.json"

for result in read-only write final; do
  printf '\n$ cat %s/%s.json\n' "$workspace" "$result"
  cat "$workspace/$result.json"
done
