# Running in Claude Code cloud sessions

Facts about the environment this repo is built in, and the rules that follow from them. Source: the Claude Code docs on cloud sessions and cloud environments (code.claude.com/docs).

## The machine

- Fresh Ubuntu 24.04 x86_64 VM per session, about 4 vCPU, 16 GB RAM, 30 GB disk.
- Preinstalled: Python 3 with uv, pytest and ruff; Node 20 to 22 (22 on PATH) with npm/npx; Docker; PostgreSQL 16 and Redis (installed, not running); git and gh.
- The repo is cloned fresh at session start. Only what is committed to the repo carries over.
- The VM is reclaimed after a period of inactivity. Running background processes are not restored when the session is reopened. **Push often.**
- Bash commands time out after 2 minutes by default and can run up to 10 minutes; longer ones move to the background. The environment raises the default (see below), but long experiment runs should still use `nohup ... &` and `--only` chunks.

## The environment Saad configures at claude.ai/code

- **Name:** `pruefstand`
- **Network access:** Custom. Tick "Also include default list of common package managers" (that keeps PyPI, npm, GitHub and Docker Hub) and add `api.groq.com` to the allowed domains. If MCPMark needs another host (for example to download initial states), add only that host too.
- **Environment variables:**
  ```
  PFS_GROQ_API_KEY=<your free Groq API key>
  BASH_DEFAULT_TIMEOUT_MS=300000
  BASH_MAX_TIMEOUT_MS=600000
  ```
  Never put `ANTHROPIC_API_KEY` here: it could change how Claude Code itself authenticates and bills.
- **Setup script:**
  ```bash
  #!/bin/bash
  # Pre-cache the official filesystem MCP server so episodes don't download it each time.
  npm install -g @modelcontextprotocol/server-filesystem || true
  ```

## Repo-side session hook

`.claude/settings.json` runs `scripts/cloud_session_start.sh` on every session start and resume. The script does nothing outside the cloud. Inside it, it starts PostgreSQL and installs Python dependencies with `uv sync`.

## What this means for the code

- Harness model calls use free Groq models with `PFS_GROQ_API_KEY`, passed explicitly to LiteLLM. They are quota-limited: long runs stop when a daily quota is used up and resume the next day.
- No Ollama models in cloud configs. `configs/local.yaml` exists for Saad's Mac.
- Postgres: native service first, Docker only as a fallback.
- Results are committed in checkpoints (SPEC section 4, Persistence).
