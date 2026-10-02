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
