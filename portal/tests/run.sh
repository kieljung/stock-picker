#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"

command -v php >/dev/null || { echo "php nicht gefunden — Portal-Tests uebersprungen."; exit 0; }

FAILED=0
for test in test_*.php; do
  echo "── $test"
  php "$test" || FAILED=1
done
exit $FAILED
