"""Paraphrases of task descriptions: generated once, checked twice, cached for every model.

What: `generate_for_task` asks the redteam model for P rewordings of a task's description.
Each candidate must pass the literal check (redteam/literals.py) and an LLM equivalence
check; a failed candidate is regenerated up to 3 times, then that variant is dropped. The
accepted texts, every rejection and every drop go into cache/paraphrases/<task_id>.json.
Why: the paraphrase condition (SPEC 6.2) asks whether success depends on wording. That is
only a fair question if the reworded task demands exactly the same end state, and if every
model sees exactly the same rewording, so paraphrases are made once and committed.
How: `pruefstand paraphrase --config X` calls `generate_for_task` for every suite task that
has no cache file yet. The paraphrase condition reads the cache with `load_paraphrases`; it
never generates text itself.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pruefstand.agent.llm import ChatModel
from pruefstand.models import Task
from pruefstand.paths import CACHE_ROOT
from pruefstand.redteam.literals import extract_literals, missing_literals
from pruefstand.tasks.mcpmark import PROMPT_SUFFIX

PARAPHRASE_CACHE = CACHE_ROOT / "paraphrases"
# Candidates per variant before it is dropped (SPEC 5.6: max 3 tries).
MAX_TRIES = 3
# Version of the prompts below; part of the cache file so a change is visible.
PROMPT_VERSION = "v2"  # v2 lists the required literals in the prompt (2026-10-03)

# One style hint per variant, so the paraphrases differ from each other as well as from the
# original. Variant i uses hint (i - 1) modulo the list length.
STYLE_HINTS = [
    "Write it as plain prose paragraphs, without Markdown headings or emoji.",
    "Keep a list structure, but reorder the information where the meaning allows it and "
    "use different headings.",
    "Make it shorter and more direct, as a colleague would write it in a message.",
]

GENERATE_PROMPT = """Rewrite the task description below in different words and with a different sentence structure. The rewritten task must require exactly the same work and the same end result.

Rules:
- Keep every requirement and constraint. Do not add, drop, soften or strengthen any.
- Copy exactly, character for character: file names, paths, numbers, quoted text, code in backticks, table names, column names and other identifiers. Never write a number as a word: write 5, not five.
- Copy Markdown tables of data unchanged.
- Each of these must appear in your text exactly as written here (numbers as digits): {literals}
- Do not solve the task or add hints.
- {style}

Reply with the rewritten description only.

Task description:
<<<
{text}
>>>"""

EQUIVALENCE_PROMPT = """Two task descriptions, A and B, are given to an agent that works on files or a database.

Question: is every fully correct solution of A also a fully correct solution of B, and every fully correct solution of B also a fully correct solution of A? Consider required names, values, formats, ordering, and anything that must or must not be changed.

Reply with JSON only, in this form: {{"equivalent": true or false, "differences": ["...", "..."]}}

A:
<<<
{a}
>>>

B:
<<<
{b}
>>>"""


def base_description(task: Task) -> str:
    """The description without MCPMark's fixed suffix: only this part is reworded."""
    suffix = PROMPT_SUFFIX[task.service]
    if not task.description.endswith(suffix):
        raise ValueError(f"{task.id}: description does not end with MCPMark's suffix")
    return task.description[: -len(suffix)]


def cache_path(task_id: str, root: Path | None = None) -> Path:
    """cache/paraphrases/<service>/<suite>/<category>/<task>.json

    `root` defaults to PARAPHRASE_CACHE, looked up at call time so tests can redirect it.
    """
    return (root or PARAPHRASE_CACHE) / f"{task_id}.json"


def source_hash(text: str) -> str:
    """sha1 of the original description, stored so a changed task invalidates its cache."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def parse_equivalence(text: str) -> dict:
    """The JSON verdict from the model's reply. Raises ValueError if there is none."""
    # Models sometimes wrap JSON in prose or code fences: take the first {...} block.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object in the reply")
    verdict = json.loads(match.group(0))
    if not isinstance(verdict.get("equivalent"), bool):
        raise ValueError("'equivalent' is not a boolean")
    differences = verdict.get("differences") or []
    return {"equivalent": verdict["equivalent"], "differences": [str(d) for d in differences]}


def strip_wrapping(text: str) -> str:
    """Remove the <<< >>> markers or a code fence the model may copy into its answer."""
    text = text.strip()
    text = re.sub(r"^<<<\s*|\s*>>>$", "", text)
    text = re.sub(r"^```[a-z]*\n|\n```$", "", text)
    return text.strip()


@dataclass
class GenerationLog:
    """Everything that happened while making the paraphrases of one task."""

    accepted: list[dict] = field(default_factory=list)  # {variant_id, text, tries, differences}
    rejected: list[dict] = field(default_factory=list)  # {variant_id, try, check, reason}
    dropped: list[dict] = field(default_factory=list)  # {variant_id, reasons}
    cost_eur: float = 0.0
    requests: int = 0


async def _ask(model: ChatModel, prompt: str, log: GenerationLog) -> str:
    """One plain completion (no tools); its cost and requests are added to the log."""
    reply = await model.complete([{"role": "user", "content": prompt}], [])
    log.cost_eur += reply.cost_eur
    log.requests += reply.attempts
    return reply.content


