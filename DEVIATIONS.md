# Deviations

Every departure from docs/SPEC.md or PRE_REGISTRATION.md, newest first. One entry per deviation.

Format:

## YYYY-MM-DD: short title
- **What the spec said:**
- **What we did instead:**
- **Why:**
- **Effect on results:** none / which metrics, and how

## 2026-10-03: Harmony parse errors from the provider are retried
- **What the spec said:** retry on transient API errors (SPEC 5.3); a 400 is fatal and ends the episode as `llm_error`.
- **What we did instead:** an HTTP 400 whose text says "unexpected tokens remaining in message header" (or "unexpected token ... while expecting") is retried like Groq's `output_parse_failed`, which was already retried. The first OpenRouter pilot (`runs/pilot-dev-20261003-031537`) was stopped after its first episode ended as `llm_error` on exactly this error, and a fresh pilot was started after the fix; the stopped one is kept for the record.
- **Why:** it is CoreWeave's parser rejecting one sampled gpt-oss reply (the Harmony chat format), not a malformed request. Sampling again usually works. Scoring it as a model failure would measure the provider's parser.
- **Effect on results:** fewer `llm_error` episodes; every retry is still counted in `llm_requests` and in cost.

## 2026-10-03: pilot restarted from scratch on OpenRouter
- **What the spec said:** M1 step 14: run `pilot` with 3 tasks for each model, then `estimate`.
- **What we did instead:** the Groq pilot `runs/pilot-dev-20261002-182600` (2 of 6 episodes finished) is kept in git for the record but not used. A new pilot with the OpenRouter config was run from scratch, and `estimate` uses only that one.
- **Why:** different serving stack, provider and limits; the Groq episodes are not comparable. Decided by Saad.
- **Effect on results:** none on the M1 numbers, which come only from OpenRouter runs.

## 2026-10-03: gpt-oss-20b tool-name leak is not repaired
- **What the spec said:** nothing; the loop passes tool calls to the server as the model wrote them (SPEC 5.3).
- **What we did instead:** nothing is changed. On OpenRouter, gpt-oss-20b sometimes returns a tool name with a piece of its Harmony chat format attached (`write_file<|channel|>commentary`). The server answers "unknown tool", the model sees the error and can retry.
- **Why:** seen on CoreWeave, DeepInfra and Parasail at similar rates, never on 120b (`docs/notes/openrouter.md`). Repairing the name would change the agent and make the harness more forgiving than MCPMark's. Groq rejected such output as `tool_use_failed` and we retried, so the effect was hidden before.
- **Effect on results:** gpt-oss-20b loses some steps (and sometimes episodes) to these errors. Countable from traces (`tool_call` names containing `<|`).

## 2026-10-03: out-of-credits answers pause the model like an exhausted quota
- **What the spec said:** SPEC 5.5 handles daily-quota 429s; any other API error ends the episode as `llm_error`.
- **What we did instead:** OpenRouter's 402 ("Insufficient credits") and "Key limit exceeded" are classified like a daily-quota 429: the model is marked exhausted, the episode in flight is not written, and the run stops cleanly so `--resume` can continue after credit is added.
- **Why:** otherwise every remaining episode would be scored as `llm_error`, which is a billing problem, not a model result.
- **Effect on results:** none.

## 2026-10-03: estimate reports hours and dollars
- **What the spec said:** `estimate` projects episodes, requests, euros and days at `rpd_limit` (SPEC 11).
- **What we did instead:** it also prints serial episode hours per model, the run's wall time at the config's `concurrency`, the cost in USD (euros divided by `usd_to_eur`) and the run total against `spend_cap_eur`. Days at `rpd_limit` and `tpd_limit` are only printed for models that have those limits.
- **Why:** paid models have no daily limits; what Saad approves is euros and hours.
- **Effect on results:** none.

## 2026-10-03: CLAUDE.md paid-model rule and spend caps
- **What the spec said:** CLAUDE.md hard rule "Never call a paid model. The configs use free models only (`spend_cap_eur: 0`)."; SPEC 10 dev config had `spend_cap_eur: 0`, `usd_to_eur: null`.
- **What we did instead:** the rule is now "Only call paid models in a run whose config has spend_cap_eur above 0, set by Saad. Never raise a spend cap yourself." `configs/dev.yaml` has `spend_cap_eur: 5` and `usd_to_eur: 4.44` (both set by Saad). `configs/full.yaml` and `configs/local.yaml` got the same models but keep `spend_cap_eur: 0`, so they refuse to start until Saad sets a cap (tested). `PRE_REGISTRATION.md` (still DRAFT) had its bracketed model proposal updated to the OpenRouter models; no hypothesis or threshold was touched. `docs/CLOUD.md` names the new key and domain.
- **Why:** Groq's paid tier is unavailable, so the free-only rule could not stay. Decided by Saad.
- **Effect on results:** euro figures are the provider's USD cost times 4.44. That factor is Saad's choice and is far above a market rate (roughly 0.85 to 0.95 EUR per USD in 2026), so reported euros are about five times the market conversion, and the 5 EUR cap stops a run at about 1.13 USD of real spend. Raw USD can be recovered as `cost_eur / 4.44`.

