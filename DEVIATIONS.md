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

## 2026-10-02: tpm_limit and tpd_limit model fields
- **What the spec said:** model fields `rpm_limit` and `rpd_limit` (SPEC 10); the quota throttle (SPEC 5.5) counts requests.
- **What we did instead:** optional `tpm_limit` (tokens per minute) and `tpd_limit` (tokens per day) fields. `tpm_limit` is set to 8000 for both gpt-oss models and `runner/quota.py` keeps a per-minute token budget when it is set. `tpd_limit` is left unset until Saad reads the value from the Groq console (the docs host is not reachable from the cloud session); `estimate` uses it when set, and a "tokens per day" 429 stops the model for the day either way. quota.json records tokens per day.
- **Why:** the Groq free tier answers with `x-ratelimit-limit-tokens: 8000` for gpt-oss-20b and gpt-oss-120b. Agent requests carry the whole conversation, so the token limit, not the request limit, is what throttles a run.
- **Effect on results:** none; runs only get slower.

## 2026-10-02: llm_requests field in EpisodeResult
- **What the spec said:** EpisodeResult has `steps` (model calls) but no request count (SPEC 4).
- **What we did instead:** added `llm_requests: int = 0`, the number of HTTP requests the episode sent to the model, retries included.
- **Why:** `estimate` projects requests per day from the pilot (SPEC 11), and retries after a per-minute 429 count against the daily quota too, so `steps` would underestimate.
- **Effect on results:** none.

## 2026-10-02: episode timeout counts agent time, not quota waits
- **What the spec said:** `episode_timeout_s` (900) limits an episode (SPEC 5.3).
- **What we did instead:** the timeout is checked before each model call against the episode's elapsed time minus the time spent waiting on our own quota throttle and on 429 backoffs. A hard wall-clock cap of 6 x `episode_timeout_s` (quota waits included) remains as a safety net against hangs. Each `llm_response` trace event records `throttle_s` and `finish_reason`.
- **Why:** in the first pilot the Groq free tier's 8,000 tokens per minute held both running episodes in the throttle for most of their time (22 requests in 11 minutes). With a wall-clock timeout, "timeout" would measure our quota, not the model. The pilot was stopped and restarted with this fix.
- **Effect on results:** `stop_reason = "timeout"` now means the agent and server used more than 900 s of real work. Episodes take longer in wall-clock time.

## 2026-10-02: concurrent workers interleave models
- **What the spec said:** specs are sorted by `(model, task_id, condition, variant_id, attempt)` (SPEC 5.5).
- **What we did instead:** the sort order is unchanged and still defines the episode list, but the execution queue takes the models round robin (each model's specs in sort order), so with `concurrency: 2` the two workers usually run different models.
- **Why:** Groq limits are per model; two workers on the same model share one 8,000 TPM budget and both stall.
- **Effect on results:** none on the content of results. With concurrency above 1 the line order in `results.jsonl` follows completion order, as before.

## 2026-10-02: max_tokens config field, default 32768
- **What the spec said:** no output cap is mentioned (SPEC 5.3, 10).
- **What we did instead:** a `max_tokens` config field (default and configs: 32768) passed on every model call.
- **Why:** with no cap, Groq stops gpt-oss replies at 2,048 completion tokens. In the second pilot a reply ended with `finish_reason: length` after 2,048 tokens of reasoning and no output, which the loop had to score as an empty final answer. MCPMark's own LiteLLM agent sets `max_tokens: 32768` (`src/agents/mcpmark_agent.py:852`), so this matches it. The second pilot was stopped and restarted.
- **Effect on results:** fewer truncated replies; longer replies cost more tokens per minute.
