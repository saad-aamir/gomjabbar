"""Tests for RunConfig loading and validation."""

from pathlib import Path

import pytest

from gomjabbar.config import RunConfig, load_config

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
    # dev.yaml has a spend cap set by Saad, and every OpenRouter model is pinned to a provider.
    config = load_config(REPO / "configs" / "dev.yaml")
    assert config.spend_cap_eur > 0
    pinned = [m for m in config.models if m.name.startswith("openrouter/")]
    assert pinned and all(m.provider for m in pinned)


def test_paid_configs_without_a_cap_refuse_to_load():
    # full.yaml and local.yaml use the same paid models but keep spend_cap_eur 0 until Saad
    # sets a cap, so they must refuse to start (CLAUDE.md: never raise a spend cap yourself).
    for name in ["full", "local"]:
        with pytest.raises(ValueError, match="spend_cap_eur is 0"):
            load_config(REPO / "configs" / f"{name}.yaml")


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


def test_provider_pin_only_for_openrouter():
    from gomjabbar.config import ModelConfig

    ModelConfig(name="openrouter/openai/gpt-oss-20b", provider="coreweave/fp4")
    with pytest.raises(ValueError, match="openrouter"):
        ModelConfig(name="groq/openai/gpt-oss-20b", provider="coreweave/fp4")


def test_known_defenses_accepted_and_typos_refused():
    config = RunConfig.model_validate(
        minimal(defenses=["pinning", "sanitizer", "description_scan"])
    )
    assert config.defenses == ["pinning", "sanitizer", "description_scan"]
    # A typo would otherwise run every episode undefended without anyone noticing.
    with pytest.raises(ValueError, match="unknown defenses"):
        RunConfig.model_validate(minimal(defenses=["sanitiser"]))
