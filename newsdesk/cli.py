"""Newsdesk command line.

    newsdesk add-source <url>     register a feed (http(s):// or a local path)
    newsdesk list-sources
    newsdesk collect              run one collection pass
    newsdesk search <query>
    newsdesk item <item_id>       full canonical record as JSON
    newsdesk summarize <item_id>  grounded summary (extractive without an LLM)
    newsdesk digest <item_id>     watch a video: transcript + vision + summary
    newsdesk digest-daily         the day's watchlist digest (--deep adds
                                  LLM deep dives)
    newsdesk script               stage the spoken-briefing script (+ sidecar)
    newsdesk audio                synthesize the staged script into the day's MP3
    newsdesk morning              the whole morning: collect through notify
                                  (--station / NEWSDESK_STATION publishes one
                                  station's feed instead of the legacy run)
    newsdesk workflow ...         the versioned workflow catalog (create,
                                  get, list, history, diff, retire, unretire)
    newsdesk log                  tail the immutable activity log
    newsdesk serve                run the local HTTP API + agent tools
    newsdesk mcp                  run the MCP server (stdio) for agent harnesses
    newsdesk accounts ...         link/inspect personal accounts (email, X, YouTube)
    newsdesk seed export|import   sources, watchlists, workflows as a committed JSON seed
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer

from .config import Settings
from .core.models import SOURCE_KINDS
from .pipeline.runner import run_collection
from .pipeline.summarize import digest_item, summarize_item
from .storage.db import Database
from .storage.repo import ItemRepo, LogRepo, SourceRepo

app = typer.Typer(help="Newsdesk — watchlist-driven news collection agent",
                  no_args_is_help=True, add_completion=False)

accounts_app = typer.Typer(help="Manage linked accounts (credentials via env/"
                                "keyring; consent grants stored locally)",
                           no_args_is_help=True)
app.add_typer(accounts_app, name="accounts")

seed_app = typer.Typer(help="Export/import sources + watchlists as a committed "
                            "JSON seed (bridges fresh databases, e.g. CI)",
                       no_args_is_help=True)
app.add_typer(seed_app, name="seed")

workflow_app = typer.Typer(help="The versioned workflow catalog: create, "
                                "inspect, diff, retire (every mutation "
                                "actor-tagged in the log)",
                           no_args_is_help=True)
app.add_typer(workflow_app, name="workflow")


def _mmss(seconds: float) -> str:
    total = round(seconds)
    return f"{total // 60}:{total % 60:02d}"


def _settings() -> Settings:
    settings = Settings.from_env()
    settings.ensure_dirs()
    return settings


@app.command("add-source")
def add_source(
    url: str = typer.Argument(..., help="Feed URL, file:// URI, or local file path"),
    kind: str = typer.Option("rss", help=f"Source kind: {'|'.join(SOURCE_KINDS)}"),
    title: Optional[str] = typer.Option(None, help="Human label"),
    publisher: Optional[str] = typer.Option(None, help="Publisher name"),
) -> None:
    settings = _settings()
    db = Database(settings)
    try:
        with db.session() as session:
            source, created = SourceRepo(session).add(url, kind=kind, title=title,
                                                      publisher=publisher)
            if created:
                LogRepo(session).append("source_added", {"source_id": source.id, "url": url,
                                                         "kind": kind}, actor="user")
            source_id, source_kind = source.id, source.kind
    except ValueError as exc:  # unknown kind: rejected at add time
        typer.echo(f"error: {exc}")
        raise typer.Exit(code=2)
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


@app.command("summarize")
def summarize(
    item_id: str = typer.Argument(..., help="Item id to summarize"),
    force: bool = typer.Option(False, help="Re-summarize even if a summary exists"),
) -> None:
    settings = _settings()
    db = Database(settings)
    with db.session() as session:
        summary = summarize_item(session, settings, item_id, force=force)
    typer.echo(json.dumps(summary, indent=2, ensure_ascii=False))


@app.command("digest")
def digest(
    item_id: str = typer.Argument(..., help="Video item id to watch and digest"),
    force: bool = typer.Option(False, help="Redo transcript/vision/summary"),
    no_vision: bool = typer.Option(False, help="Skip keyframe extraction + vision"),
) -> None:
    settings = _settings()
    db = Database(settings)
    with db.session() as session:
        report = digest_item(session, settings, item_id, force=force,
                             with_vision=not no_vision)
    typer.echo(json.dumps(report, indent=2, ensure_ascii=False))


@app.command("log")
def log(limit: int = typer.Option(20, help="Number of entries")) -> None:
    db = Database(_settings())
    with db.session() as session:
        rows = LogRepo(session).recent(limit=limit)
    for e in rows:
        typer.echo(f"#{e.id} {e.ts.isoformat()} [{e.actor}] {e.action} {json.dumps(e.detail, ensure_ascii=False)}")


@app.command("digest-daily")
def digest_daily(
    hours: int = typer.Option(24, help="How far back to look"),
    limit: int = typer.Option(24, help="Max items considered"),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON briefing"),
    deep: int = typer.Option(0, help="Also inline per-item summaries for the top N items"),
) -> None:
    """The daily briefing: what is worth following, with drill-down ids."""
    settings = _settings()
    db = Database(settings)
    with db.session() as session:
        from .pipeline.digest import build_daily_digest, render_markdown
        digest = build_daily_digest(session, settings, hours=hours, limit=limit)
        if deep > 0:
            from .pipeline.summarize import summarize_item
            for section in digest.get("worth_following", [])[:3]:
                for item_id in section.get("item_ids", [])[:deep]:
                    summary = summarize_item(session, settings, item_id)
                    claims = summary.get("what_happened") or []
                    section.setdefault("deep_dives", []).append({
                        "item_id": item_id,
                        "headline": summary.get("headline"),
                        "claims": claims[:4],
                    })
            digest["deep"] = True
    if json_out:
        typer.echo(json.dumps(digest, indent=2, ensure_ascii=False))
    else:
        typer.echo(render_markdown(digest))
        if digest.get("deep"):
            for section in digest.get("worth_following", []):
                for dive in section.get("deep_dives", []):
                    typer.echo(f"\n--- deep dive: {dive['headline']} "
                               f"({dive['item_id']}) ---")
                    for claim in dive["claims"]:
                        typer.echo(f"  • {claim}")


@app.command("script")
def write_script(
    hours: int = typer.Option(24, help="How far back the digest window looks"),
    json_out: bool = typer.Option(False, "--json", help="Emit the script as JSON"),
) -> None:
    """Stage the spoken-briefing script: digest -> script-writer -> sidecar."""
    settings = _settings()
    db = Database(settings)
    with db.session() as session:
        from .morning.orchestrator import stage_script
        from .morning.registries import load_plugins
        from .morning.script import episode_date
        from .pipeline.digest import build_daily_digest

        load_plugins()
        digest = build_daily_digest(session, settings, hours=hours)
        brief = stage_script(session, settings, digest, episode_date())
    if json_out:
        typer.echo(json.dumps(brief, indent=2, ensure_ascii=False))
    else:
        stats = brief["stats"]
        typer.echo(f"{brief['date']} script ({brief['method']}): "
                   f"{len(brief['sections'])} sections, {stats['words']} words, "
                   f"~{_mmss(stats['est_seconds'])}")
        for s in brief["sections"]:
            ids = ",".join(s["item_ids"]) or "-"
            typer.echo(f"  {s['type']:<10} {s['est_seconds']:>4}s  {ids}")
        typer.echo(f"sidecar: {brief['sidecar']}")


@app.command("audio")
def render_audio(
    date: Optional[str] = typer.Option(None, help="Episode date (default: today in HKT)"),
    json_out: bool = typer.Option(False, "--json", help="Emit the render report as JSON"),
) -> None:
    """Synthesize the staged script into the day's MP3 (edge-tts engine)."""
    from .morning.edgetts import TTSError
    from .morning.registries import TTS_ENGINES, load_plugins
    from .morning.script import episode_date, sidecar_path

    load_plugins()
    settings = _settings()
    d = date or episode_date()
    script_path = sidecar_path(settings, d)
    db = Database(settings)
    try:
        with db.session() as session:
            report = TTS_ENGINES.get()(settings, script_path)
            LogRepo(session).append("morning_audio_rendered", {
                "date": report["date"], "engine": report["engine"],
                "duration_seconds": report["duration_seconds"],
                "chunks": report["chunks"], "mp3": report["mp3"]})
    except TTSError as exc:
        typer.echo(f"error: {exc}")
        raise typer.Exit(code=1)
    if json_out:
        typer.echo(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        typer.echo(f"{report['date']} audio: {report['chunks']} chunks, "
                   f"{_mmss(report['duration_seconds'])} "
                   f"(voice {', '.join(report['voices'])})")
        typer.echo(f"mp3: {report['mp3']}")


@app.command("morning")
def morning(
    json_out: bool = typer.Option(False, "--json", help="Emit the run report as JSON"),
    date: Optional[str] = typer.Option(
        None, "--date",
        help="Backfill: episode date YYYY-MM-DD (morning tz) instead of today"),
    station: Optional[str] = typer.Option(
        None, "--station",
        help="Publish as this station (its scope, workflow, feed identity "
             "and path); default: NEWSDESK_STATION, else the station-less "
             "legacy run"),
    archive_dir: Optional[Path] = typer.Option(
        None, "--archive",
        help="After a successful publish, archive the episode bundle "
             "(script + version-aware meta) under this directory. A "
             "fresh-runner re-run has no sidecar to archive."),
) -> None:
    """The whole morning: collect -> digest -> script -> tts -> publish -> notify."""
    from .workflow.catalog import (CatalogError, WorkflowCatalog,
                                   ensure_default_catalog)
    from .workflow.engine import WorkflowRunError, run_workflow
    from .workflow.stations import StationRepo

    if date is not None:
        try:
            date = datetime.strptime(date, "%Y-%m-%d").date().isoformat()
        except ValueError:
            typer.echo("error: --date must be YYYY-MM-DD")
            raise typer.Exit(code=1)

    settings = _settings()
    station = station or settings.station

    def mask(text: str) -> str:
        # the token-bearing URLs are the feed's only auth; the CLI report
        # becomes the CI run log, so the token never survives printing
        return text.replace(settings.feed_token, "***") if settings.feed_token else text

    db = Database(settings)
    with db.session() as session:
        try:
            # W2: the morning runs the catalog's default workflow — the
            # shipped file bootstraps the catalog once, then the DB row
            # (floating default-morning@latest) is the living data. A
            # station run (W4) runs the station's own bound workflow ref;
            # the engine re-resolves the station row at pre-flight.
            ensure_default_catalog(session)
            if station:
                ref = StationRepo(session).resolve(station)["workflow_ref"]
            else:
                ref = "default-morning"
            descriptor = WorkflowCatalog(session).resolve(ref)
            report = run_workflow(session, settings, descriptor,
                                  station, date=date)
        except (WorkflowRunError, CatalogError) as exc:
            typer.echo(f"error: {exc}")
            raise typer.Exit(code=1)
    # ticket 04: the run that publishes also archives the episode bundle —
    # the sidecar is this runner's only copy, so the archive happens here
    # or never (an already_published re-run is a fresh runner: skip)
    if archive_dir is not None and report["outcome"] == "published":
        from .morning.archive import ArchiveError, write_bundle

        try:
            report["archive"] = write_bundle(
                settings, report,
                workflow_name=descriptor["name"],
                workflow_version=descriptor["version"],
                archive_dir=archive_dir, station=station)
        except ArchiveError as exc:
            typer.echo(f"error: {exc}")
            raise typer.Exit(code=1)
    if json_out:
        typer.echo(mask(json.dumps(report, indent=2, ensure_ascii=False)))
        return
    stages = report["stages"]
    if report["outcome"] == "already_published":
        typer.echo(f"morning {report['date']}: already published — nothing to do")
        return
    if not {"collect", "digest", "script", "tts", "publish", "notify"} \
            <= set(stages):
        # a station may bind a workflow with renamed stages: the machine
        # report stays the honest surface
        typer.echo(mask(json.dumps(report, indent=2, ensure_ascii=False)))
        return
    prefix = f"morning {report['date']}" + (f" [{station}]" if station else "")
    typer.echo(f"{prefix}: published")
    typer.echo(f"  collect   {stages['collect']['sources']} sources, "
               f"{stages['collect']['status']}")
    typer.echo(f"  digest    {stages['digest']['method']}, "
               f"{stages['digest']['items_in_window']} items in window "
               f"(verdicts: {stages['digest']['verdict_method']})")
    typer.echo(f"  script    {stages['script']['method']}, "
               f"{stages['script']['words']} words, "
               f"~{_mmss(stages['script']['est_seconds'])}")
    typer.echo(f"  tts       {stages['tts']['chunks']} chunks, "
               f"{_mmss(stages['tts']['duration_seconds'])}")
    typer.echo(f"  publish   {stages['publish']['publisher']}")
    typer.echo(mask(f"    episode: {stages['publish']['episode_url']}"))
    typer.echo(mask(f"    feed:    {stages['publish']['feed_url']}"))
    typer.echo(f"  notify    {stages['notify']['outcome']} "
               f"({', '.join(stages['notify']['notifiers']) or 'none registered'})")
    if report.get("archive"):
        typer.echo(f"  archive   {report['archive']['path']}")


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8000),
) -> None:
    import uvicorn

    typer.echo(f"Serving Newsdesk API on http://{host}:{port} (docs at /docs)")
    uvicorn.run("newsdesk.api.app:app", host=host, port=port, log_level="info")


