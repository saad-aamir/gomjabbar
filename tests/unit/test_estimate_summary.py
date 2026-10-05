"""Tests for analysis/estimate.py and analysis/summary.py."""

from gomjabbar.analysis.estimate import estimate
from gomjabbar.analysis.summary import summary_text
from gomjabbar.config import RunConfig
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
    from gomjabbar.analysis.estimate import wall_hours

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


def test_summary_reports_parse_failures_and_malformed_names():
    results = [
        make_result(
            make_spec(model="m", attempt=0),
            llm_requests=10,
            parse_failure_retries=2,
            malformed_tool_names=1,
        ),
        make_result(make_spec(model="m", attempt=1), llm_requests=10),
    ]
    text = summary_text(results, k=1, seed=1)
    assert "parse failures    2 retries / 20 requests = 0.100, in 1/2 episodes" in text
    assert "malformed names   1 calls (0.50 per episode), in 1/2 episodes" in text


def test_estimate_converts_pilot_euros_to_the_planned_rate():
    # The pilot used 4.0 EUR per USD, the planned run 1.0: a 0.40 EUR pilot episode is 0.10 USD,
    # so 10 planned episodes cost 1.00 EUR at the planned rate.
    cfg = RunConfig.model_validate(
        dict(
            run_name="t",
            suite="x",
            services=["filesystem"],
            models=[{"name": "a", "price_usd_per_mtok": 1.0}],
            seed=1,
            conditions=["baseline"],
            spend_cap_eur=5,
            usd_to_eur=1.0,
        )
    )
    pilot = [make_result(make_spec(model="a", attempt=0), cost_eur=0.40)]
    specs = [make_spec(model="a", attempt=i) for i in range(10)]
    (e,) = estimate(cfg, specs, pilot, pilot_usd_to_eur=4.0)
    assert round(e.cost_eur, 6) == 1.0
    assert round(e.cost_usd, 6) == 1.0


def test_summary_reports_empty_replies_and_the_mcpmark_rule():
    # Two attempts on one task, both pass; the second needed one empty-reply re-sample.
    results = [
        make_result(make_spec(model="m", attempt=0), steps=4),
        make_result(
            make_spec(model="m", attempt=1),
            steps=4,
            empty_reply_resamples=1,
            empty_replies_stopped=1,
        ),
    ]
    text = summary_text(results, k=2, seed=1)
    # Normal scoring: both pass. MCPMark rule: the re-sampled episode fails.
    assert "pass@1 (state)    1.000" in text
    assert "MCPMark rule      pass@1 0.500" in text
    assert "MCPMark rule      pass^2 0.000" in text
    # 1 empty reply out of 9 replies (8 steps + 1 re-sampled reply).
    assert "empty replies     1 / 9 replies = 0.111" in text
    assert "1 re-samples in 1/2 episodes" in text
