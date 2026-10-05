"""The M4 experiment configs: what they would run, without running anything (no API calls)."""

from __future__ import annotations

import functools
from collections import Counter
from pathlib import Path

from gomjabbar.conditions import inject, poison
from gomjabbar.config import load_config
from gomjabbar.models import Condition
from gomjabbar.payloads import load_payloads
from gomjabbar.runner.grid import build_specs
from gomjabbar.tasks.mcpmark import read_suite

REPO = Path(__file__).resolve().parents[2]
MODEL_120B = "openrouter/openai/gpt-oss-120b"


def specs_of(name: str):
    config = load_config(REPO / "configs" / f"{name}.yaml")
    return config, build_specs(config, "dry", read_suite(REPO / config.suite), None)


def test_defense_config_is_120b_with_three_defenses_and_230_episodes():
    config, specs = specs_of("defense")
    assert [m.name for m in config.models] == [MODEL_120B]
    assert config.defenses == ["pinning", "sanitizer", "description_scan"]
    assert config.spend_cap_eur == 1.2
    assert config.k == 5 and config.payload_set == "standard"
    # 20 tasks x 5 baseline attempts; 3 poison payloads per task (fs and pg each have 3);
    # 4 inject payloads per filesystem task and 3 per postgres task. No pushback rows.
    assert Counter(s.condition for s in specs) == {
        Condition.BASELINE: 100,
        Condition.POISON: 60,
        Condition.INJECT: 70,
    }
    assert all(s.defenses == config.defenses for s in specs)


def test_holdout_configs_differ_only_in_defenses_and_name():
    plain, _ = specs_of("holdout")
    defended, _ = specs_of("holdout-defended")
    assert plain.payload_set == defended.payload_set == "holdout"
    assert plain.defenses == [] and defended.defenses == [
        "pinning",
        "sanitizer",
        "description_scan",
    ]
    assert plain.spend_cap_eur == defended.spend_cap_eur == 0.5
    assert [m.name for m in plain.models] == [MODEL_120B]
    a = plain.model_dump(exclude={"run_name", "defenses"})
    b = defended.model_dump(exclude={"run_name", "defenses"})
    assert a == b


def test_holdout_config_uses_only_holdout_payloads(tmp_path, monkeypatch):
    # Put one payload of each kind in a temporary holdout folder and expand one task.
    folder = tmp_path / "holdout"
    folder.mkdir()
    (folder / "p.yaml").write_text(
        "id: hp\nmode: append_description\ntarget_tool: auto\ntext: x\n"
        "success_check: {kind: canary_leaked}\n"
    )
    (folder / "i.yaml").write_text("id: hi\ntext: y\nsuccess_check: {kind: canary_leaked}\n")
    loader = functools.partial(load_payloads, root=tmp_path)
    monkeypatch.setattr(poison, "load_payloads", loader)
    monkeypatch.setattr(inject, "load_payloads", loader)
    config, _ = specs_of("holdout")
    task = "filesystem/easy/file_context/file_splitting"
    variants = [s.variant_id for s in poison.expand(config, "r", task, MODEL_120B)]
    variants += [s.variant_id for s in inject.expand(config, "r", task, MODEL_120B)]
    assert variants == ["poison-holdout-hp", "inject-holdout-hi"]


def test_holdout_config_runs_nothing_while_the_folder_is_empty():
    _, specs = specs_of("holdout")
    holdout_files = list((REPO / "payloads" / "holdout").glob("*.yaml"))
    if not holdout_files:
        assert specs == []
