"""Tests for the paraphrase literal check, generation with both checks, and the cache."""

import asyncio
import json
from pathlib import Path

import pytest

from gomjabbar.conditions import expand, prompt_for
from gomjabbar.models import Condition, Service, Task
from gomjabbar.redteam import paraphrase
from gomjabbar.redteam.literals import extract_literals, missing_literals
from gomjabbar.tasks.mcpmark import PROMPT_SUFFIX
from tests.fixtures.scripted_llm import ScriptedLLM, final
from tests.helpers import make_spec

# ---- literal check -----------------------------------------------------------------------------


def kinds(text: str) -> dict[str, str]:
    return {lit.text: lit.kind for lit in extract_literals(text)}


def test_paths_and_file_names():
    original = "Move docs/2024/report.md and large_file.txt into `archive/` next to notes.csv."
    found = kinds(original)
    assert found["docs/2024/report.md"] == "path"
    assert found["notes.csv"] == "file"
    assert found["archive/"] == "code"
    good = "Put notes.csv, large_file.txt and docs/2024/report.md into `archive/`."
    assert missing_literals(original, good) == []
    bad = "Put the CSV notes, large_file.txt and docs/2024/report.md into `archive/`."
    assert [m.text for m in missing_literals(original, bad)] == ["notes.csv"]


def test_numbers_must_stay_whole_numbers():
    original = "Split it into exactly 3 files, differing by at most 100 characters (2.5%)."
    assert {"3", "100", "2.5"} <= set(kinds(original))
    assert missing_literals(original, "Make 3 files; at most 100 chars apart (2.5%).") == []
    # "three" is not 3, and "30" or "1000" do not contain the numbers 3 or 100.
    lost = missing_literals(original, "Make three files; 30 or 1000 chars apart (2.5%).")
    assert {m.text for m in lost} == {"3", "100"}


def test_list_numbering_is_not_a_literal():
    original = "1. Read the file\n2. Write `out.txt`\n"
    assert "1" not in kinds(original) and "2" not in kinds(original)
    # ... and list numbering in a paraphrase cannot stand in for a number of the task.
    lost = missing_literals("Split into 3 files.", "1. Split\n2. into\n3. three files")
    assert [m.text for m in lost] == ["3"]


def test_quoted_strings_double_single_and_curly():
    original = "Set status to 'archived' and the title to \"Q3 Report\", note “do not edit”."
    found = kinds(original)
    assert {"archived", "Q3 Report", "do not edit"} <= set(found)
    # An apostrophe is not a quote.
    assert "s manager" not in "".join(found)
    lost = missing_literals(original, "Use status 'Archived', title \"Q3 Report\", “do not edit”.")
    assert [m.text for m in lost] == ["archived"]  # case matters


def test_sql_identifiers_and_code_spans():
    original = (
        "Create view `exec_summary` joining dept_emp and salaries where to_date = '9999-01-01'; "
        "set CustomerId and FirstName."
    )
    found = kinds(original)
    assert found["exec_summary"] == "code"
    assert found["dept_emp"] == "identifier" and found["to_date"] == "identifier"
    assert found["CustomerId"] == "identifier" and found["FirstName"] == "identifier"
    assert found["9999-01-01"] == "quoted"
    lost = missing_literals(
        original,
        "Make view `exec_summary` over dept_emp and salaries, to_date = '9999-01-01'; "
        "set customer id and FirstName.",
    )
    assert [m.text for m in lost] == ["CustomerId"]


def test_table_cells_must_survive():
    original = "| Name | City |\n|------|------|\n| Danielle | East William |\n"
    found = kinds(original)
    assert found["Danielle"] == "table_cell" and found["East William"] == "table_cell"
    assert "------" not in found
    assert [m.text for m in missing_literals(original, "Danielle from East Williamsburg")] == [
        "Name",
        "City",
    ]


# ---- generation and cache ----------------------------------------------------------------------


ORIGINAL = "Rename the largest `.jpg` file to `largest.jpg`. Keep 2 copies."


def make_task(task_id="filesystem/easy/cat/rename") -> Task:
    return Task(
        id=task_id,
        service=Service.FILESYSTEM,
        description=ORIGINAL + PROMPT_SUFFIX[Service.FILESYSTEM],
        source_dir=Path("."),
        meta={},
    )


GOOD_1 = "Find the biggest `.jpg` and call it `largest.jpg`; keep 2 copies."
GOOD_2 = "Keep 2 copies. The `.jpg` with the most bytes becomes `largest.jpg`."
EQUIVALENT = '{"equivalent": true, "differences": []}'


