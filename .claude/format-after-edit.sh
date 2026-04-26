#!/usr/bin/env bash
# PostToolUse hook: auto-format file edited by Claude Code.
# Dispatches by extension: .py -> ruff format, .md -> prettier.
# Best-effort: never blocks the agent on formatter failure.

set -u

FILE=$(jq -r '.tool_input.file_path // empty' 2>/dev/null) || exit 0
[ -z "$FILE" ] && exit 0
[ -f "$FILE" ] || exit 0

case "$FILE" in
  *.py)
    uv run --quiet ruff format "$FILE" >/dev/null 2>&1
    ;;
  *.md)
    command -v prettier >/dev/null 2>&1 \
      && prettier --write --log-level warn "$FILE" >/dev/null 2>&1
    ;;
esac

exit 0
