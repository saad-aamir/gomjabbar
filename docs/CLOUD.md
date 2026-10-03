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
- **Network access:** Custom. Tick "Also include default list of common package managers" (that keeps PyPI, npm, GitHub and Docker Hub) and add `openrouter.ai` to the allowed domains (the model API; `api.groq.com` is no longer needed since 2026-10-03). If MCPMark needs another host (for example to download initial states), add only that host too.
- **Environment variables:**
  ```
  PFS_OPENROUTER_API_KEY=<your OpenRouter API key>
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

`.claude/settings.json` runs `scripts/cloud_session_start.sh` on every session start and resume. The script does nothing outside the cloud. Inside it, it starts the Docker daemon (for the Postgres container), sets Saad as git author and installs Python dependencies with `uv sync`.

## What this means for the code

- Harness model calls go to gpt-oss-20b and gpt-oss-120b on OpenRouter with `PFS_OPENROUTER_API_KEY`, passed explicitly to LiteLLM, pinned to one upstream provider (`docs/notes/openrouter.md`). They are paid: a run only starts when its config has `spend_cap_eur` above 0, set by Saad, and it stops cleanly before crossing that cap. If the account runs out of credit, the run pauses like an exhausted quota and continues with `--resume`. (Until 2026-10-03 the harness used Groq's free tier; see `DEVIATIONS.md`.)
- No Ollama models in cloud configs. `configs/local.yaml` exists for Saad's Mac.
- Postgres: a Docker container from MCPMark's image (`pgvector/pgvector:0.8.0-pg17-bookworm`), started by the sandbox on first use. The native PostgreSQL 16 cannot restore MCPMark's PostgreSQL 17 backups, so it is not used (since 2026-10-03). The Docker daemon is not running at session start; the session hook starts it.
- Results are committed in checkpoints (SPEC section 4, Persistence).