@app.command("mcp")
def mcp() -> None:
    """Run the MCP server over stdio (for agent harness integration)."""
    from .mcp_server import main as mcp_main

    mcp_main()


# -- seed -----------------------------------------------------------------


@seed_app.command("export")
def seed_export(
    out: Path = typer.Argument(Path("seed/newsdesk-seed.json"),
                               help="Output JSON path"),
) -> None:
    """Write every source, watchlist, workflow, and station to a JSON
    file, ready to commit."""
    from .seed import export_seed

    db = Database(_settings())
    with db.session() as session:
        data = export_seed(session)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    terms = sum(len(w["terms"]) for w in data["watchlists"])
    typer.echo(f"{out}: {len(data['sources'])} sources, {terms} terms "
               f"across {len(data['watchlists'])} watchlists, "
               f"{len(data['workflows'])} workflow versions, "
               f"{len(data['stations'])} stations")


@seed_app.command("import")
def seed_import(
    path: Path = typer.Argument(..., help="Seed JSON path"),
) -> None:
    """Seed the database from a JSON file (idempotent; never deletes)."""
    from .seed import SeedError, import_seed

    if not path.exists():
        typer.echo(f"error: no such seed file: {path}")
        raise typer.Exit(code=1)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        typer.echo(f"error: {path} is not valid JSON: {exc}")
        raise typer.Exit(code=1)
    db = Database(_settings())
    with db.session() as session:
        try:
            stats = import_seed(session, data, actor="user")
        except SeedError as exc:
            typer.echo(f"error: {exc}")
            raise typer.Exit(code=1)
    typer.echo(f"{path}: +{stats['sources_added']} sources "
               f"({stats['sources_present']} present), "
               f"+{stats['terms_added']} terms "
               f"({stats['terms_present']} present), "
               f"{stats['watchlists_created']} watchlists created, "
               f"+{stats['links_added']} links "
               f"({stats['links_present']} present), "
               f"+{stats['workflows_added']} workflow versions "
               f"({stats['workflows_present']} present), "
               f"+{stats['stations_added']} stations "
               f"(+{stats['stations_updated']} updated, "
               f"{stats['stations_present']} present)")


