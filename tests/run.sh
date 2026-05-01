#!/usr/bin/env bash
# Run the unit-test suite. No dependencies beyond Python 3.
# From the plugin root: ./tests/run.sh
set -euo pipefail

cd "$(dirname "$0")/.."
exec python3 -m unittest discover tests/ -v
