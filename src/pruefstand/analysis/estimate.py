"""Projection of a run's size and duration from a pilot run.

What: for each model, multiply the planned number of episodes by the pilot's average
requests and tokens per episode, and divide by the model's daily limits to get days.
Why: free-tier quotas make the full grid a multi-day job (SPEC 11, `estimate`). Saad
approves a run only after seeing these numbers.
How: `pruefstand estimate --pilot runs/<id> --config X` builds the planned specs with
runner/grid.py and calls `estimate`.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from pruefstand.config import RunConfig
from pruefstand.models import EpisodeResult, EpisodeSpec


@dataclass
class ModelEstimate:
    model: str
    episodes: int
    pilot_episodes: int
    requests_per_episode: float
    tokens_per_episode: float
    seconds_per_episode: float
    total_requests: int
    total_tokens: int
    days_by_requests: int | None  # at rpd_limit
    days_by_tokens: int | None  # at tpd_limit, if known
    hours_by_tpm: float | None  # minimum wall time at tpm_limit
    cost_eur: float


def estimate(
    config: RunConfig, specs: list[EpisodeSpec], pilot: list[EpisodeResult]
) -> list[ModelEstimate]:
    planned = Counter(spec.model for spec in specs)
    estimates = []
    for model in config.models:
        rows = [r for r in pilot if r.spec.model == model.name]
        if not rows:
            raise ValueError(f"the pilot has no results for {model.name}")
        n = len(rows)
        req = sum(r.llm_requests for r in rows) / n
        tok = sum(r.tokens_in + r.tokens_out for r in rows) / n
        sec = sum(r.duration_s for r in rows) / n
        cost = sum(r.cost_eur for r in rows) / n
        episodes = planned[model.name]
        total_requests = math.ceil(req * episodes)
        total_tokens = math.ceil(tok * episodes)
        estimates.append(
            ModelEstimate(
                model=model.name,
                episodes=episodes,
                pilot_episodes=n,
                requests_per_episode=req,
                tokens_per_episode=tok,
                seconds_per_episode=sec,
                total_requests=total_requests,
                total_tokens=total_tokens,
                days_by_requests=math.ceil(total_requests / model.rpd_limit)
                if model.rpd_limit
                else None,
                days_by_tokens=math.ceil(total_tokens / model.tpd_limit)
                if model.tpd_limit
                else None,
                hours_by_tpm=total_tokens / model.tpm_limit / 60 if model.tpm_limit else None,
                cost_eur=cost * episodes,
            )
        )
    return estimates
