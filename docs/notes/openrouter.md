# OpenRouter: provider choice and API behaviour

What Prüfstand relies on about OpenRouter, the model API since 2026-10-03 (`DEVIATIONS.md`). Checked by calling the API from the cloud session with `PFS_OPENROUTER_API_KEY` on 2026-10-03, through LiteLLM 1.103.2 the same way `agent/llm.py` calls it. Scripts were throwaway probes and are not in the repo.

## Why a provider is pinned

OpenRouter is a router: one model id such as `openai/gpt-oss-20b` is served by many upstream providers, with different hardware, inference engines and weight formats (fp4, fp8, bf16). By default it load-balances between them and falls back to another one on errors. Two episodes of the same task could then run on different stacks, so a difference between them could come from the stack, not the model. Pinning one provider for both models keeps every episode on the same stack.

Request field (OpenRouter provider routing), sent by `agent/llm.py` through LiteLLM's `extra_body`:

```json
"provider": {"order": ["coreweave/fp4"], "allow_fallbacks": false}
```

- `order` accepts an endpoint slug with its variant (`coreweave/fp4`), not only the provider name. That matters where a provider has several endpoints for one model (DeepInfra serves gpt-oss-120b as `deepinfra/bf16` and as `deepinfra/turbo` with a 16,384 token output cap).
- With `allow_fallbacks: false` and an unavailable pin, OpenRouter answers **HTTP 404** "No endpoints found ... Filter by Fallback removed ..." and never routes elsewhere. Tested with a made-up slug and with a provider that has no tool support for the model. `classify_error` treats 404 as fatal, so a missing pin ends the episode as `llm_error` immediately (no retries).
- The response names the provider that served it in a top-level `provider` field (for example `"CoreWeave"`). LiteLLM keeps it as `response.provider`. It is written to every `llm_response` trace event and to the result row (`provider`).

## Providers that serve both models (2026-10-03)

From `GET /api/v1/models/openai/<model>/endpoints`. Prices in USD per million tokens (input / output).

| Provider (slug) | 20b | 120b | Tools | Cached-input price | Uptime 30 min (20b / 120b) |
| --- | --- | --- | --- | --- | --- |
| CoreWeave (`coreweave/fp4`) | fp4, 0.03 / 0.13 | fp4, 0.03 / 0.17 | yes | yes | 99.95 / 99.66 |
| DeepInfra (`deepinfra/bf16`) | bf16, 0.03 / 0.14 | bf16, 0.037 / 0.17 | yes | no | 99.99 / 99.95 |
| DekaLLM (`dekallm/bf16`) | bf16, 0.029 / 0.14 | bf16, 0.03 / 0.18 | yes | yes | 99.54 / 99.86 |
| AkashML | fp4, 0.02 / 0.10 | bf16, 0.037 / 0.187 | yes | 120b only | 99.66 / 99.96 |
| Parasail (`parasail/fp4`) | fp4, 0.03 / 0.15 | fp4, 0.10 / 0.75 | yes | yes | 99.92 / 100 |
| Groq | 0.075 / 0.30 | 0.15 / 0.60 | yes | yes | 93.8 (degraded) / 98.7 |
| Novita, SiliconFlow, Google Vertex, Amazon Bedrock | | | no tool support for at least one of the two | | |

Every endpoint above offers a context of about 131k tokens and an output cap of at least 32,768 tokens, so `max_tokens: 32768` fits.

## Tool-calling tests

Probe: system prompt in the style of `SYSTEM_PROMPT_V1`, two tools (`list_directory`, `write_file`), a task that needs one call of each and then a DONE message, run to the end with fake tool results. Pass means the model called `list_directory`, then `write_file`, then said DONE.

