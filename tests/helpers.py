"""Small builders shared by the tests.

What: functions that make valid EpisodeSpec and EpisodeResult objects with sensible defaults.
Why: many tests need a result row and only care about one or two fields.
How: tests call `make_spec(...)` or `make_result(...)` and override what they test.
"""

from gomjabbar.models import Condition, EpisodeResult, EpisodeSpec


def make_spec(**overrides) -> EpisodeSpec:
    """An EpisodeSpec for a baseline episode, with any field overridden."""
    fields = dict(
        run_id="test-run",
        task_id="filesystem/easy/cat/task",
        condition=Condition.BASELINE,
        variant_id="orig",
        model="scripted/model",
        attempt=0,
        seed=1,
    )
    fields.update(overrides)
    return EpisodeSpec(**fields)


def make_result(spec: EpisodeSpec | None = None, **overrides) -> EpisodeResult:
    """A passing EpisodeResult for the spec, with any field overridden."""
    spec = spec or make_spec()
    fields = dict(
        spec=spec,
        episode_id=spec.episode_id,
        passed=True,
        strict_passed=True,
        verify_stdout_tail="ok",
        final_claim="done",
        false_success=False,
        canary_accessed=False,
        canary_leaked=False,
        attacker_goal_met=None,
        pushback=None,
        stop_reason="final_answer",
        steps=3,
        tokens_in=100,
        tokens_out=20,
        cost_eur=0.0,
        duration_s=1.0,
        model_version="scripted-1",
        config_hash="abc",
        git_commit="deadbeef",
    )
    fields.update(overrides)
    return EpisodeResult(**fields)
