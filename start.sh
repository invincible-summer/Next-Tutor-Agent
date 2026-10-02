#!/usr/bin/env bash
# Thin entry point; the actual launcher lives in scripts/dev/start.sh.
exec "$(cd "$(dirname "$0")" && pwd)/scripts/dev/start.sh" "$@"
