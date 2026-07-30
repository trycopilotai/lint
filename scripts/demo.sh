#!/bin/sh
set -eu

workspace="${1:-demo-work}"
mkdir -p "$workspace"
printf 'zeta==1\nalpha==1\n' >"$workspace/requirements.txt"

python3 lint.py --cwd "$workspace" >"$workspace/read-only.json" || true
python3 lint.py --cwd "$workspace" --write >"$workspace/write.json"
python3 lint.py --cwd "$workspace" >"$workspace/final.json"

printf '%s\n' "$workspace/read-only.json"
printf '%s\n' "$workspace/write.json"
printf '%s\n' "$workspace/final.json"
