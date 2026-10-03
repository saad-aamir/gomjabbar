"""Run configuration: the RunConfig model and YAML loading.

What: parses configs/*.yaml into a validated RunConfig and computes its hash.
Why: every run is fully described by one config; its hash goes into every result row so a
result can always be traced back to the exact settings that produced it (SPEC 10).
How: the CLI loads a config with `load_config`, the runner expands it into EpisodeSpecs,
and the store writes the resolved config next to the results.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from pruefstand.models import Condition, Service

# Fault profiles the proxy knows (SPEC 5.2). "partial" is P1.
FaultProfile = Literal[
    "latency", "timeout", "rpc_error", "tool_error", "malformed", "empty", "rate_limit", "partial"
]


class ModelConfig(BaseModel):
    """One model under test (or the redteam model)."""

    name: str  # LiteLLM model string, e.g. "openrouter/openai/gpt-oss-20b"
    api_key_env: str | None = None  # name of the env var holding the key; None for Ollama
    free_tier: bool = False  # True if calls cost nothing (quota-limited instead)
    # OpenRouter only: the one upstream endpoint every request must go to, as a provider slug
    # such as "coreweave/fp4". Sent with allow_fallbacks false, so OpenRouter fails the request
    # instead of silently routing to another provider (reproducibility, DEVIATIONS.md).
    provider: str | None = None
    rpm_limit: int | None = None  # requests per minute allowed by the provider
    rpd_limit: int | None = None  # requests per day allowed by the provider
    tpm_limit: int | None = None  # tokens per minute allowed by the provider, if it has one
    tpd_limit: int | None = None  # tokens per day allowed by the provider, if known
    # Fallback price for paid models when the response carries no cost of its own. OpenRouter
    # reports the cost of every response, so for it this is only a safety net.
    price_usd_per_mtok: float | None = None

    @model_validator(mode="after")
    def _key_env_is_prefixed(self) -> ModelConfig:
        # Every key variable must start with PFS_ (SPEC 5.3): it keeps our keys separate from
        # anything Claude Code or other tools read, and lets the proxy strip them from the server.
        if self.api_key_env is not None and not self.api_key_env.startswith("PFS_"):
            raise ValueError(f"api_key_env must start with PFS_, got {self.api_key_env!r}")
        return self

    @model_validator(mode="after")
    def _provider_needs_openrouter(self) -> ModelConfig:
        # Provider pinning is an OpenRouter routing feature; elsewhere it would be ignored
        # silently, which would make the config claim a pin that never happened.
        if self.provider is not None and not self.name.startswith("openrouter/"):
            raise ValueError(f"provider is only supported for openrouter/ models: {self.name}")
        return self


class RunConfig(BaseModel):
    """A whole run, as written in configs/*.yaml (SPEC 10)."""

    run_name: str
    suite: Path  # file with one task id per line
    services: list[Service]
    models: list[ModelConfig]
    redteam_model: ModelConfig | None = None
    temperature: float | None = None  # None means the provider default
    # Output cap per model call. MCPMark uses 32768 (src/agents/mcpmark_agent.py:852); without
    # it some providers cut gpt-oss replies short, often mid-reasoning (DEVIATIONS.md).
    max_tokens: int = 32768
    seed: int
    k: int = 5  # baseline attempts per task
    paraphrases: int = 3
    fault_profiles: list[FaultProfile] = Field(default_factory=list)
    conditions: list[Condition]
    defenses: list[str] = Field(default_factory=list)
    # MCPMark's defaults: MAX_TURNS = 100 model calls (src/agents/mcpmark_agent.py:47) and
    # --timeout 3600 s per task (pipeline.py). Matched since 2026-10-03 (DEVIATIONS.md).
    max_steps: int = 100
    tool_timeout_s: float = 30
    episode_timeout_s: float = 3600
    concurrency: int = 1
    spend_cap_eur: float = 0
    usd_to_eur: float | None = None
    keep_sandboxes: bool = False

    @model_validator(mode="after")
    def _spend_cap_is_safe(self) -> RunConfig:
        # The spend cap must never be silently disabled (SPEC 10).
        all_models = list(self.models) + ([self.redteam_model] if self.redteam_model else [])
        for model in all_models:
            if model.free_tier:
                continue
            # spend_cap_eur 0 means free models only.
            if self.spend_cap_eur == 0:
                raise ValueError(
                    f"model {model.name} is not free_tier, but spend_cap_eur is 0 (free only)"
                )
            # A paid model needs a known price and a currency conversion.
            if self.usd_to_eur is None:
                raise ValueError(f"model {model.name} is paid but usd_to_eur is null")
            if model.price_usd_per_mtok is None and not _litellm_knows_price(model.name):
                raise ValueError(f"model {model.name} is paid but has no known price")
        return self

    def config_hash(self) -> str:
        """sha1 of the resolved config as canonical JSON. Same settings, same hash."""
        data = self.model_dump(mode="json")
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(canonical.encode("utf-8")).hexdigest()

    def model_by_name(self, name: str) -> ModelConfig:
        """Look up a model under test by its LiteLLM name."""
        for model in self.models:
            if model.name == name:
                return model
        raise KeyError(name)


def _litellm_knows_price(model_name: str) -> bool:
    """True if LiteLLM's price table has an entry for this model."""
    # Imported here because litellm is slow to import and only paid models need it.
    import litellm

    try:
        info = litellm.get_model_info(model_name)
    except Exception:
        # get_model_info raises for unknown models.
        return False
    return bool(info.get("input_cost_per_token"))


def load_config(path: Path | str) -> RunConfig:
    """Read a YAML config file and validate it into a RunConfig."""
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return RunConfig.model_validate(raw)


def dump_config(config: RunConfig) -> str:
    """Serialize a resolved config back to YAML (written to runs/<run_id>/config.yaml)."""
    return yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
