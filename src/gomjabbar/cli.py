"""Command line interface: doctor, pilot, estimate, run and report (SPEC 11).

What: the `gomjabbar` command, built with Typer: doctor, paraphrase, pilot, estimate, run,
report, compare.
Why: every experiment step is one command, so runs are reproducible from the shell history
and the cloud session can run them in the background with nohup.
How: each command loads a config, builds the pieces (task loader, run store, quota manager,
model clients) and hands them to the runner or analysis modules. `compare` (M4) pairs two
runs episode by episode and writes compare.html.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import typer

from gomjabbar.analysis.estimate import estimate as estimate_run
from gomjabbar.analysis.estimate import wall_hours
from gomjabbar.analysis.summary import summary_text
from gomjabbar.config import RunConfig, load_config
from gomjabbar.models import Condition
from gomjabbar.paths import MCPMARK_ROOT, REPO_ROOT
from gomjabbar.runner.checkpoint import git_commit_id
from gomjabbar.runner.environment import environment_for
from gomjabbar.runner.episode import RunInfo
from gomjabbar.runner.grid import RunStatus, build_specs, run_grid
from gomjabbar.runner.quota import QuotaManager
from gomjabbar.runner.store import RunStore
from gomjabbar.tasks.mcpmark import MCPMarkTasks, read_suite

app = typer.Typer(add_completion=False, help="Gom Jabbar: chaos testing for tool-using AI agents.")

RUNS_DIR = REPO_ROOT / "runs"


def _quiet_litellm() -> None:
    """Keep LiteLLM's banners and debug hints out of the terminal and run logs."""
    os.environ.setdefault("LITELLM_LOG", "ERROR")
    import litellm

    litellm.suppress_debug_info = True


def _new_run_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"


def _require_preregistration(config: RunConfig) -> None:
    """Never run the full suite before PRE_REGISTRATION.md is FINAL (CLAUDE.md hard rule)."""
    if config.run_name == "full" or Path(config.suite).name == "full.txt":
        first = (REPO_ROOT / "PRE_REGISTRATION.md").read_text(encoding="utf-8").splitlines()[0]
        if first.strip() != "Status: FINAL":
            typer.echo("Refusing: PRE_REGISTRATION.md is not 'Status: FINAL'.", err=True)
            raise typer.Exit(2)


def _key_guard(config: RunConfig):
    """The key spend guard for OpenRouter models, or None if the config sets no key cap."""
    from gomjabbar.runner.budget import KeySpendGuard, openrouter_key_usage_usd

    if config.key_spend_cap_usd is None:
        return None
    envs = {m.api_key_env for m in config.models if m.name.startswith("openrouter/")}
    if config.redteam_model and config.redteam_model.name.startswith("openrouter/"):
        envs.add(config.redteam_model.api_key_env)
    envs.discard(None)
    if len(envs) != 1:
        raise typer.BadParameter("key_spend_cap_usd needs exactly one OpenRouter key variable")
    key = os.environ.get(envs.pop(), "")
    return KeySpendGuard(config.key_spend_cap_usd, lambda: openrouter_key_usage_usd(key))


def _task_ids(config: RunConfig, limit: int | None = None) -> list[str]:
    """Task ids from the suite file, limited to the config's services."""
    services = {s.value for s in config.services}
    ids = [i for i in read_suite(REPO_ROOT / config.suite) if i.split("/")[0] in services]
    return ids[:limit] if limit else ids


