"""Tests for the retry decisions in agent/llm.py (no API calls)."""

from pruefstand.agent.llm import classify_error


class FakeAPIError(Exception):
    """An exception shaped like a LiteLLM error: a message and a status code."""

    def __init__(self, message: str, status_code: int | None):
        super().__init__(message)
        self.status_code = status_code


def test_per_minute_429_waits_as_told():
    exc = FakeAPIError(
        "Rate limit reached for model `openai/gpt-oss-20b` on tokens per minute (TPM): "
        "Limit 8000, Used 7000, Requested 2000. Please try again in 7.5s.",
        429,
    )
    verdict = classify_error(exc, attempt=1)
    assert verdict.action == "retry"
    assert verdict.wait_s == 8.0


def test_minutes_in_retry_hint():
    exc = FakeAPIError("Rate limit reached ... Please try again in 1m2.5s.", 429)
    assert classify_error(exc, 1).wait_s == 63.0


def test_daily_429_is_quota_day():
    exc = FakeAPIError(
        "Rate limit reached for model on requests per day (RPD): Limit 1000, Used 1000", 429
    )
    assert classify_error(exc, 1).action == "quota_day"
    exc = FakeAPIError("Rate limit reached on tokens per day (TPD): Limit 200000", 429)
    assert classify_error(exc, 1).action == "quota_day"


def test_request_too_large_is_fatal():
    exc = FakeAPIError("Request too large for model on tokens per minute (TPM)", 413)
    assert classify_error(exc, 1).action == "fatal"


def test_server_errors_retry_with_backoff():
    assert classify_error(FakeAPIError("oops", 503), 1).action == "retry"
    assert classify_error(FakeAPIError("oops", 503), 3).wait_s == 8.0


def test_bad_request_is_fatal_but_tool_use_failed_retries():
    assert classify_error(FakeAPIError("invalid model", 400), 1).action == "fatal"
    assert classify_error(FakeAPIError("tool_use_failed", 400), 1).action == "retry"


def test_daily_429_with_short_hint_is_waited_out():
    exc = FakeAPIError(
        "Rate limit reached on tokens per day (TPD): Limit 200000, Used 199000, "
        "Requested 5000. Please try again in 2m30s.",
        429,
    )
    verdict = classify_error(exc, 1)
    assert verdict.action == "retry"
    assert verdict.wait_s == 151.0


def test_daily_429_with_long_hint_is_quota_day():
    exc = FakeAPIError(
        "Rate limit reached on requests per day (RPD). Please try again in 3h2m1s.", 429
    )
    assert classify_error(exc, 1).action == "quota_day"


def test_out_of_credits_stops_the_model_for_now():
    # OpenRouter answers 402 when the account has no credit, 403 when a key's limit is hit.
    exc = FakeAPIError("Insufficient credits. Add more using https://openrouter.ai/...", 402)
    assert classify_error(exc, 1).action == "quota_day"
    exc = FakeAPIError("Key limit exceeded (total limit). Manage it using ...", 403)
    assert classify_error(exc, 1).action == "quota_day"


def test_upstream_rate_limit_retries():
    # OpenRouter's 429 when the pinned provider is busy: no daily wording, so retry.
    exc = FakeAPIError(
        "openai/gpt-oss-20b is temporarily rate-limited upstream. Retry shortly", 429
    )
    assert classify_error(exc, 1).action == "retry"


# ---- the LiteLLM client, with litellm.acompletion replaced by a fake --------------------------


def fake_response(cost: float | None):
    """An object shaped like a LiteLLM response from OpenRouter."""
    from types import SimpleNamespace as NS

    message = NS(content="DONE", tool_calls=None, reasoning_content=None)
    usage = NS(
        prompt_tokens=1000,
        completion_tokens=200,
        prompt_tokens_details=NS(cached_tokens=640),
        cost=cost,
    )
    return NS(
        choices=[NS(message=message, finish_reason="stop")],
        usage=usage,
        model="openai/gpt-oss-20b",
        system_fingerprint=None,
        provider="CoreWeave",
    )