# -- workflow catalog -----------------------------------------------------


@contextmanager
def _workflow_session():
    """One catalog verb's boilerplate: a session with the shipped default
    bootstrapped, ``CatalogError`` rendered as the CLI error exit."""
    from .workflow.catalog import CatalogError, WorkflowCatalog, \
        ensure_default_catalog

    db = Database(_settings())
    with db.session() as session:
        ensure_default_catalog(session)
        try:
            yield WorkflowCatalog(session)
        except CatalogError as exc:
            typer.echo(f"error: {exc}")
            raise typer.Exit(code=1)


@workflow_app.command("create")
def workflow_create(
    path: Path = typer.Argument(..., help="Descriptor JSON file"),
) -> None:
    """Append a new workflow version from a descriptor JSON file.

    The document self-describes its name@version; the version must be
    exactly max+1 for its name (dense, never reused). Logged as actor
    ``user`` — the CLI is the user's surface.
    """
    if not path.exists():
        typer.echo(f"error: no such descriptor file: {path}")
        raise typer.Exit(code=1)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        typer.echo(f"error: {path} is not valid JSON: {exc}")
        raise typer.Exit(code=1)
    with _workflow_session() as catalog:
        version = catalog.create_version(doc, actor="user", via="cli")
    typer.echo(f"created {doc['name']}@{version}")


