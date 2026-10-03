"""Tests for runner/quota.py: token bucket, token window, daily counter, clean exhaustion."""

import pytest

from pruefstand.agent.llm import QuotaExhausted
from pruefstand.config import ModelConfig
from pruefstand.runner.quota import QuotaManager, TokenBucket, TokenWindow
from pruefstand.runner.store import RunStore


class Clock:
    def __init__(self, t=1_790_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_token_bucket_allows_burst_then_waits():
    clock = Clock()
    bucket = TokenBucket(capacity=3, rate=3 / 60, clock=clock)
    for _ in range(3):
        assert bucket.wait_time() == 0
        bucket.take()
    # Empty: one token comes back after 20 seconds at 3 per minute.
    assert bucket.wait_time() == pytest.approx(20.0)
    clock.t += 20
    assert bucket.wait_time() == 0


def test_token_window_waits_for_old_entries_to_expire():
    clock = Clock()
    window = TokenWindow(limit=8000, clock=clock)
    window.add(5000)
    clock.t += 10
    window.add(2000)
    assert window.wait_time(500) == 0
    # 2000 more would exceed 8000; the first entry expires 50 s from now.
    assert window.wait_time(2000) == pytest.approx(50.01)
    clock.t += 51
    assert window.wait_time(2000) == 0


def test_window_lets_an_oversized_request_through_when_empty():
    window = TokenWindow(limit=100, clock=Clock())
    assert window.wait_time(500) == 0


def make_manager(tmp_path, clock, rpd=10):
    store = RunStore(tmp_path / "run")
    model = ModelConfig(name="m", free_tier=True, rpm_limit=1000, rpd_limit=rpd)

    async def no_sleep(seconds):
        clock.t += seconds

    return QuotaManager(store, [model], clock=clock, sleep=no_sleep), store


async def test_daily_counter_persists_and_resets_next_day(tmp_path):
    clock = Clock()
    manager, store = make_manager(tmp_path, clock, rpd=100)
    gate = manager.gate("m")
    for _ in range(4):
        await gate.acquire(10)
        gate.record(12)
    # A new manager (a resumed process) reads the same counters from quota.json.
    again, _ = make_manager(tmp_path, clock, rpd=100)
    assert again.state["m"]["requests"] == 4
    assert again.state["m"]["tokens"] == 48
    # Next UTC day: the daily counter starts over, run totals are kept.
    clock.t += 86_400
    assert again.can_start("m")
    assert again.state["m"]["requests"] == 0
    assert again.state["m"]["run_requests"] == 4


async def test_can_start_stops_before_rpd_by_running_average(tmp_path):
    clock = Clock()
    manager, _ = make_manager(tmp_path, clock, rpd=10)
    gate = manager.gate("m")
    # One episode used 4 requests, so the average is 4.
    for _ in range(4):
        await gate.acquire(1)
    manager.count_episode("m")
    assert manager.can_start("m")  # 4 + 4 <= 10
    for _ in range(4):
        await gate.acquire(1)
    manager.count_episode("m")
    assert not manager.can_start("m")  # 8 + 4 > 10: done for today
    assert manager.is_exhausted("m")


async def test_exhausted_gate_raises_cleanly(tmp_path):
    clock = Clock()
    manager, _ = make_manager(tmp_path, clock)
    gate = manager.gate("m")
    gate.mark_day_exhausted()
    with pytest.raises(QuotaExhausted):
        await gate.acquire(1)
    assert manager.all_exhausted(["m"])


async def test_rpm_throttle_sleeps(tmp_path):
    clock = Clock()
    store = RunStore(tmp_path / "run")
    model = ModelConfig(name="m", free_tier=True, rpm_limit=2)
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        clock.t += seconds

    manager = QuotaManager(store, [model], clock=clock, sleep=fake_sleep)
    gate = manager.gate("m")
    for _ in range(3):
        await gate.acquire(1)
    # The third request had to wait 30 s (2 per minute).
    assert slept == [pytest.approx(30.0)]


async def test_can_start_respects_daily_token_limit(tmp_path):
    clock = Clock()
    store = RunStore(tmp_path / "run")
    model = ModelConfig(name="m", free_tier=True, tpd_limit=200_000)

    async def no_sleep(seconds):
        clock.t += seconds

    manager = QuotaManager(store, [model], clock=clock, sleep=no_sleep)
    gate = manager.gate("m")
    await gate.acquire(1)
    gate.record(90_000)
    manager.count_episode("m")
    assert manager.can_start("m")  # 90k + 90k <= 200k
    await gate.acquire(1)
    gate.record(90_000)
    manager.count_episode("m")
    assert not manager.can_start("m")  # 180k + 90k > 200k