## 2026-10-03: Groq-specific limits removed
- **What the spec said:** dev config `rpm_limit: 30`, `rpd_limit: 1000`, plus our `tpm_limit: 8000` and `tpd_limit: 200000` (entries of 2026-10-02 below).
- **What we did instead:** the OpenRouter models set only `rpm_limit: 60` as our own ceiling; no `rpd_limit`, `tpm_limit` or `tpd_limit`. `concurrency` stays 2. The generic quota and retry code is unchanged and still tested: token bucket, token window, daily counters, per-day 429 handling, the parse-failure retry and `max_tokens: 32768`.
- **Why:** `GET /api/v1/key` reports no request rate limit for this paid key (`rate_limit.requests: -1`) and OpenRouter has no daily limit for paid models. Upstream 429s are retried with backoff.
- **Effect on results:** none on outcomes; runs are no longer throttled to Groq's quotas.

## 2026-10-03: cost from the provider's reported cost; cached tokens recorded
- **What the spec said:** cost via `litellm.completion_cost` converted with `usd_to_eur`, or `price_usd_per_mtok` for models LiteLLM cannot price (SPEC 5.3, 10).
- **What we did instead:** `agent/llm.py` uses the cost the response reports (`usage.cost`, OpenRouter, in USD, cache discounts included) first, then `price_usd_per_mtok`, then `litellm.completion_cost`. `price_usd_per_mtok` is set to CoreWeave's output price (0.13 for 20b, 0.17 for 120b), an upper bound used only if a response lacks a cost. Cached input tokens (`usage.prompt_tokens_details.cached_tokens`) are recorded per response and as a new `tokens_cached_in` field in EpisodeResult. SPEC 4 and 5.3 updated.
- **Why:** LiteLLM's price table holds the cheapest OpenRouter provider's price, not the pinned provider's. The reported cost is what is actually billed.
- **Effect on results:** cost figures match OpenRouter's billing.

## 2026-10-03: one pinned OpenRouter provider, recorded per result
- **What the spec said:** nothing about upstream providers (SPEC 5.3, 10).
- **What we did instead:** a new optional model field `provider`. When set, every request carries OpenRouter provider routing `{order: [<provider>], allow_fallbacks: false}`. Both models and the redteam model use `coreweave/fp4`. The provider that actually served each reply (`response.provider`) goes into every `llm_response` trace event and into a new `provider` field of EpisodeResult (several would be joined with "+", which would show the pin failed). The pin is part of the config, so it is in `runs/<id>/config.yaml` and the config hash. SPEC 4, 5.3 and 10 updated.
- **Why:** reproducibility. OpenRouter otherwise load-balances between providers with different engines and weight formats. CoreWeave serves both models in fp4 (the released format), passed tool calling on 120b every time, had no upstream 429s under parallel load, and reports cached tokens (`docs/notes/openrouter.md`). Decided by Saad (pin one provider, no fallbacks); provider chosen by Claude after testing.
- **Effect on results:** all episodes run on one stack. If CoreWeave is down, requests fail with 404 or 5xx instead of moving elsewhere, so episodes may end as `llm_error`.

## 2026-10-03: model API switched from Groq to OpenRouter
- **What the spec said:** two free gpt-oss models on Groq, key `PFS_GROQ_API_KEY`, `free_tier: true` (SPEC 5.3, 10); redteam model `groq/llama-3.3-70b-versatile`.
- **What we did instead:** `openrouter/openai/gpt-oss-20b` and `openrouter/openai/gpt-oss-120b`, key `PFS_OPENROUTER_API_KEY`, `free_tier: false`, in dev, full and local configs. Redteam model `openrouter/openai/gpt-oss-120b`. `openrouter.ai` is in the allowed domains.
- **Why:** Groq's free tier allowed about 200k tokens per model per day, which made the M1 baseline a multi-day job, and Groq's paid tier is unavailable. Decided by Saad.
- **Effect on results:** same model weights, different provider and serving stack, so numbers are not comparable with the Groq pilot. The redteam model changes from Llama 3.3 70B to gpt-oss-120b, which is also one of the models under test (relevant from M2: paraphrases are written by one of the tested models).

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
- **What we did instead:** optional `tpm_limit` (tokens per minute) and `tpd_limit` (tokens per day) fields. `tpm_limit` is set to 8000 for both gpt-oss models and `runner/quota.py` keeps a per-minute token budget when it is set. `tpd_limit` is set to 200000 for both gpt-oss models, the value in Groq's own 429 message ("tokens per day (TPD): Limit 200000", seen in the third pilot); `estimate` uses it, and `can_start` stops a model for the day when one more average episode would cross it. quota.json records tokens per day.
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

## 2026-10-02: a per-day 429 with a short retry hint is waited out
- **What the spec said:** a 429 that says the daily quota is exhausted marks that model as done for today (SPEC 5.5).
- **What we did instead:** if the per-day 429 also says "try again in" 15 minutes or less, the client waits that long and retries (the wait counts as quota time, not agent time). Only a per-day 429 without a hint, or with a longer one, marks the model done for today. The provider's message is now written to run.log when a model pauses.
- **Why:** in the third pilot gpt-oss-120b was marked done for the day after 33 requests and about 146k tokens, yet a minute later both a tiny and a 4.6k-token request succeeded. Groq's daily token limit appears to be a rolling window, so stopping for the whole day wastes most of it.
- **Effect on results:** none on episode outcomes; runs pause less often.
