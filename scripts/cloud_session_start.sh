#!/bin/bash
# Runs at the start (and resume) of every Claude Code session.
# Does nothing on a local machine; in a cloud session it prepares services and dependencies.

# Only act inside a Claude Code cloud session.
if [ "$CLAUDE_CODE_REMOTE" != "true" ]; then
  exit 0
fi

# Start the Docker daemon for the Postgres container (MCPMark's PostgreSQL 17 image); the
# sandbox starts the container itself. Skipped if the daemon is already running.
if ! docker info >/dev/null 2>&1; then
  (dockerd >/tmp/dockerd.log 2>&1 &)
fi

# Everything below works on the repo, so move into it first.
cd "$CLAUDE_PROJECT_DIR" || exit 0

# Make Saad the git author of every commit made in this session (Claude stays as a Co-authored-by trailer).
git config user.name "Saad Aamir"
git config user.email "68020093+saad-aamir@users.noreply.github.com"

# Install or refresh Python dependencies once pyproject.toml exists (it won't before M1 scaffolding).
if [ -f pyproject.toml ]; then
  uv sync --quiet || true
fi

# Never fail session start because of this script.
exit 0