def _execute(
    config: RunConfig,
    run_id: str,
    task_ids: list[str],
    only: Condition | None,
    checkpoint_every: int | None,
) -> RunStatus:
    """Shared body of `run` and `pilot`."""
    _quiet_litellm()
    from gomjabbar.agent.llm import LiteLLMChat

    specs = build_specs(config, run_id, task_ids, only)
    if config.payload_set == "holdout" and not specs:
        # The held-out payloads are written only after the pattern freeze; none yet.
        # Checked before the run folder exists, so a refused run leaves nothing behind.
        typer.echo("Refusing: payloads/holdout/ has no payloads for this run.", err=True)
        raise typer.Exit(2)
    store = RunStore(RUNS_DIR / run_id)
    store.write_config(config)
    loader = MCPMarkTasks()
    tasks = {task_id: loader.load(task_id) for task_id in task_ids}
    if Condition.PARAPHRASE in ([only] if only else config.conditions):
        # Paraphrases are made once by `gomjabbar paraphrase`, never during a run.
        from gomjabbar.redteam.paraphrase import ParaphraseCacheMissing, load_paraphrases

        try:
            for task in tasks.values():
                load_paraphrases(task)
        except ParaphraseCacheMissing as exc:
            typer.echo(f"Refusing: {exc}", err=True)
            raise typer.Exit(2) from exc
    info = RunInfo(config=config, config_hash=config.config_hash(), git_commit=git_commit_id())
    quota = QuotaManager(store, config.models)
    clients = {
        m.name: LiteLLMChat(
            m, config.temperature, quota.gate(m.name), config.usd_to_eur, config.max_tokens
        )
        for m in config.models
    }
    key_guard = _key_guard(config)
    typer.echo(f"run {run_id}: {len(specs)} episodes, results in {store.run_dir}")
    status = asyncio.run(
        run_grid(
            specs,
            tasks,
            store,
            info,
            llm_for=lambda model: clients[model],
            environment_for=environment_for,
            quota=quota,
            checkpoint_every=checkpoint_every,
            on_result=typer.echo,
            key_guard=key_guard,
        )
    )
    typer.echo("")
    typer.echo(summary_text(store.read_results(), config.k, config.seed))
    if status.paused_models and not status.finished:
        all_paused = quota.all_exhausted([m.name for m in config.models])
        if status.account_problem:
            # Missing credit or a rejected (expired, invalid) key: nothing was scored.
            typer.echo(
                f"\npaused: the provider rejected the account ({status.account_problem[:200]}). "
                f"Fix the key or credit, then resume with --resume {run_id}"
            )
        elif all_paused:
            typer.echo(
                f"\nquota exhausted: resume after the provider's daily reset with --resume {run_id}"
            )
        else:
            typer.echo(f"\npaused models {sorted(status.paused_models)}; --resume {run_id} later")
    elif status.key_spend_stop:
        typer.echo(
            f"\nkey spend limit: stopped before the OpenRouter key could pass USD "
            f"{config.key_spend_cap_usd:g} ({status.key_spend_stop}); partial results kept"
        )
    elif status.budget_stop:
        typer.echo(f"\nspend cap reached; partial results in {store.run_dir}")
    else:
        typer.echo(f"\nfinished: {status.done}/{status.total} episodes")
    return status


@app.command()
def pilot(
    config: Path = typer.Option(..., help="config YAML"),
    tasks: int = typer.Option(3, help="first N tasks of the suite"),
    resume: str | None = typer.Option(None, help="pilot RUN_ID to continue"),
) -> None:
    """Baseline only, k=1, on the first N tasks: a smoke test that measures cost per episode."""
    cfg = load_config(config)
    _require_preregistration(cfg)
    cfg = cfg.model_copy(update={"k": 1, "conditions": [Condition.BASELINE]})
    run_id = resume or _new_run_id(f"pilot-{cfg.run_name}")
    _execute(cfg, run_id, _task_ids(cfg, tasks), None, None)


