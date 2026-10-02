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
