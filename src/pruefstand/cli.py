"""Command line interface: doctor, pilot, estimate, run and report (SPEC 11).

What: the `pruefstand` command, built with Typer.
Why: every experiment step is one command, so runs are reproducible from the shell history
and the cloud session can run them in the background with nohup.
How: each command loads a config, builds the pieces (task loader, run store, quota manager,
model clients) and hands them to the runner or analysis modules. The HTML report and the
paired `compare` arrive in M3.
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

from pruefstand.analysis.estimate import estimate as estimate_run
from pruefstand.analysis.summary import summary_text
from pruefstand.config import RunConfig, load_config
from pruefstand.models import Condition
from pruefstand.paths import MCPMARK_ROOT, REPO_ROOT
from pruefstand.runner.checkpoint import git_commit_id
from pruefstand.runner.environment import environment_for
from pruefstand.runner.episode import RunInfo
from pruefstand.runner.grid import RunStatus, build_specs, run_grid
from pruefstand.runner.quota import QuotaManager
from pruefstand.runner.store import RunStore
from pruefstand.tasks.mcpmark import MCPMarkTasks, read_suite

app = typer.Typer(add_completion=False, help="Prüfstand: chaos testing for tool-using AI agents.")

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
    from pruefstand.agent.llm import LiteLLMChat

    store = RunStore(RUNS_DIR / run_id)
    store.write_config(config)
    loader = MCPMarkTasks()
    tasks = {task_id: loader.load(task_id) for task_id in task_ids}
    specs = build_specs(config, run_id, task_ids, only)
    info = RunInfo(config=config, config_hash=config.config_hash(), git_commit=git_commit_id())
    quota = QuotaManager(store, config.models)
    clients = {
        m.name: LiteLLMChat(
            m, config.temperature, quota.gate(m.name), config.usd_to_eur, config.max_tokens
        )
        for m in config.models
    }
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
        )
    )
    typer.echo("")
    typer.echo(summary_text(store.read_results(), config.k, config.seed))
    if status.paused_models and not status.finished:
        all_paused = quota.all_exhausted([m.name for m in config.models])
        if all_paused:
            typer.echo(
                f"\nquota exhausted: resume after the provider's daily reset with --resume {run_id}"
            )
        else:
            typer.echo(f"\npaused models {sorted(status.paused_models)}; --resume {run_id} later")
    elif status.budget_stop:
        typer.echo(f"\nspend cap reached; partial results in {store.run_dir}")
    else:
        typer.echo(f"\nfinished: {status.done}/{status.total} episodes")
    return status


@app.command()
def pilot(
    config: Path = typer.Option(..., help="config YAML"),
    tasks: int = typer.Option(3, help="first N tasks of the suite"),
) -> None:
    """Baseline only, k=1, on the first N tasks: a smoke test that measures cost per episode."""
    cfg = load_config(config)
    _require_preregistration(cfg)
    cfg = cfg.model_copy(update={"k": 1, "conditions": [Condition.BASELINE]})
    _execute(cfg, _new_run_id(f"pilot-{cfg.run_name}"), _task_ids(cfg, tasks), None, None)


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
def estimate(
    pilot: Path = typer.Option(..., help="pilot run folder, runs/<id>"),
    config: Path = typer.Option(..., help="config YAML of the planned run"),
    only: Condition | None = typer.Option(None, help="only this condition"),
) -> None:
    """Project episodes, requests, tokens, euros and days for a planned run from a pilot."""
    cfg = load_config(config)
    specs = build_specs(cfg, "estimate", _task_ids(cfg), only)
    pilot_results = RunStore(pilot).read_results()
    typer.echo(f"planned: {len(specs)} episodes ({only.value if only else 'all conditions'})")
    for e in estimate_run(cfg, specs, pilot_results):
        typer.echo(f"== {e.model}")
        typer.echo(f"   episodes          {e.episodes}  (pilot average over {e.pilot_episodes})")
        typer.echo(
            f"   per episode       {e.requests_per_episode:.1f} requests, "
            f"{e.tokens_per_episode:,.0f} tokens, {e.seconds_per_episode:.0f}s"
        )
        typer.echo(
            f"   total             {e.total_requests:,} requests, {e.total_tokens:,} tokens, "
            f"EUR {e.cost_eur:.2f}"
        )
        if e.days_by_requests is not None:
            typer.echo(f"   days at rpd       {e.days_by_requests}")
        typer.echo(
            "   days at tpd       "
            + (
                str(e.days_by_tokens)
                if e.days_by_tokens is not None
                else "unknown (tpd_limit not set)"
            )
        )
        if e.hours_by_tpm is not None:
            typer.echo(f"   min. hours at tpm {e.hours_by_tpm:.1f}")


@app.command()
def report(
    run_dir: Path = typer.Argument(..., help="runs/<run_id>"),
    text: bool = typer.Option(True, "--text/--html", help="terminal card (HTML arrives in M3)"),
) -> None:
    """Print the report card of a run."""
    if not text:
        typer.echo("The HTML report arrives in M3.", err=True)
        raise typer.Exit(2)
    store = RunStore(run_dir)
    cfg = load_config(store.config_path)
    typer.echo(summary_text(store.read_results(), cfg.k, cfg.seed))


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
    # Postgres: the native service first, Docker as fallback (needed from M2).
    pg = (
        subprocess.run(["pg_isready"], capture_output=True, text=True)
        if shutil.which("pg_isready")
        else None
    )
    if pg is not None and pg.returncode == 0:
        _check(True, "postgres (native)", pg.stdout.strip())
    elif shutil.which("docker"):
        _check(
            True, "postgres", "native service not running; docker available as fallback", warn=True
        )
    else:
        all_ok &= _check(False, "postgres", "neither a running native service nor docker")
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
    from pruefstand.agent.llm import LiteLLMChat, LLMError

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
            _check(True, label, f"test call ok, version {reply.model_version}")
        except LLMError as exc:
            all_ok &= _check(False, label, str(exc)[:200], warn=optional)
    if not all_ok:
        raise typer.Exit(1)
    typer.echo("doctor: all required checks passed")


if __name__ == "__main__":
    app()