@workflow_app.command("get")
def workflow_get(
    ref: str = typer.Argument(..., help="Workflow ref: name, name@N, or name@latest"),
) -> None:
    """Print one stored descriptor document (works on retired names)."""
    with _workflow_session() as catalog:
        doc = catalog.document(ref)
    typer.echo(json.dumps(doc, indent=2, ensure_ascii=False))


@workflow_app.command("list")
def workflow_list() -> None:
    """List every workflow name with its latest version and retirement."""
    with _workflow_session() as catalog:
        rows = catalog.list()
    if not rows:  # unreachable while the shipped default bootstraps
        typer.echo("No workflows in the catalog.")
        return
    typer.echo(f"{'NAME':<24} {'LATEST':>6}  STATE")
    for r in rows:
        state = "retired" if r["retired_at"] else "active"
        typer.echo(f"{r['name']:<24} {r['latest']:>6}  {state}")


@workflow_app.command("history")
def workflow_history(
    name: str = typer.Argument(..., help="Workflow name"),
) -> None:
    """Show a workflow's full version history (the append-only truth)."""
    with _workflow_session() as catalog:
        history = catalog.versions(name)
    for v in history:
        title = v["document"].get("title") or "-"
        typer.echo(f"v{v['version']:<3} by {v['created_by']:<6} "
                   f"{v['created_at']}  {title}")


