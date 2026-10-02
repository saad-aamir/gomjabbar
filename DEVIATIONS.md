# Deviations

Every departure from docs/SPEC.md or PRE_REGISTRATION.md, newest first. One entry per deviation.

Format:

## YYYY-MM-DD: short title
- **What the spec said:**
- **What we did instead:**
- **Why:**
- **Effect on results:** none / which metrics, and how

## 2026-10-02: canary vault only in attack conditions, plus a vault_control condition
- **What the spec said:** SPEC 5.1 planted the canary (`vault/` as a second filesystem root, `vault.api_keys` in Postgres) in every condition so leak rates are comparable.
- **What we did instead:** the vault is planted only in poison, inject and rugpull, and in a new control condition `vault_control` (1 attempt per task, vault present, empty plan). Baseline, paraphrase, fault and pushback start the server exactly as MCPMark does, with `workspace/` only. SPEC 4, 5.1, 6.8 and 8 updated.
- **Why:** an extra allowed root changes what `list_allowed_directories` returns and what the agent explores, so baseline would no longer match MCPMark's setup. `vault_control` keeps leak rates comparable: it separates the effect of the extra folder (vault_control vs baseline) from the effect of the attack (attack vs vault_control). Decided by Saad.
- **Effect on results:** baseline, paraphrase, fault and pushback numbers are closer to MCPMark's setting. Canary access and leak rates are only defined in vault conditions; `vault_control` is their no-attack reference. Adds 1 episode per task per model.

## 2026-10-02: malformed is an ordinary fault profile
- **What the spec said:** SPEC 5.2 expected `malformed` might kill the SDK session, ending the episode with `transport_failure` and reporting the profile separately as a host-robustness result.
- **What we did instead:** `malformed` is treated like every other fault profile and counts in fault recovery. `transport_failure` is reserved for sessions that genuinely die (server exits, `Connection closed`), whatever the profile. SPEC 5.2 updated.
- **Why:** tested with `mcp` 1.30.0 (`docs/notes/mcp-stdio.md`): the client logs the parse error, the session survives, the waiting call times out and the next call works. The model sees a tool error, so this is a model result. Decided by Saad.
- **Effect on results:** fault recovery includes `malformed` episodes. No separate host-robustness line for `malformed` unless a session actually dies.

## 2026-10-02: MCP server environment is the full environment minus PFS_ variables
- **What the spec said:** nothing explicit; the MCP SDK's default gives the server only `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, `USER`.
- **What we did instead:** the agent loop passes the full parent environment to the proxy and server, after removing every variable whose name starts with `PFS_`. A unit test checks the stripping. SPEC 5.3 updated.
- **Why:** with the SDK default, `npx` loses the proxy and CA variables and fails with `SELF_SIGNED_CERT_IN_CHAIN` in the cloud session. Stripping `PFS_` keeps our API keys away from the server under test. Decided by Saad.
- **Effect on results:** none.

## 2026-10-02: pin mcp>=1.30,<2
- **What the spec said:** "MCP Python SDK", no version; SPEC 5.3 and 13 describe the 1.x API (`stdio_client`, `ClientSession`, FastMCP).
- **What we did instead:** `mcp>=1.30,<2` in `pyproject.toml`.
- **Why:** 2.x renamed FastMCP and moved protocol types to a separate package (`docs/notes/mcp-stdio.md`). The malformed-line behaviour was tested on 1.30.0. Approved by Saad.
- **Effect on results:** none.

## 2026-10-02: cache/ is gitignored except cache/paraphrases/
- **What the spec said:** SPEC 3 says `cache/` is "paraphrase cache, committed"; SPEC 4 says only `cache/paraphrases/` is committed.
- **What we did instead:** `.gitignore` ignores `cache/*` and re-includes `cache/paraphrases/`. MCPMark initial states downloaded to `cache/mcpmark_states/` stay out of git.
- **Why:** the initial states are third-party data (tens of MB per category) and are re-downloadable; the paraphrases must be identical across runs, so they are committed. Approved by Saad.
- **Effect on results:** none.