def run_client(monkeypatch, model_fields: dict, cost: float | None):
    """Call LiteLLMChat.complete once against the fake; return (reply, kwargs sent)."""
    import asyncio

    import litellm

    from pruefstand.agent.llm import LiteLLMChat
    from pruefstand.config import ModelConfig

    sent: dict = {}

    async def fake_acompletion(**kwargs):
        sent.update(kwargs)
        return fake_response(cost)

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    monkeypatch.setenv("PFS_TEST_KEY", "k")
    model = ModelConfig(
        name="openrouter/openai/gpt-oss-20b", api_key_env="PFS_TEST_KEY", **model_fields
    )
    client = LiteLLMChat(model, usd_to_eur=0.5)
    reply = asyncio.run(client.complete([{"role": "user", "content": "hi"}], []))
    return reply, sent


def test_pinned_provider_is_sent_without_fallbacks(monkeypatch):
    reply, sent = run_client(monkeypatch, {"provider": "coreweave/fp4"}, cost=0.002)
    assert sent["extra_body"] == {
        "provider": {"order": ["coreweave/fp4"], "allow_fallbacks": False}
    }
    # The serving provider and cached tokens come back on the reply.
    assert reply.provider == "CoreWeave"
    assert reply.tokens_cached == 640


def test_no_provider_means_no_routing_options(monkeypatch):
    _, sent = run_client(monkeypatch, {}, cost=0.002)
    assert "extra_body" not in sent


def test_reported_cost_wins_over_configured_price(monkeypatch):
    # usage.cost is 0.002 USD; at 0.5 EUR per USD that is 0.001 EUR. The configured price
    # (1 USD per million tokens) would give 0.0006 EUR and must not be used.
    reply, _ = run_client(monkeypatch, {"price_usd_per_mtok": 1.0}, cost=0.002)
    assert abs(reply.cost_eur - 0.001) < 1e-12


def test_configured_price_is_the_fallback(monkeypatch):
    # No cost in the response: 1,200 tokens at 1 USD per million = 0.0012 USD = 0.0006 EUR.
    reply, _ = run_client(monkeypatch, {"price_usd_per_mtok": 1.0}, cost=None)
    assert abs(reply.cost_eur - 0.0006) < 1e-12


def test_harmony_parse_error_retries():
    # Seen in the first OpenRouter pilot (2026-10-03): CoreWeave's parser rejected one sample.
    exc = FakeAPIError(
        "litellm.BadRequestError: OpenrouterException - Upstream error from CoreWeave: "
        'unexpected tokens remaining in message header: Some("file size is 1200 bytes")',
        400,
    )
    assert classify_error(exc, 1).action == "retry"


# ---- parse-failure retries: counted and capped at 3 per call -----------------------------------

PARSE_ERROR = FakeAPIError(
    "Upstream error from CoreWeave: unexpected tokens remaining in message header", 400
)


def run_with_failures(monkeypatch, failures: int):
    """Complete once while the provider raises PARSE_ERROR `failures` times, then answers."""
    import asyncio

    import litellm

    from pruefstand.agent import llm as llm_module
    from pruefstand.agent.llm import LiteLLMChat
    from pruefstand.config import ModelConfig

    calls = {"n": 0}

    async def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] <= failures:
            raise PARSE_ERROR
        return fake_response(0.001)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(litellm, "acompletion", flaky)
    monkeypatch.setattr(llm_module.asyncio, "sleep", no_sleep)
    model = ModelConfig(name="openrouter/openai/gpt-oss-20b", free_tier=True)
    return asyncio.run(LiteLLMChat(model).complete([{"role": "user", "content": "hi"}], []))


def test_parse_retries_are_counted(monkeypatch):
    reply = run_with_failures(monkeypatch, failures=3)
    assert reply.parse_retries == 3  # three retries, the fourth request worked
    assert reply.attempts == 4


def test_fourth_parse_failure_is_llm_error(monkeypatch):
    import pytest

    from pruefstand.agent.llm import LLMError

    with pytest.raises(LLMError, match="parse failure after 3 retries") as info:
        run_with_failures(monkeypatch, failures=4)
    assert info.value.parse_retries == 3
    assert info.value.attempts == 4
