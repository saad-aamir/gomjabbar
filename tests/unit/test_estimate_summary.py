"""Tests for analysis/estimate.py and analysis/summary.py."""

from pruefstand.analysis.estimate import estimate
from pruefstand.analysis.summary import summary_text
from pruefstand.config import RunConfig
from tests.helpers import make_result, make_spec


def config():
    return RunConfig.model_validate(
        dict(
            run_name="t",
            suite="x",
            services=["filesystem"],
            models=[{"name": "m", "free_tier": True, "rpd_limit": 100, "tpm_limit": 6000}],
            seed=1,
            conditions=["baseline"],
        )
    )


def test_estimate_projects_requests_and_days():
    pilot = [
        make_result(
            make_spec(model="m", attempt=0), llm_requests=10, tokens_in=5000, tokens_out=1000
        ),
        make_result(make_spec(model="m", attempt=1), llm_requests=20, tokens_in=8000, tokens_out=0),
    ]
    specs = [make_spec(model="m", attempt=i) for i in range(50)]
    (e,) = estimate(config(), specs, pilot)
    assert e.requests_per_episode == 15
    assert e.total_requests == 750
    assert e.days_by_requests == 8  # 750 / 100 rounded up
    assert e.days_by_tokens is None  # tpd unknown
    assert e.total_tokens == 350_000
    assert round(e.hours_by_tpm, 2) == round(350_000 / 6000 / 60, 2)


def test_summary_shows_pass_at_1_and_pass_hat_k():
    results = [
        make_result(make_spec(model="m", task_id=t, attempt=a), passed=(t == "a"))
        for t in ["a", "b"]
        for a in range(5)
    ]
    text = summary_text(results, k=5, seed=1)
    assert "pass@1 (state)    0.500 [" in text
    assert "pass^5 (state)    0.500 [" in text


def test_estimate_borrows_for_a_model_without_pilot_episodes():
    pilot = [make_result(make_spec(model="other"), llm_requests=10)]
    (e,) = estimate(config(), [make_spec(model="m")], pilot)
    assert e.borrowed and e.requests_per_episode == 10


def test_estimate_wall_hours_and_usd_for_a_paid_model():
    from pruefstand.analysis.estimate import wall_hours

    cfg = RunConfig.model_validate(
        dict(
            run_name="t",
            suite="x",
            services=["filesystem"],
            models=[
                {"name": "a", "price_usd_per_mtok": 1.0},
                {"name": "b", "price_usd_per_mtok": 1.0},
            ],
            seed=1,
            conditions=["baseline"],
            concurrency=2,
            spend_cap_eur=5,
            usd_to_eur=0.5,
        )
    )
    # Every pilot episode takes 360 s and costs 0.02 EUR.
    pilot = [
        make_result(make_spec(model=m, attempt=0), duration_s=360.0, cost_eur=0.02)
        for m in ("a", "b")
    ]
    specs = [make_spec(model=m, attempt=i) for m in ("a", "b") for i in range(10)]
    estimates = estimate(cfg, specs, pilot)
    for e in estimates:
        assert round(e.hours_serial, 6) == 1.0  # 10 episodes x 360 s
        assert round(e.cost_eur, 6) == 0.2
        assert round(e.cost_usd, 6) == 0.4  # 0.2 EUR / 0.5 EUR per USD
        assert e.days_by_requests is None and e.days_by_tokens is None  # no daily limits
    # Two models, one hour each, two workers: one hour of wall time.
    assert round(wall_hours(cfg, estimates), 6) == 1.0