@app.command()
def run(
    config: Path = typer.Option(..., help="config YAML"),
    resume: str | None = typer.Option(None, help="RUN_ID to continue"),
    dry_run: bool = typer.Option(False, "--dry-run", help="print episode counts and exit"),
    checkpoint_every: int | None = typer.Option(
        None, help="git commit and push every N episodes (default 25 in cloud sessions)"
    ),
    only: Condition | None = typer.Option(None, help="run a single condition"),
) -> None:
    """Run the grid from a config, resumably."""
    cfg = load_config(config)
    _require_preregistration(cfg)
    task_ids = _task_ids(cfg)
    run_id = resume or _new_run_id(cfg.run_name)
    if dry_run:
        from collections import Counter

        specs = build_specs(cfg, run_id, task_ids, only)
        counts = Counter((s.model, s.condition.value) for s in specs)
        for (model, condition), n in sorted(counts.items()):
            typer.echo(f"{model:35s} {condition:14s} {n}")
        typer.echo(f"total {len(specs)} episodes")
        return
    if checkpoint_every is None and os.environ.get("CLAUDE_CODE_REMOTE") == "true":
        checkpoint_every = 25
    _execute(cfg, run_id, task_ids, only, checkpoint_every)


@app.command()
def paraphrase(
    config: Path = typer.Option(..., help="config YAML (its redteam_model writes the texts)"),
    samples: int = typer.Option(5, help="paraphrases to put in the review page"),
) -> None:
    """Generate and cache paraphrases for every suite task that has none yet (SPEC 5.6)."""
    from gomjabbar.redteam.paraphrase import (
        cache_path,
        generate_for_task,
        samples_markdown,
    )

    cfg = load_config(config)
    _require_preregistration(cfg)
    if cfg.redteam_model is None:
        typer.echo("the config has no redteam_model", err=True)
        raise typer.Exit(2)
    _quiet_litellm()
    from gomjabbar.agent.llm import LiteLLMChat

    model = LiteLLMChat(cfg.redteam_model, cfg.temperature, None, cfg.usd_to_eur, cfg.max_tokens)
    key_guard = _key_guard(cfg)
    loader = MCPMarkTasks()
    tasks = [loader.load(task_id) for task_id in _task_ids(cfg)]
    # Up to 5 tasks at a time; each task's variants are made one after another.
    parallel = 5
    totals = {"eur": 0.0}

    async def make(task, gate: asyncio.Semaphore) -> None:
        async with gate:
            # The key's absolute limit: reserve 0.05 USD for each task that may be running.
            if key_guard is not None and not key_guard.can_start(0.05, in_flight=parallel):
                typer.echo(
                    f"skipped  {task.id}: key spend limit ({key_guard.error or key_guard.last_usage})"
                )
                return
            log = await generate_for_task(task, model, cfg.redteam_model.name, cfg.paraphrases)
            totals["eur"] += log.cost_eur
            typer.echo(
                f"made     {task.id}: {len(log.accepted)}/{cfg.paraphrases} accepted, "
                f"{len(log.rejected)} rejected, {len(log.dropped)} dropped, EUR {log.cost_eur:.4f}"
            )

    async def make_all() -> None:
        gate = asyncio.Semaphore(parallel)
        todo = []
        for task in tasks:
            if cache_path(task.id).exists():
                typer.echo(f"cached   {task.id}")
            else:
                todo.append(make(task, gate))
        await asyncio.gather(*todo)

    asyncio.run(make_all())
    total_eur = totals["eur"]
    page = REPO_ROOT / "docs" / "notes" / "paraphrase-samples.md"
    page.write_text(samples_markdown(tasks, samples), encoding="utf-8")
    typer.echo(f"done: EUR {total_eur:.4f} this time; review page {page}")


