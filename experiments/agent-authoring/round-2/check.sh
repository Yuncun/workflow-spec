#!/bin/bash
# Check a workflow against this round's catalog, keeping a copy of every attempt.
# usage: check.sh <path to workflow.yaml>
set -u
here="$(cd "$(dirname "$0")" && pwd)"
root="$(git -C "$here" rev-parse --show-toplevel)"
dir="$(cd "$(dirname "$1")" && pwd)"
n=$(( $(ls "$dir"/attempt-*.yaml 2>/dev/null | wc -l) + 1 ))
cp "$1" "$dir/attempt-$n.yaml"
"$root/.venv/bin/wfspec" check "$1" --catalog "$here/catalog" --json > "$dir/attempt-$n.json"
"$root/.venv/bin/wfspec" check "$1" --catalog "$here/catalog"
