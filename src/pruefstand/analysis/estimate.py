"""Projection of a run's size and duration from a pilot run.

What: for each model, multiply the planned number of episodes by the pilot's average
requests, tokens, seconds and euros per episode, and divide by the model's daily limits
(if it has any) to get days.
Why: Saad approves a run only after seeing its cost and duration (SPEC 11, `estimate`).
Paid models are limited by the spend cap, quota-limited ones by their daily limits.
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
    hours_serial: float  # episodes x average episode seconds, one episode at a time
    cost_eur: float
    cost_usd: float | None  # cost_eur converted back with usd_to_eur, if it is set
    borrowed: bool = False  # True if the pilot had no episode of this model (pooled average used)


def estimate(
    config: RunConfig,
    specs: list[EpisodeSpec],
    pilot: list[EpisodeResult],
    pilot_usd_to_eur: float | None = None,
) -> list[ModelEstimate]:
    """Project the planned specs from the pilot's per-episode averages.

    `pilot_usd_to_eur` is the rate the pilot run used. The pilot's euro costs are converted
    back to dollars with it and then to euros with the planned config's rate, so a change of
    `usd_to_eur` between pilot and run does not distort the estimate.
    """
    planned = Counter(spec.model for spec in specs)
    # Factor from the pilot's euros to the planned run's euros (1 if either rate is unknown).
    if pilot_usd_to_eur and config.usd_to_eur:
        eur_factor = config.usd_to_eur / pilot_usd_to_eur
    else:
        eur_factor = 1.0
    estimates = []
    for model in config.models:
        rows = [r for r in pilot if r.spec.model == model.name]
        # A model with no finished pilot episode borrows the average of the others, flagged.
        borrowed = not rows
        if borrowed:
            rows = list(pilot)
        if not rows:
            raise ValueError("the pilot has no results at all")
        n = len(rows)
        req = sum(r.llm_requests for r in rows) / n
        tok = sum(r.tokens_in + r.tokens_out for r in rows) / n
        sec = sum(r.duration_s for r in rows) / n
        cost = sum(r.cost_eur for r in rows) / n * eur_factor
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
                hours_serial=sec * episodes / 3600,
                cost_eur=cost * episodes,
                cost_usd=cost * episodes / config.usd_to_eur if config.usd_to_eur else None,
                borrowed=borrowed,
            )
        )
    return estimates


def wall_hours(config: RunConfig, estimates: list[ModelEstimate]) -> float:
    """Expected wall time of the whole run in hours, ignoring quota pauses.

    The grid runs `concurrency` episodes at once (models interleaved, DEVIATIONS.md), so the
    serial episode time of all models is shared among that many workers.
    """
    return sum(e.hours_serial for e in estimates) / max(config.concurrency, 1)