@app.command()
def estimate(
    pilot: Path = typer.Option(..., help="pilot run folder, runs/<id>"),
    config: Path = typer.Option(..., help="config YAML of the planned run"),
    only: Condition | None = typer.Option(None, help="only this condition"),
) -> None:
    """Project episodes, requests, tokens, euros and days for a planned run from a pilot."""
    cfg = load_config(config)
    specs = build_specs(cfg, "estimate", _task_ids(cfg), only)
    pilot_store = RunStore(pilot)
    pilot_results = pilot_store.read_results()
    # The pilot's own euro rate, so its costs can be converted to the planned run's rate.
    pilot_rate = load_config(pilot_store.config_path).usd_to_eur
    typer.echo(f"planned: {len(specs)} episodes ({only.value if only else 'all conditions'})")
    estimates = estimate_run(cfg, specs, pilot_results, pilot_rate)
    for e in estimates:
        typer.echo(f"== {e.model}")
        source = (
            f"no pilot episode of its own: borrowed the average of {e.pilot_episodes} others"
            if e.borrowed
            else f"pilot average over {e.pilot_episodes}"
        )
        typer.echo(f"   episodes          {e.episodes}  ({source})")
        typer.echo(
            f"   per episode       {e.requests_per_episode:.1f} requests, "
            f"{e.tokens_per_episode:,.0f} tokens, {e.seconds_per_episode:.0f}s"
        )
        usd = f" (USD {e.cost_usd:.2f})" if e.cost_usd is not None else ""
        typer.echo(
            f"   total             {e.total_requests:,} requests, {e.total_tokens:,} tokens, "
            f"EUR {e.cost_eur:.2f}{usd}"
        )
        typer.echo(f"   episode hours     {e.hours_serial:.1f} (one at a time)")
        # Daily limits only exist for quota-limited (free tier) models.
        if e.days_by_requests is not None:
            typer.echo(f"   days at rpd       {e.days_by_requests}")
        if e.days_by_tokens is not None:
            typer.echo(f"   days at tpd       {e.days_by_tokens}")
        if e.hours_by_tpm is not None:
            typer.echo(f"   min. hours at tpm {e.hours_by_tpm:.1f}")
    # Run-level totals: what Saad approves.
    total_eur = sum(e.cost_eur for e in estimates)
    typer.echo("== run")
    typer.echo(f"   cost              EUR {total_eur:.2f} (spend cap EUR {cfg.spend_cap_eur:g})")
    typer.echo(
        f"   wall time         {wall_hours(cfg, estimates):.1f} h at concurrency {cfg.concurrency}"
    )
    if cfg.spend_cap_eur > 0 and total_eur > cfg.spend_cap_eur:
        typer.echo("   WARNING: the estimate is above the spend cap; the run would stop early")


@app.command()
def report(
    run_dir: Path = typer.Argument(..., help="runs/<run_id>"),
    text: bool = typer.Option(False, "--text", help="print the card only, no HTML"),
) -> None:
    """Write RUN_DIR/report.html and print the report card (SPEC 12)."""
    from gomjabbar.report.card import build_card, card_text
    from gomjabbar.report.html import write_report

    store = RunStore(run_dir)
    cfg = load_config(store.config_path)
    results = store.read_results()
    # Baseline diagnostics (parse failures, empty replies, providers), then the full card.
    typer.echo(summary_text(results, cfg.k, cfg.seed))
    typer.echo("")
    typer.echo(card_text(build_card(results, cfg.k, cfg.seed)))
    if not text:
        typer.echo(f"\nwrote {write_report(run_dir)}")


@app.command()
def compare(
    run_a: Path = typer.Argument(..., help="runs/<run_id> of the reference run (A)"),
    run_b: Path = typer.Argument(..., help="runs/<run_id> of the changed run (B)"),
) -> None:
    """Pair two runs episode by episode, print McNemar per condition, write RUN_B/compare.html."""
    from gomjabbar.analysis.compare import compare_runs, comparison_text
    from gomjabbar.report.compare_html import write_compare

    store_a, store_b = RunStore(run_a), RunStore(run_b)
    # The bootstrap seed comes from run B's config, so the same pair of runs always gives
    # the same intervals.
    seed = load_config(store_b.config_path).seed
    comparison = compare_runs(
        store_a.read_results(), store_b.read_results(), seed, run_a.name, run_b.name
    )
    if not comparison.rows:
        typer.echo(
            "no paired episodes: the runs share no (model, task, condition, variant, attempt)"
        )
        raise typer.Exit(1)
    typer.echo(comparison_text(comparison))
    typer.echo(f"\nwrote {write_compare(comparison, run_b / 'compare.html')}")