@workflow_app.command("diff")
def workflow_diff(
    name: str = typer.Argument(..., help="Workflow name"),
    version_a: int = typer.Argument(..., help="Base version"),
    version_b: int = typer.Argument(..., help="Compared version"),
) -> None:
    """Structural diff of two versions (RFC 6901 paths, keyed by stage)."""
    with _workflow_session() as catalog:
        changes = catalog.diff(name, version_a, version_b)
    if not changes:
        typer.echo(f"{name} v{version_a} -> v{version_b}: no differences")
        return
    typer.echo(json.dumps(changes, indent=2, ensure_ascii=False))


@workflow_app.command("retire")
def workflow_retire(
    name: str = typer.Argument(..., help="Workflow name"),
) -> None:
    """Retire a workflow name: new versions and runs are refused (loudly),
    history stays. Logged as actor ``user``."""
    with _workflow_session() as catalog:
        catalog.retire(name, actor="user", via="cli")
    typer.echo(f"retired {name}")


@workflow_app.command("unretire")
def workflow_unretire(
    name: str = typer.Argument(..., help="Workflow name"),
) -> None:
    """Undo a retirement (the data was never gone). Logged as actor
    ``user``."""
    with _workflow_session() as catalog:
        catalog.unretire(name, actor="user", via="cli")
    typer.echo(f"unretired {name}")


