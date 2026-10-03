"""Spend guard: stop a run cleanly before it would cost more than spend_cap_eur.

What: tracks the cumulative euro cost of a run and decides whether one more episode fits.
Why: SPEC 5.5. The default models are free (cost 0), and config.py already refuses paid
models when the cap is 0, so this guard matters only if Saad ever adds a paid model.
How: the grid runner builds a BudgetGuard from the results already written and asks
`can_start()` before each episode, then calls `add(cost)` after it.
"""

from __future__ import annotations


class BudgetGuard:
    def __init__(self, spend_cap_eur: float, costs_so_far: list[float]):
        self.cap = spend_cap_eur
        self.spent = sum(costs_so_far)
        self.episodes = len(costs_so_far)

    def average_cost(self) -> float:
        return self.spent / self.episodes if self.episodes else 0.0

    def can_start(self) -> bool:
        """False if one more average episode would cross the cap."""
        if self.cap == 0:
            # Free models only (enforced by config.py): nothing can be spent.
            return True
        return self.spent + self.average_cost() <= self.cap

    def add(self, cost_eur: float) -> None:
        self.spent += cost_eur
        self.episodes += 1


# ---- key spend guard: the provider account's total, across runs ------------------------------

# OpenRouter's endpoint describing the calling key: total usage and limit, in US dollars.
OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"


def openrouter_key_usage_usd(api_key: str) -> float:
    """Total USD spent so far with this OpenRouter key (all runs, all time).

    Retried a few times; if OpenRouter cannot be asked, this raises, and the guard treats
    that as "do not start" (fail closed), because the limit must never be crossed.
    """
    import httpx

    last_error: Exception | None = None
    for _ in range(3):
        try:
            response = httpx.get(
                OPENROUTER_KEY_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30,
            )
            response.raise_for_status()
            return float(response.json()["data"]["usage"])
        except Exception as exc:  # noqa: BLE001 - any failure is retried, then reported
            last_error = exc
    raise RuntimeError(f"could not read OpenRouter key usage: {last_error}")


class KeySpendGuard:
    """Stops a run before the provider key's total spend could cross a fixed limit.

    What: before each episode, reads the key's total usage from the provider and checks that
    `in_flight` more average episodes still fit under `cap_usd`.
    Why: BudgetGuard only knows the current run. Saad set an absolute limit for the whole
    key (all runs, probes and paraphrase generation together), so the account itself is
    asked, which also counts spend this process never saw.
    How: the grid runner calls `can_start(avg_episode_usd, in_flight)`; `usage` is a function
    so tests can replace the HTTP call.
    """

    def __init__(self, cap_usd: float, usage):
        self.cap_usd = cap_usd
        self.usage = usage  # callable returning total USD spent with the key
        self.last_usage: float | None = None  # last value read, for the log
        self.error = ""  # why the last check refused, if it could not read the usage

    def can_start(self, avg_episode_usd: float, in_flight: int) -> bool:
        """False if `in_flight` more average episodes could cross the cap, or usage is unknown."""
        try:
            self.last_usage = self.usage()
        except Exception as exc:  # noqa: BLE001 - unknown usage means do not spend more
            self.error = str(exc)
            return False
        return self.last_usage + avg_episode_usd * max(1, in_flight) <= self.cap_usd