async def generate_variant(
    model: ChatModel, original: str, variant: int, log: GenerationLog
) -> dict | None:
    """Make one checked paraphrase, or None after MAX_TRIES failed candidates."""
    variant_id = f"para-{variant}"
    style = STYLE_HINTS[(variant - 1) % len(STYLE_HINTS)]
    # The literals the check will demand, spelled out so the model knows exactly what to keep.
    literals = ", ".join(lit.text for lit in extract_literals(original)) or "(none)"
    reasons = []
    for attempt in range(1, MAX_TRIES + 1):
        candidate = strip_wrapping(
            await _ask(
                model, GENERATE_PROMPT.format(style=style, text=original, literals=literals), log
            )
        )
        # Check 1, deterministic: every literal survived.
        missing = missing_literals(original, candidate) if candidate else []
        if not candidate or missing:
            reason = (
                "empty reply"
                if not candidate
                else "missing literals: " + ", ".join(repr(m.text) for m in missing[:10])
            )
            log.rejected.append(
                {"variant_id": variant_id, "try": attempt, "check": "literal", "reason": reason}
            )
            reasons.append(reason)
            continue
        # Check 2, LLM: both directions of "a correct solution of one solves the other".
        raw = await _ask(model, EQUIVALENCE_PROMPT.format(a=original, b=candidate), log)
        try:
            verdict = parse_equivalence(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            reason = f"unreadable equivalence verdict: {exc}"
            log.rejected.append(
                {"variant_id": variant_id, "try": attempt, "check": "equivalence", "reason": reason}
            )
            reasons.append(reason)
            continue
        if not verdict["equivalent"]:
            reason = "not equivalent: " + "; ".join(verdict["differences"])[:500]
            log.rejected.append(
                {"variant_id": variant_id, "try": attempt, "check": "equivalence", "reason": reason}
            )
            reasons.append(reason)
            continue
        return {
            "variant_id": variant_id,
            "text": candidate,
            "tries": attempt,
            "differences": verdict["differences"],
        }
    log.dropped.append({"variant_id": variant_id, "reasons": reasons})
    return None


async def generate_for_task(
    task: Task, model: ChatModel, model_name: str, count: int, root: Path | None = None
) -> GenerationLog:
    """Generate `count` paraphrases for a task and write its cache file."""
    original = base_description(task)
    log = GenerationLog()
    for variant in range(1, count + 1):
        accepted = await generate_variant(model, original, variant, log)
        if accepted is not None:
            log.accepted.append(accepted)
    path = cache_path(task.id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "task_id": task.id,
        "source_sha1": source_hash(original),
        "generator": model_name,
        "prompt_version": PROMPT_VERSION,
        "created": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "requested": count,
        "variants": log.accepted,
        "rejected": log.rejected,
        "dropped": log.dropped,
        "cost_eur": round(log.cost_eur, 6),
        "requests": log.requests,
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return log


class ParaphraseCacheMissing(Exception):
    """A paraphrase episode needs a cache file that does not exist (or is stale)."""


def load_paraphrases(task: Task, root: Path | None = None) -> dict[str, str]:
    """{variant_id: reworded description} from the cache. Raises if missing or stale."""
    path = cache_path(task.id, root)
    if not path.exists():
        raise ParaphraseCacheMissing(
            f"no paraphrases for {task.id}; run `pruefstand paraphrase --config ...` first"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    # A changed task description makes old paraphrases meaningless.
    if data["source_sha1"] != source_hash(base_description(task)):
        raise ParaphraseCacheMissing(f"paraphrases for {task.id} were made from another text")
    return {v["variant_id"]: v["text"] for v in data["variants"]}


def paraphrased_prompt(task: Task, variant_id: str, root: Path | None = None) -> str:
    """The prompt for a paraphrase episode: the cached rewording plus MCPMark's suffix."""
    texts = load_paraphrases(task, root)
    if variant_id not in texts:
        raise ParaphraseCacheMissing(f"{task.id} has no paraphrase {variant_id}")
    return texts[variant_id] + PROMPT_SUFFIX[task.service]


def samples_markdown(tasks: list[Task], count: int, root: Path | None = None) -> str:
    """A review page: `count` paraphrases next to their originals (M2 step 4).

    Picks the first accepted variant of tasks in suite order, alternating services where
    possible, so Saad sees both filesystem and postgres examples.
    """
    by_service: dict[str, list[Task]] = {}
    for task in tasks:
        by_service.setdefault(task.service.value, []).append(task)
    # Round robin over services, in suite order within each.
    order: list[Task] = []
    queues = list(by_service.values())
    for i in range(max((len(q) for q in queues), default=0)):
        order.extend(q[i] for q in queues if i < len(q))
    lines = [
        "# Paraphrase samples for review",
        "",
        f"{count} paraphrases next to their originals, for Saad to check (M2 step 4). "
        "Generated by `pruefstand paraphrase`; every one passed the literal check and the "
        "equivalence check. The full set, with every rejected and dropped candidate, is in "
        "`cache/paraphrases/`. MCPMark's fixed suffix is appended to both texts in real "
        "episodes and is left out here.",
        "",
    ]
    shown = 0
    for task in order:
        if shown == count:
            break
        path = cache_path(task.id, root)
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data["variants"]:
            continue
        variant = data["variants"][0]
        shown += 1
        rejected = len([r for r in data["rejected"] if r["variant_id"] == variant["variant_id"]])
        lines += [
            f"## {shown}. `{task.id}` ({variant['variant_id']})",
            "",
            f"Accepted on try {variant['tries']} ({rejected} rejected before it).",
            "",
            "**Original**",
            "",
            *["> " + line if line else ">" for line in base_description(task).strip().splitlines()],
            "",
            "**Paraphrase**",
            "",
            *["> " + line if line else ">" for line in variant["text"].strip().splitlines()],
            "",
        ]
    return "\n".join(lines) + "\n"
