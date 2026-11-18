"""Command-line interface. Thin: parses arguments, calls HocApp, formats output."""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

from hoc.core.changes import ChangeSetStatus
from hoc.core.common import Actor
from hoc.core.errors import HocError
from hoc.llm import usage_summary
from hoc.services import HocApp

app = typer.Typer(no_args_is_help=True, help="hoc-ai: graph-based learning system prototype")
graph_app = typer.Typer(no_args_is_help=True, help="Inspect the knowledge graph")
cs_app = typer.Typer(no_args_is_help=True, help="Manage ChangeSets")
snap_app = typer.Typer(no_args_is_help=True, help="Graph snapshots")
llm_app = typer.Typer(no_args_is_help=True, help="LLM usage")
demo_app = typer.Typer(no_args_is_help=True, help="Example data")
learn_app = typer.Typer(no_args_is_help=True, help="Learning-side data")
app.add_typer(graph_app, name="graph")
app.add_typer(cs_app, name="changeset")
app.add_typer(snap_app, name="snapshot")
app.add_typer(llm_app, name="llm")
app.add_typer(demo_app, name="demo")
app.add_typer(learn_app, name="learn")

ConfigOpt = Annotated[
    Path | None,
    typer.Option("--config", envvar="HOC_CONFIG", help="YAML overriding config/default.yaml"),
]
DbOpt = Annotated[
    Path | None, typer.Option("--db", envvar="HOC_DB", help="SQLite file (default from config)")
]
EXAMPLE_SEED = Path(__file__).resolve().parents[2] / "data" / "examples" / "probability_seed.json"


def _open(config: Path | None, db: Path | None) -> HocApp:
    return HocApp.open(config, db)


