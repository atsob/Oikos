#!/usr/bin/env bash
# Prints the next release version: vYYYY-MM-DD.HF — today's date and the next hot fix number (0 for the first release of the day).
#
#   scripts/next_version.sh                      e.g. v2026-10-26.0, or v2026-10-26.1 if .0 already exists
#   scripts/next_version.sh 2026-10-30           the next version for another date
#   v=$(scripts/next_version.sh) && git tag "$v" && git push origin "$v"     release it (the Release image workflow does the rest)
set -euo pipefail
cd "$(dirname "$0")/.."
day="${1:-$(date +%F)}"
case "$day" in [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;; *) echo "date must be YYYY-MM-DD" >&2; exit 1;; esac
git fetch --tags --quiet origin 2>/dev/null || echo "(could not reach origin — counting local tags only)" >&2
last="$(git tag --list "v${day}.*" | sed -E "s/^v${day}\.//" | grep -E '^[0-9]+$' | sort -n | tail -1 || true)"
echo "v${day}.$(( ${last:--1} + 1 ))"
