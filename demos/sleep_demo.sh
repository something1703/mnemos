#!/usr/bin/env bash
# Phase 05.7 — thin wrapper so `make demo-sleep-cycle` matches the other
# demo-* targets. The actual narrative lives in sleep_demo.py because it
# needs a real MCP client and rich-ish terminal formatting.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec uv run python "$HERE/sleep_demo.py"
