#!/bin/bash
# Runs at the start (and resume) of every Claude Code session.
# Does nothing on a local machine; in a cloud session it prepares services and dependencies.

# Only act inside a Claude Code cloud session.
if [ "$CLAUDE_CODE_REMOTE" != "true" ]; then
  exit 0
fi

# Start the preinstalled PostgreSQL 16 service; harmless if it is already running.
service postgresql start >/dev/null 2>&1 || true

# Install or refresh Python dependencies once pyproject.toml exists (it won't before M1 scaffolding).
cd "$CLAUDE_PROJECT_DIR" || exit 0
if [ -f pyproject.toml ]; then
  uv sync --quiet || true
fi

# Never fail session start because of this script.
exit 0