| Provider | 20b passed | 120b passed | Notes |
| --- | --- | --- | --- |
| CoreWeave | 22 of 30 | 20 of 20 | no 429s, even with 10 requests in parallel |
| DeepInfra | 11 of 20 | 10 of 10 | no 429s |
| DekaLLM | 2 of 11 | 1 of 1 | 20b: 6 of 11 got upstream 429s; others looped (once on a path with a leading space) |
| AkashML | 0 of 11 | 1 of 1 | 20b: upstream 429 on every request |
| Parasail | 6 of 11 | 1 of 1 | 3 upstream 429s on 20b |

Totals over several batches (1, 5, 6 to 8, and 10 trials in parallel). Few trials, so these are a smoke test of tool calling, not a measurement of the models.

**Every 20b failure that was not a 429 was the same thing on every provider:** the tool name came back as `write_file<|channel|>commentary`, a piece of gpt-oss's Harmony chat format leaking into the function name. It appeared on CoreWeave, DeepInfra and Parasail at similar rates (between 1 in 8 and 1 in 2 trials per batch in this probe), so it belongs to gpt-oss-20b on the common serving stack, not to one provider, and it does not decide the choice. It never appeared on 120b. In the old Groq pilot it did not appear either: Groq's parser rejects such output as `tool_use_failed`, which the client retries.

What happens in a real episode: the loop sends the call to the MCP server under the broken name, the server answers with an "unknown tool" error, and the model sees that error and can try again. Prüfstand does not repair the name, because that would change the agent. The rate is visible in traces (`tool_call` events with `<|` in the name) and is a known source of 20b failures.

## Choice: `coreweave/fp4` for both models

- Serves both models from one endpoint each, so the slug is unambiguous.
- fp4 for both models, which is the weight format OpenAI released gpt-oss in (MXFP4), on the same provider and stack for both.
- Tool calling passed on 120b every time; 20b behaves like on every other provider.
- No upstream 429s under parallel load, unlike DekaLLM, AkashML and Parasail.
- Reports cached input tokens, and cache reads are discounted.
- Cheapest of the clean options.

Runner-up: `deepinfra/bf16` (higher uptime, but bf16 instead of the native format, and no cached-token reporting). Switching later is a config change (`provider:`) and makes results incomparable, so it would need a fresh run and a `DEVIATIONS.md` entry.

## Cost reporting

- Every response carries `usage.cost` in US dollars (cache discounts included). LiteLLM 1.103.2 keeps it as `response.usage.cost` and also copies it to `response._hidden_params["response_cost"]`. `agent/llm.py` reads `usage.cost` first; `price_usd_per_mtok` in the config is only a fallback (set to CoreWeave's output price, an upper bound for input plus output).
- Cached input tokens: `usage.prompt_tokens_details.cached_tokens`. Recorded as `tokens_cached` per response and `tokens_cached_in` per result row. They are part of `prompt_tokens`, not extra.
- LiteLLM's own price table also knows these models (`openrouter/openai/gpt-oss-20b`: 0.018 / 0.09 USD per million), but that is the cheapest provider's price, not the pinned one, so it is not used.

## Limits on a paid account

- `GET /api/v1/key` for this key: credit `limit` 5 (USD), `rate_limit.requests` -1 ("deprecated and safe to ignore"), `is_free_tier` false, `expires_at` 2026-10-10. There is no daily request or token limit for paid models, so `rpd_limit`, `tpm_limit` and `tpd_limit` are not set.
- The configs keep `rpm_limit: 60` per model as our own ceiling. With `concurrency: 2` and interleaved models a run sends far fewer requests than that, so it only matters if concurrency goes up.
- Upstream providers can still be busy: OpenRouter then answers 429 "temporarily rate-limited upstream. Please retry shortly". It has no "per day" wording, so the client retries with backoff.
- No credit left: OpenRouter answers 402 ("Insufficient credits"), and 403 "Key limit exceeded" once a key's own spending limit is reached. Both are treated like an exhausted daily quota: the model pauses, nothing is written for the episode in flight, and the run resumes with `--resume` after credit is added.
- The key's $5 limit is a second, independent cap next to `spend_cap_eur`.
