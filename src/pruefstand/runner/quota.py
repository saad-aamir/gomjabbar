"""Quota throttle for free-tier models: stay under per-minute limits, stop at daily limits.

What: per model, a token bucket for requests per minute, a sliding window for tokens per
minute, and a daily counter of requests and tokens persisted in runs/<run_id>/quota.json.
Why: the default models are free but rate-limited (SPEC 5.5). Going over a per-minute limit
wastes requests on 429s; going over the daily limit ends the day. The run must slow itself
down, and when a model's day is used up it must stop that model cleanly so `--resume` can
continue tomorrow with every completed result intact.
How: LiteLLMChat calls `ModelGate.acquire` before and `record` after every HTTP request, and
`mark_day_exhausted` on a daily-quota 429. The grid runner asks `QuotaManager.can_start`
before each episode and `all_exhausted` to know when to stop.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from pruefstand.agent.llm import QuotaExhausted
from pruefstand.config import ModelConfig
from pruefstand.runner.store import RunStore

# Before any episode finished, assume an episode needs this many requests.
DEFAULT_REQUESTS_PER_EPISODE = 15


def utc_day(now: float) -> str:
    """The provider day a timestamp falls in. Daily quotas are assumed to reset at 00:00 UTC."""
    return datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%d")


class TokenBucket:
    """Classic token bucket: `capacity` requests, refilled at `rate` per second."""

    def __init__(self, capacity: float, rate: float, clock: Callable[[], float]):
        self.capacity = capacity
        self.rate = rate
        self.clock = clock
        self.tokens = capacity  # start full
        self.updated = clock()

    def _refill(self) -> None:
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now

    def wait_time(self) -> float:
        """Seconds until one token is available (0 if available now)."""
        self._refill()
        if self.tokens >= 1:
            return 0.0
        return (1 - self.tokens) / self.rate

    def take(self) -> None:
        """Use one token. Call only after wait_time() returned 0."""
        self._refill()
        self.tokens -= 1


class TokenWindow:
    """Tokens used in the last 60 seconds, for a tokens-per-minute limit."""

    def __init__(self, limit: int, clock: Callable[[], float]):
        self.limit = limit
        self.clock = clock
        self.entries: deque[tuple[float, int]] = deque()  # (time, tokens)

    def _used(self) -> int:
        # Forget entries older than one minute.
        cutoff = self.clock() - 60
        while self.entries and self.entries[0][0] <= cutoff:
            self.entries.popleft()
        return sum(tokens for _, tokens in self.entries)

    def wait_time(self, wanted: int) -> float:
        """Seconds until `wanted` more tokens fit under the limit."""
        used = self._used()
        # A request larger than the whole limit can only go when the window is empty.
        if used == 0 or used + wanted <= self.limit:
            return 0.0
        # Wait until enough old entries expire.
        freed = 0
        now = self.clock()
        for stamp, tokens in self.entries:
            freed += tokens
            if used - freed + wanted <= self.limit:
                return max(stamp + 60 - now, 0.0) + 0.01
        return max(self.entries[-1][0] + 60 - now, 0.0) + 0.01

    def add(self, tokens: int) -> None:
        self.entries.append((self.clock(), tokens))


class ModelGate:
    """The RequestGate for one model (see agent/llm.py)."""

    def __init__(
        self,
        manager: QuotaManager,
        model: ModelConfig,
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
    ):
        self.manager = manager
        self.model = model
        self.clock = clock
        self.sleep = sleep
        rpm = model.rpm_limit
        # Bucket of rpm requests refilled over a minute; None means no per-minute limit.
        self.bucket = TokenBucket(rpm, rpm / 60, clock) if rpm else None
        self.window = TokenWindow(model.tpm_limit, clock) if model.tpm_limit else None
        # One request at a time passes the throttle, so concurrent episodes queue fairly.
        self.lock = asyncio.Lock()
        # Tokens reserved for the request in flight, corrected by `record`.
        self._reserved = 0

    async def acquire(self, estimated_tokens: int) -> None:
        """Wait until a request is allowed; count it. Raises QuotaExhausted for the day."""
        async with self.lock:
            while True:
                if self.manager.is_exhausted(self.model.name):
                    raise QuotaExhausted(f"daily quota used up for {self.model.name}")
                wait = 0.0
                if self.bucket is not None:
                    wait = max(wait, self.bucket.wait_time())
                if self.window is not None:
                    wait = max(wait, self.window.wait_time(estimated_tokens))
                if wait <= 0:
                    break
                await self.sleep(wait)
            if self.bucket is not None:
                self.bucket.take()
            # Reserve the estimate now; `record` corrects it with the real count.
            if self.window is not None:
                self.window.add(estimated_tokens)
            self._reserved = estimated_tokens
            # Every HTTP request counts once against the daily counter, 429s included.
            self.manager.count_request(self.model.name)

    def record(self, tokens: int) -> None:
        """Called after a successful request with the real token count."""
        if self.window is not None:
            # Replace the estimate by the real number (difference can be negative).
            self.window.add(tokens - self._reserved)
            self._reserved = 0
        self.manager.count_tokens(self.model.name, tokens)

    def mark_day_exhausted(self) -> None:
        self.manager.mark_exhausted(self.model.name)


class QuotaManager:
    """Daily counters for every model of a run, persisted in quota.json."""

    def __init__(
        self,
        store: RunStore,
        models: list[ModelConfig],
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.store = store
        self.models = {m.name: m for m in models}
        self.clock = clock
        self.sleep = sleep
        self.state = store.read_json(store.quota_path, {})
        self.gates = {m.name: ModelGate(self, m, clock, sleep) for m in models}

    # ---- daily counters ----------------------------------------------------------------

    def _today(self, model: str) -> dict:
        """Today's counter for a model, starting a fresh one when the day changes."""
        entry = self.state.setdefault(model, {})
        day = utc_day(self.clock())
        if entry.get("day") != day:
            entry["day"] = day
            entry["requests"] = 0
            entry["tokens"] = 0
            entry["exhausted"] = False
        # Run totals survive day changes; they give the average requests per episode.
        entry.setdefault("run_requests", 0)
        entry.setdefault("run_tokens", 0)
        entry.setdefault("run_episodes", 0)
        return entry

    def _save(self) -> None:
        self.store.write_json(self.store.quota_path, self.state)

    def count_request(self, model: str) -> None:
        entry = self._today(model)
        entry["requests"] += 1
        entry["run_requests"] += 1
        self._save()

    def count_tokens(self, model: str, tokens: int) -> None:
        entry = self._today(model)
        entry["tokens"] += tokens
        entry["run_tokens"] += tokens
        self._save()

    def count_episode(self, model: str) -> None:
        """One more episode finished for this model (for the running average)."""
        self._today(model)["run_episodes"] += 1
        self._save()

    def mark_exhausted(self, model: str) -> None:
        self._today(model)["exhausted"] = True
        self._save()

    # ---- decisions ---------------------------------------------------------------------

    def is_exhausted(self, model: str) -> bool:
        return bool(self._today(model)["exhausted"])

    def requests_per_episode(self, model: str) -> float:
        """Running average of requests per finished episode."""
        entry = self._today(model)
        if entry["run_episodes"] == 0:
            return DEFAULT_REQUESTS_PER_EPISODE
        return entry["run_requests"] / entry["run_episodes"]

    def tokens_per_episode(self, model: str) -> float:
        """Running average of tokens per finished episode (0 before the first one)."""
        entry = self._today(model)
        if entry["run_episodes"] == 0:
            return 0.0
        return entry["run_tokens"] / entry["run_episodes"]

    def can_start(self, model: str) -> bool:
        """False if the model is done for today, or one more average episode would cross the
        daily request limit (rpd) or the daily token limit (tpd)."""
        entry = self._today(model)
        if entry["exhausted"]:
            return False
        config = self.models[model]
        over_rpd = (
            config.rpd_limit is not None
            and entry["requests"] + self.requests_per_episode(model) > config.rpd_limit
        )
        # Groq's token limit is a rolling 24 hours, approximated here by the UTC day. If the
        # window is still full after midnight, the provider's own 429 stops the model again.
        over_tpd = (
            config.tpd_limit is not None
            and entry["tokens"] + self.tokens_per_episode(model) > config.tpd_limit
        )
        if over_rpd or over_tpd:
            # Treat it as done for today, so the run can stop cleanly.
            entry["exhausted"] = True
            self._save()
            return False
        return True

    def all_exhausted(self, models: list[str]) -> bool:
        return all(self.is_exhausted(m) for m in models)

    def gate(self, model: str) -> ModelGate:
        return self.gates[model]
