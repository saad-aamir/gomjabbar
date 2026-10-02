"""Tests for RunConfig loading and validation."""

from pathlib import Path

import pytest

from pruefstand.config import RunConfig, load_config

REPO = Path(__file__).resolve().parents[2]


def minimal(**overrides) -> dict:
    """A small valid config as a dict."""
    data = dict(
        run_name="t",
        suite="suites/dev.txt",
        services=["filesystem"],
        models=[{"name": "groq/x", "api_key_env": "PFS_X", "free_tier": True}],
        seed=1,
        conditions=["baseline"],
    )
    data.update(overrides)
    return data


def test_repo_configs_load():
    for name in ["dev", "full", "local"]:
        config = load_config(REPO / "configs" / f"{name}.yaml")
        assert config.spend_cap_eur == 0


def test_hash_is_stable_and_sensitive():
    a = RunConfig.model_validate(minimal())
    b = RunConfig.model_validate(minimal())
    c = RunConfig.model_validate(minimal(k=3))
    assert a.config_hash() == b.config_hash()
    assert a.config_hash() != c.config_hash()


def test_paid_model_refused_when_spend_cap_is_zero():
    with pytest.raises(ValueError, match="free only"):
        RunConfig.model_validate(minimal(models=[{"name": "groq/x", "api_key_env": "PFS_X"}]))


def test_paid_model_refused_without_currency():
    with pytest.raises(ValueError, match="usd_to_eur"):
        RunConfig.model_validate(
            minimal(
                models=[{"name": "groq/x", "api_key_env": "PFS_X", "price_usd_per_mtok": 1.0}],
                spend_cap_eur=5,
            )
        )


def test_key_env_must_be_prefixed():
    with pytest.raises(ValueError, match="PFS_"):
        RunConfig.model_validate(
            minimal(models=[{"name": "x", "api_key_env": "ANTHROPIC_API_KEY", "free_tier": True}])
        )