# -- accounts -------------------------------------------------------------


@accounts_app.command("list")
def accounts_list() -> None:
    """Show grants + credential presence (values are never printed)."""
    from .accounts.manager import AccountManager

    rows = AccountManager(_settings()).status()
    typer.echo(f"{'ACCOUNT':<24} {'CAPABILITIES':<28} CREDENTIALS")
    for row in rows:
        caps = ",".join(row["capabilities"]) or "-"
        cred = row.get("credentials") or {}
        cred_state = "all set" if cred and all(cred.values()) else (
            "missing: " + ",".join(k for k, v in cred.items() if not v) if cred else "-")
        note = f"  ({row['note']})" if row.get("note") else ""
        typer.echo(f"{row['account']:<24} {caps:<28} {cred_state}{note}")


@accounts_app.command("grant")
def accounts_grant(
    kind: str = typer.Argument(..., help="email | twitter | youtube-account"),
    ref: str = typer.Argument("default", help="Account label"),
    capability: list[str] = typer.Option(..., "--cap",
                                         help="Capability to grant (repeatable)"),
) -> None:
    """Link an account by granting capabilities (consent record)."""
    from .accounts.manager import AccountManager

    entry = AccountManager(_settings()).grant(kind, ref, capability)
    typer.echo(f"granted {kind}/{ref}: {', '.join(entry['capabilities'])}")


@accounts_app.command("revoke")
def accounts_revoke(
    kind: str = typer.Argument(...),
    ref: str = typer.Argument("default"),
) -> None:
    from .accounts.manager import AccountManager

    ok = AccountManager(_settings()).revoke(kind, ref)
    typer.echo(f"revoked {kind}/{ref}" if ok else f"no grant for {kind}/{ref}")
    if not ok:
        raise typer.Exit(code=1)


@accounts_app.command("test")
def accounts_test(
    kind: str = typer.Argument(..., help="email | twitter | youtube-account"),
    ref: str = typer.Argument("default"),
) -> None:
    """Verify the grant + credentials connect (read-only, one request)."""
    from .accounts import email as email_provider
    from .accounts import twitter as twitter_provider
    from .accounts import youtube_account as youtube_provider
    from .accounts.manager import PROVIDERS, AccountManager

    probes = {"email": email_provider.test_connection,
              "twitter": twitter_provider.test_connection,
              "youtube-account": youtube_provider.test_connection}
    probe = probes.get(kind)
    if probe is None:
        typer.echo(f"unknown kind '{kind}' (known: {', '.join(sorted(probes))})")
        raise typer.Exit(code=2)
    try:
        session = AccountManager(_settings()).connect(
            kind, ref, PROVIDERS[kind]["capabilities"][0])
        typer.echo(f"{kind}/{ref}: {probe(session)}")
    except Exception as exc:
        typer.echo(f"{kind}/{ref}: FAILED — {exc}")
        raise typer.Exit(code=1)


@accounts_app.command("sync-youtube")
def accounts_sync_youtube(
    ref: str = typer.Argument("default"),
) -> None:
    """Register every public subscription of the linked channel as a source."""
    from .accounts.youtube_account import sync_subscriptions

    db = Database(_settings())
    with db.session() as session:
        report = sync_subscriptions(session, _settings(), ref)
    typer.echo(f"{report['account']}: {report['registered']} sources registered, "
               f"{report['already_present']} already present "
               f"({report['subscriptions']} subscriptions seen)")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
