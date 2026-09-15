#!/usr/bin/env bash
# Run the same non-mutating gates locally and in GitHub Actions.
set -euo pipefail
cd "$(dirname "$0")/.."

uv lock --check
uv sync --locked --no-default-groups --group dev
uv run --locked --no-sync ruff check .
uv run --locked --no-sync ruff format --check .
uv run --locked --no-sync mypy --no-incremental
uv run --locked --no-sync python -m pytest
uv run --locked --no-sync python scripts/check_package.py
uv run --locked --no-sync python scripts/audit.py