def _check(ok: bool, label: str, detail: str = "", warn: bool = False) -> bool:
    mark = "ok  " if ok else ("warn" if warn else "FAIL")
    typer.echo(f"[{mark}] {label}" + (f": {detail}" if detail else ""))
    return ok or warn


def _version(command: list[str]) -> str:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


@app.command()
def doctor(
    config: Path = typer.Option(REPO_ROOT / "configs" / "dev.yaml", help="config YAML"),
) -> None:
    """Check the environment: tools, Postgres, keys, one test call per model, MCPMark."""
    all_ok = True
    remote = os.environ.get("CLAUDE_CODE_REMOTE") == "true"
    typer.echo(f"cloud session: {'yes' if remote else 'no'}")
    all_ok &= _check(sys.version_info >= (3, 11), "python >= 3.11", sys.version.split()[0])
    all_ok &= _check(shutil.which("uv") is not None, "uv", _version(["uv", "--version"]))
    node = _version(["node", "--version"])
    all_ok &= _check(node.startswith("v") and int(node[1:].split(".")[0]) >= 20, "node >= 20", node)
    all_ok &= _check(shutil.which("npx") is not None, "npx")
    # Postgres: MCPMark's PostgreSQL 17 image in Docker (the sandbox starts the container).
    from gomjabbar.sandbox import postgres as pg_sandbox

    try:
        pg_sandbox.ensure_container()
        _check(True, "postgres (docker)", f"{pg_sandbox.IMAGE} on port {pg_sandbox.PG_PORT}")
    except pg_sandbox.PostgresUnavailable as exc:
        all_ok &= _check(False, "postgres (docker)", str(exc))
    commit_file = REPO_ROOT / "vendor" / "MCPMARK_COMMIT"
    all_ok &= _check(
        (MCPMARK_ROOT / "tasks").is_dir() and commit_file.exists(),
        "vendored MCPMark",
        commit_file.read_text().strip()[:12] if commit_file.exists() else "missing",
    )
    try:
        cfg = load_config(config)
    except Exception as exc:  # noqa: BLE001 - report any config problem
        _check(False, f"config {config}", str(exc))
        raise typer.Exit(1) from exc
    _check(True, f"config {config.name}", "every model is free or priced")
    if any(m.name.startswith("ollama/") for m in cfg.models):
        all_ok &= _check(shutil.which("ollama") is not None, "ollama (config uses an Ollama model)")
    _quiet_litellm()
    from gomjabbar.agent.llm import LiteLLMChat, LLMError

    models = [(m, False) for m in cfg.models]
    if cfg.redteam_model:
        # The redteam model is used from M2 on, so a failure there is only a warning for now.
        models.append((cfg.redteam_model, True))
    for model, optional in models:
        label = f"model {model.name}" + (" (redteam, used from M2)" if optional else "")
        if model.api_key_env and not os.environ.get(model.api_key_env):
            all_ok &= _check(False, label, f"{model.api_key_env} is not set", warn=optional)
            continue
        try:
            reply = asyncio.run(
                LiteLLMChat(model).complete([{"role": "user", "content": "Reply with OK."}], [])
            )
            # Show the serving provider too, so a pinned provider that did not hold is visible.
            served = f", provider {reply.provider}" if reply.provider else ""
            _check(True, label, f"test call ok, version {reply.model_version}{served}")
        except LLMError as exc:
            all_ok &= _check(False, label, str(exc)[:200], warn=optional)
    if not all_ok:
        raise typer.Exit(1)
    typer.echo("doctor: all required checks passed")


if __name__ == "__main__":
    app()
