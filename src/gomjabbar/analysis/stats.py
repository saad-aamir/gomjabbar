"""Task-level bootstrap confidence intervals.

What: `bootstrap_mean` resamples tasks with replacement 10,000 times and returns the mean
with a 95% percentile interval (SPEC 9).
Why: episodes of the same task are not independent (an easy task passes every attempt), so
resampling episodes would make intervals too narrow. Resampling whole tasks keeps each
task's episodes together.
How: metrics.py produces one value per task; this module turns it into `value [low, high]`.
The random generator is seeded from the config, and tasks are taken in sorted order, so the
same inputs always give the same interval.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

N_RESAMPLES = 10_000


@dataclass
class Estimate:
    value: float
    low: float
    high: float
    n_tasks: int

    def __str__(self) -> str:
        # Every headline number is printed as value [low, high] (SPEC 9).
        return f"{self.value:.3f} [{self.low:.3f}, {self.high:.3f}]"


def bootstrap_mean(
    per_task: dict[str, float], seed: int, n_resamples: int = N_RESAMPLES, level: float = 0.95
) -> Estimate:
    """Mean over tasks with a percentile bootstrap interval over tasks."""
    if not per_task:
        return Estimate(float("nan"), float("nan"), float("nan"), 0)
    # Sorted task order so the resample indices map to the same tasks every time.
    values = np.array([per_task[task] for task in sorted(per_task)], dtype=float)
    rng = np.random.default_rng(seed)
    # Each row picks len(values) tasks with replacement; the row mean is one resample.
    picks = rng.integers(0, len(values), size=(n_resamples, len(values)))
    means = values[picks].mean(axis=1)
    alpha = (1 - level) / 2
    low, high = np.quantile(means, [alpha, 1 - alpha])
    return Estimate(float(values.mean()), float(low), float(high), len(values))