def _run[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Decorator turning expected domain errors into clean CLI errors."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except HocError as e:
            typer.secho(f"error: {e}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from e

    return wrapper


@app.command()
@_run
def init(config: ConfigOpt = None, db: DbOpt = None) -> None:
    """Create the database (idempotent)."""
    a = _open(config, db)
    typer.echo(f"database ready: {a.db.path}")


@graph_app.command("show")
@_run
def graph_show(config: ConfigOpt = None, db: DbOpt = None) -> None:
    """List concepts, relations and misconceptions."""
    g = _open(config, db).graph()
    typer.secho(f"Concepts ({len(g.concepts())})", bold=True)
    for c in g.concepts():
        lock = " [locked]" if c.locked else ""
        typer.echo(f"  {c.id:<32} {c.kind.value:<9} {c.status.value}{lock}")
    typer.secho(f"Relations ({len(g.relations())})", bold=True)
    for r in g.relations():
        nec = f" ({r.necessity.value})" if r.necessity else ""
        typer.echo(f"  {r.source} --{r.type.value}{nec}--> {r.target}  [{r.status.value}]")
    typer.secho(f"Misconceptions ({len(g.misconceptions())})", bold=True)
    for m in g.misconceptions():
        typer.echo(f"  {m.id}: {m.statement}")


@cs_app.command("list")
@_run
def cs_list(
    status: Annotated[ChangeSetStatus | None, typer.Option()] = None,
    config: ConfigOpt = None,
    db: DbOpt = None,
) -> None:
    for cs in _open(config, db).changesets.find(status):
        typer.echo(
            f"{cs.id}  {cs.status.value:<12} {cs.author.name:<14} "
            f"{len(cs.ops)} ops  {cs.rationale[:60]}"
        )


@cs_app.command("show")
@_run
def cs_show(changeset_id: str, config: ConfigOpt = None, db: DbOpt = None) -> None:
    cs = _open(config, db).changesets.get(changeset_id)
    typer.echo(cs.model_dump_json(indent=2))


@cs_app.command("propose")
@_run
def cs_propose(
    file: Path,
    apply: Annotated[bool, typer.Option("--apply", help="auto-apply immediately")] = False,
    config: ConfigOpt = None,
    db: DbOpt = None,
) -> None:
    """Store a ChangeSet from a JSON file; optionally auto-apply it."""
    a = _open(config, db)
    cs = a.changesets.propose(a.parse_changeset(file.read_text()))
    if apply:
        cs = a.changesets.apply(cs.id)
    typer.echo(f"{cs.id} {cs.status.value}")


def _expert(name: str) -> Actor:
    return Actor(kind="human", name=name)


@cs_app.command("apply")
@_run
def cs_apply(changeset_id: str, config: ConfigOpt = None, db: DbOpt = None) -> None:
    cs = _open(config, db).changesets.apply(changeset_id)
    typer.echo(f"{cs.id} {cs.status.value}")


@cs_app.command("approve")
@_run
def cs_approve(
    changeset_id: str,
    by: Annotated[str, typer.Option(help="expert name")] = "expert",
    config: ConfigOpt = None,
    db: DbOpt = None,
) -> None:
    cs = _open(config, db).changesets.approve(changeset_id, _expert(by))
    typer.echo(f"{cs.id} {cs.status.value}")


@cs_app.command("reject")
@_run
def cs_reject(
    changeset_id: str,
    by: Annotated[str, typer.Option(help="expert name")] = "expert",
    config: ConfigOpt = None,
    db: DbOpt = None,
) -> None:
    cs = _open(config, db).changesets.reject(changeset_id, _expert(by))
    typer.echo(f"{cs.id} {cs.status.value}")


@cs_app.command("revert")
@_run
def cs_revert(changeset_id: str, config: ConfigOpt = None, db: DbOpt = None) -> None:
    cs = _open(config, db).changesets.revert(changeset_id)
    typer.echo(f"{cs.id} {cs.status.value}")


@snap_app.command("create")
@_run
def snap_create(label: str, config: ConfigOpt = None, db: DbOpt = None) -> None:
    info = _open(config, db).snapshots.create(label)
    typer.echo(f"{info.id} (after changeset #{info.applied_seq})")


@snap_app.command("list")
@_run
def snap_list(config: ConfigOpt = None, db: DbOpt = None) -> None:
    for s in _open(config, db).snapshots.list():
        typer.echo(f"{s.id}  {s.label:<20} #{s.applied_seq}  {s.created_at}")


@llm_app.command("usage")
@_run
def llm_usage(config: ConfigOpt = None, db: DbOpt = None) -> None:
    """Per-step LLM calls, cache hits, tokens and cost."""
    rows = usage_summary(_open(config, db).db)
    if not rows:
        typer.echo("no LLM calls recorded")
    for r in rows:
        typer.echo(
            f"{r['step']:<24} calls={r['calls']:<4} cached={r['cache_hits']:<4} "
            f"tokens={r['in']}/{r['out']} cost=${r['cost_usd']:.4f}"
        )


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    config: ConfigOpt = None,
) -> None:
    """Start the graph studio (web UI + API) at http://HOST:PORT."""
    import uvicorn

    from hoc.config import load_settings
    from hoc.web.server import create_app

    typer.echo(f"graph studio: http://{host}:{port}")
    uvicorn.run(create_app(load_settings(config)), host=host, port=port, log_level="warning")


@learn_app.command("export")
@_run
def learn_export(
    learner: Annotated[str, typer.Option(help="Learner profile id")] = "me",
    config: ConfigOpt = None,
    db: DbOpt = None,
) -> None:
    """Print everything recorded about a learner as JSON lines (turns, evidence, decisions)."""
    import json

    from hoc.services import LearningSpace

    space = LearningSpace(_open(config, db))
    for row in space.export_rows(learner):
        typer.echo(json.dumps(row, ensure_ascii=False))


@demo_app.command("seed")
@_run
def demo_seed(config: ConfigOpt = None, db: DbOpt = None) -> None:
    """Load the example probability graph as an expert-approved ChangeSet."""
    a = _open(config, db)
    cs = a.changesets.propose(a.parse_changeset(EXAMPLE_SEED.read_text()))
    cs = a.changesets.approve(cs.id, _expert("demo"))
    typer.echo(f"seeded with {cs.id} ({len(cs.ops)} ops)")


if __name__ == "__main__":  # pragma: no cover
    app()
