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
