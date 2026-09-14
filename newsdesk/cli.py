"""Newsdesk command line.

    newsdesk add-source <url>     register a feed (http(s):// or a local path)
    newsdesk list-sources
    newsdesk collect              run one collection pass
    newsdesk search <query>
    newsdesk item <item_id>       full canonical record as JSON
    newsdesk log                  tail the immutable activity log
    newsdesk serve                run the local HTTP API + agent tools
"""

from __future__ import annotations

import json
from typing import Optional

import typer

from .config import Settings
from .pipeline.runner import run_collection
from .storage.db import Database
from .storage.repo import ItemRepo, LogRepo, SourceRepo

app = typer.Typer(help="Newsdesk — watchlist-driven news collection agent",
                  no_args_is_help=True, add_completion=False)


def _settings() -> Settings:
    settings = Settings.from_env()
    settings.ensure_dirs()
    return settings


@app.command("add-source")
def add_source(
    url: str = typer.Argument(..., help="Feed URL, file:// URI, or local file path"),
    kind: str = typer.Option("rss", help="Source kind: rss|html|youtube|api|forum|newsletter|manual"),
    title: Optional[str] = typer.Option(None, help="Human label"),
    publisher: Optional[str] = typer.Option(None, help="Publisher name"),
) -> None:
    settings = _settings()
    db = Database(settings)
    with db.session() as session:
        source, created = SourceRepo(session).add(url, kind=kind, title=title,
                                                  publisher=publisher)
        if created:
            LogRepo(session).append("source_added", {"source_id": source.id, "url": url,
                                                     "kind": kind}, actor="user")
        source_id, source_kind = source.id, source.kind
    state = "added" if created else "already registered"
    typer.echo(f"[{source_id}] {url} ({state}, kind={source_kind})")


@app.command("list-sources")
def list_sources() -> None:
    db = Database(_settings())
    with db.session() as session:
        rows = SourceRepo(session).list()
    if not rows:
        typer.echo("No sources registered. Add one with: newsdesk add-source <url>")
        return
    typer.echo(f"{'ID':>4}  {'KIND':<10} {'ON':<3} {'LAST STATUS':<22} URL")
    for r in rows:
        on = "yes" if r.enabled else "no"
        typer.echo(f"{r.id:>4}  {r.kind:<10} {on:<3} {(r.last_status or '-'):<22} {r.url}")


@app.command("collect")
def collect(
    source_id: Optional[int] = typer.Option(None, help="Collect only this source id"),
) -> None:
    settings = _settings()
    db = Database(settings)
    with db.session() as session:
        job = run_collection(session, settings, [source_id] if source_id else None)
        job_id, job_status, job_stats = job.id, job.status, job.stats
    for sid, stats in job_stats.get("sources", {}).items():
        if stats.get("error"):
            typer.echo(f"source {sid}: ERROR {stats['error']}")
        else:
            typer.echo(
                f"source {sid}: +{stats['created']} new, "
                f"~{stats['updated']} revised, ={stats['unchanged']} unchanged, "
                f"{stats['duplicate']} dup"
                + (" (not modified)" if stats.get("not_modified") else "")
                + (" (skipped)" if stats.get("skipped") else "")
            )
    typer.echo(f"job {job_id}: {job_status} {job_stats.get('totals', {})}")


@app.command("search")
def search(
    query: str = typer.Argument(..., help="Full-text query"),
    limit: int = typer.Option(10, help="Max results"),
) -> None:
    db = Database(_settings())
    with db.session() as session:
        rows = ItemRepo(session).search(query, limit=limit)
    if not rows:
        typer.echo("No matches.")
        return
    for r in rows:
        when = (r.published_at or r.retrieved_at).strftime("%Y-%m-%d")
        dup = " [dup]" if r.duplicate_of else ""
        typer.echo(f"{when}  {r.publisher or '-':<24} {r.title[:80]}{dup}")
        typer.echo(f"{'':>10}  id={r.id}  {r.url}")


@app.command("item")
def item(item_id: str = typer.Argument(..., help="Item id, e.g. item_abc123")) -> None:
    db = Database(_settings())
    with db.session() as session:
        row = ItemRepo(session).get(item_id)
    if not row:
        typer.echo(f"Not found: {item_id}")
        raise typer.Exit(code=1)
    typer.echo(json.dumps(row.to_canonical(), indent=2, ensure_ascii=False))


@app.command("log")
def log(limit: int = typer.Option(20, help="Number of entries")) -> None:
    db = Database(_settings())
    with db.session() as session:
        rows = LogRepo(session).recent(limit=limit)
    for e in rows:
        typer.echo(f"#{e.id} {e.ts.isoformat()} [{e.actor}] {e.action} {json.dumps(e.detail, ensure_ascii=False)}")


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8000),
) -> None:
    import uvicorn

    typer.echo(f"Serving Newsdesk API on http://{host}:{port} (docs at /docs)")
    uvicorn.run("newsdesk.api.app:app", host=host, port=port, log_level="info")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