def test_generation_checks_both_and_writes_the_cache(tmp_path):
    llm = ScriptedLLM(
        [
            final("Rename the biggest picture to `largest.jpg`. Keep two copies."),  # literals lost
            final(GOOD_1),
            final('Sure: {"equivalent": false, "differences": ["copies"]}'),  # rejected
            final(GOOD_1),
            final(EQUIVALENT),  # para-1 accepted on try 3
            final(GOOD_2),
            final("```json\n" + EQUIVALENT + "\n```"),  # para-2 accepted on try 1
        ]
    )
    log = asyncio.run(paraphrase.generate_for_task(make_task(), llm, "redteam/model", 2, tmp_path))
    data = json.loads(paraphrase.cache_path("filesystem/easy/cat/rename", tmp_path).read_text())
    assert [(v["variant_id"], v["text"], v["tries"]) for v in data["variants"]] == [
        ("para-1", GOOD_1, 3),
        ("para-2", GOOD_2, 1),
    ]
    # Every rejection is logged with the check that failed.
    assert [(r["variant_id"], r["try"], r["check"]) for r in data["rejected"]] == [
        ("para-1", 1, "literal"),
        ("para-1", 2, "equivalence"),
    ]
    # "two" instead of 2 was caught (".jpg" survived inside "largest.jpg").
    assert data["rejected"][0]["reason"] == "missing literals: '2'"
    assert data["dropped"] == []
    assert data["generator"] == "redteam/model"
    assert log.requests == 7


def test_variant_is_dropped_after_three_failed_tries(tmp_path):
    llm = ScriptedLLM([final("no literals here")] * 3)
    asyncio.run(paraphrase.generate_for_task(make_task(), llm, "m", 1, tmp_path))
    data = json.loads(paraphrase.cache_path("filesystem/easy/cat/rename", tmp_path).read_text())
    assert data["variants"] == []
    assert data["dropped"][0]["variant_id"] == "para-1"
    assert len(data["dropped"][0]["reasons"]) == 3


@pytest.fixture
def cached(tmp_path, monkeypatch):
    """A cache with para-1 and para-2 for make_task(), at a temporary location."""
    monkeypatch.setattr(paraphrase, "PARAPHRASE_CACHE", tmp_path)
    llm = ScriptedLLM([final(GOOD_1), final(EQUIVALENT), final(GOOD_2), final(EQUIVALENT)])
    asyncio.run(paraphrase.generate_for_task(make_task(), llm, "m", 2))
    return tmp_path


def test_every_model_sees_identical_paraphrases(cached):
    from gomjabbar.config import RunConfig

    config = RunConfig.model_validate(
        dict(
            run_name="t",
            suite="x",
            services=["filesystem"],
            models=[{"name": "a", "free_tier": True}, {"name": "b", "free_tier": True}],
            seed=1,
            paraphrases=3,
            conditions=["paraphrase"],
        )
    )
    task = make_task()
    specs_a = expand(Condition.PARAPHRASE, config, "r", task.id, "a")
    specs_b = expand(Condition.PARAPHRASE, config, "r", task.id, "b")
    # Only the cached variants become episodes (3 requested, 2 in the cache).
    assert [s.variant_id for s in specs_a] == ["para-1", "para-2"]
    prompts_a = [prompt_for(s, task) for s in specs_a]
    prompts_b = [prompt_for(s, task) for s in specs_b]
    assert prompts_a == prompts_b
    assert prompts_a[0] == GOOD_1 + PROMPT_SUFFIX[Service.FILESYSTEM]
    # Reading the cache never calls a model: the prompts come from the file only.
    file = paraphrase.cache_path(task.id)
    before = file.read_bytes()
    assert prompt_for(specs_a[1], task) == prompt_for(specs_b[1], task)
    assert file.read_bytes() == before


def test_missing_or_stale_cache_refuses(cached):
    task = make_task()
    spec = make_spec(condition=Condition.PARAPHRASE, variant_id="para-1", task_id=task.id)
    other = make_task("filesystem/easy/cat/other")
    with pytest.raises(paraphrase.ParaphraseCacheMissing):
        prompt_for(make_spec(condition=Condition.PARAPHRASE, variant_id="para-1"), other)
    changed = task.model_copy(
        update={"description": "Changed." + PROMPT_SUFFIX[Service.FILESYSTEM]}
    )
    with pytest.raises(paraphrase.ParaphraseCacheMissing, match="another text"):
        prompt_for(spec, changed)


def test_fault_condition_expands_one_episode_per_profile():
    from gomjabbar.conditions import plan_for
    from gomjabbar.config import RunConfig

    config = RunConfig.model_validate(
        dict(
            run_name="t",
            suite="x",
            services=["filesystem"],
            models=[{"name": "a", "free_tier": True}],
            seed=1,
            fault_profiles=["timeout", "empty"],
            conditions=["fault"],
        )
    )
    specs = expand(Condition.FAULT, config, "r", "t1", "a")
    assert [s.variant_id for s in specs] == ["fault-timeout", "fault-empty"]
    rule = plan_for(specs[0]).faults[0]
    assert (rule.profile, rule.tool, rule.nth_call) == ("timeout", "*", 2)
    # Faults do not change the prompt.
    task = make_task()
    assert prompt_for(specs[0], task) == task.description
