"""Tests for the report card and the offline HTML report."""

import re

from gomjabbar.config import RunConfig, dump_config
from gomjabbar.models import Condition
from gomjabbar.report.card import build_card, card_text
from gomjabbar.report.html import write_report
from gomjabbar.runner.store import RunStore
from tests.helpers import make_result, make_spec

PROFILES = ["timeout", "empty"]


def synthetic_results():
    """Two models, two services, baseline k=2, one paraphrase, two fault profiles."""
    results = []
    for model in ["m/a", "m/b"]:
        for task in ["filesystem/easy/c/t1", "postgres/easy/c/t2"]:
            for attempt in range(2):
                spec = make_spec(model=model, task_id=task, attempt=attempt)
                results.append(make_result(spec, passed=True))
            spec = make_spec(
                model=model, task_id=task, condition=Condition.PARAPHRASE, variant_id="para-1"
            )
            results.append(make_result(spec, passed=model == "m/a"))
            for profile in PROFILES:
                spec = make_spec(
                    model=model,
                    task_id=task,
                    condition=Condition.FAULT,
                    variant_id=f"fault-{profile}",
                )
                dead = profile == "timeout" and task.startswith("postgres") and model == "m/a"
                results.append(
                    make_result(
                        spec,
                        passed=False,
                        false_success=profile == "empty",
                        final_claim="done" if profile == "empty" else "none",
                        stop_reason="transport_failure" if dead else "final_answer",
                    )
                )
    return results


def test_card_numbers():
    cards = build_card(synthetic_results(), k=2, seed=1)
    assert [(c.model, c.scope) for c in cards] == [
        ("m/a", "all"),
        ("m/a", "filesystem"),
        ("m/a", "postgres"),
        ("m/b", "all"),
        ("m/b", "filesystem"),
        ("m/b", "postgres"),
    ]
    text = card_text(cards)
    # m/b: baseline 100%, paraphrase 0% -> drop 100 points; m/a holds under paraphrase.
    assert "drop under paraphrase                  0.0 [0.0, 0.0] pts" in text
    assert "drop under paraphrase                  100.0 [100.0, 100.0] pts" in text
    # The transport failure is reported on its own and left out of fault recovery.
    a_all = cards[0]
    assert a_all.transport_failures == 1
    assert a_all.fault_recovery["timeout"].n_tasks == 1  # only the filesystem task counts
    assert "false success: fault" in text
    assert "MCPMark rule" in text


def test_report_html_is_offline_and_complete(tmp_path):
    run_dir = tmp_path / "runs" / "r1"
    store = RunStore(run_dir)
    config = RunConfig.model_validate(
        dict(
            run_name="t",
            suite="x",
            services=["filesystem", "postgres"],
            models=[{"name": "m/a", "free_tier": True}, {"name": "m/b", "free_tier": True}],
            seed=1,
            k=2,
            conditions=["baseline", "paraphrase", "fault"],
        )
    )
    store.config_path.write_text(dump_config(config))
    for result in synthetic_results():
        store.append_result(result)
    html = write_report(run_dir).read_text()
    # Offline: no external scripts, styles, fonts or images.
    assert not re.search(r"""(src|href)=["']https?://""", html)
    assert "<script" not in html
    for heading in [
        "1. Report card",
        "2. Pass rate per condition",
        "3. Fault recovery per fault profile",
        "5. Failing episodes",
        "6. Run metadata",
    ]:
        assert heading in html
    # Charts carry intervals in their tooltips, one dot per model and condition.
    assert html.count("<svg") == 6  # 3 scopes x (conditions + faults)
    assert "baseline: 100.0% [100.0, 100.0], 2 tasks" in html
    # Transport failures are listed apart from model failures.
    assert "Transport failures (host robustness, not model results)" in html
