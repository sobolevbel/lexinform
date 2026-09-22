"""Command-line interface."""

import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import anthropic
import openai
import typer

from lexinform import __version__
from lexinform.adapters.github_inbox import GitHubInboxWriter
from lexinform.adapters.telegram import TelegramBotClient, TelegramRunNotifier
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.concurrency import fan_out
from lexinform.container import Container, build_container
from lexinform.errors import ServiceUnavailableError
from lexinform.logging_setup import configure_logging
from lexinform.models import (
    SILENCED_BY_OPERATOR,
    BackfillOutcome,
    BackfillReport,
    Bill,
    BillStatus,
    BillSubmission,
    RclProject,
    RunMode,
    RunReport,
    TokenUsage,
    flatten_stages,
    is_pre_print_number,
    is_rcl_number,
    is_wykaz_number,
    rcl_project_id,
    stage_fingerprint,
    usage_of,
)
from lexinform.pricing import cost_usd, format_tokens, format_usd
from lexinform.services.lookup import BillNotFoundError
from lexinform.services.pipeline import RunOptions
from lexinform.services.rcl_projects import RclProjectReader
from lexinform.services.text_prefilter import PrefilterLoad
from lexinform.settings import Settings

log = logging.getLogger(__name__)

app = typer.Typer(
    help="Tracks Polish Sejm bills that affect foreigners and posts summaries to Telegram.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
db_app = typer.Typer(help="Database maintenance (init, dump, restore).", no_args_is_help=True)
app.add_typer(db_app, name="db")

NUMBER_HELP = (
    "Print (druk) number, RPW/… before one, RCL/{id} on RCL, or a wykaz number (UD408):"
    " the RCL project when it is out, the wykaz prac RM entry before that."
)

SinceOpt = Annotated[
    datetime | None,
    typer.Option(
        "--since",
        help="Only bills modified since this ISO date/datetime (UTC).",
        formats=["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"],
    ),
]


def _settings() -> Settings:
    settings = Settings()
    configure_logging(settings.log_level, json_output=settings.log_json)
    return settings


def _container() -> Container:
    return build_container(_settings())


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: Annotated[
        bool, typer.Option("--version", help="Show version and exit.", is_eager=True)
    ] = False,
) -> None:
    if version:
        typer.echo(f"lexinform {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


@app.command()
def run(
    since: SinceOpt = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print messages, roll back DB changes.")
    ] = False,
    no_publish: Annotated[
        bool, typer.Option("--no-publish", help="Analyse but mark candidates as skipped.")
    ] = False,
    no_track: Annotated[bool, typer.Option("--no-track", help="Skip status tracking.")] = False,
    no_rcl: Annotated[
        bool, typer.Option("--no-rcl", help="Skip legislacja.rcl.gov.pl (government projects).")
    ] = False,
    full_track: Annotated[
        bool,
        typer.Option(
            "--full-track", help="Check every followed bill, not only those modified since."
        ),
    ] = False,
    max_publish: Annotated[int | None, typer.Option(help="Cap on new posts per run.")] = None,
    max_analyze: Annotated[int | None, typer.Option(help="Cap on LLM analyses per run.")] = None,
    min_score: Annotated[
        int | None, typer.Option(min=1, max=5, help="Minimum score to publish.")
    ] = None,
) -> None:
    """Daily job: discover, prefilter, analyse, publish, track."""
    settings = _settings()
    c: Container | None = None
    try:
        c = build_container(settings)
        pipeline = c.pipeline(dry_run=dry_run)
    except Exception as exc:
        _report_startup_failure(settings, f"startup failed: {type(exc).__name__}: {exc}")
        if c is not None:
            c.close()
        raise typer.Exit(code=1) from None
    s = c.settings
    try:
        report = pipeline.run(
            RunOptions(
                term=s.term,
                since=_utc(since),
                dry_run=dry_run,
                publish=not no_publish,
                track=not no_track,
                rcl=not no_rcl,
                full_track=full_track,
                max_publish=s.max_publish_per_run if max_publish is None else max_publish,
                max_analyze=s.max_analyze_per_run if max_analyze is None else max_analyze,
                min_score=s.min_score if min_score is None else min_score,
            )
        )
    finally:
        c.close()
    typer.echo(report.model_dump_json(indent=2))
    raise typer.Exit(code=0 if report.ok else 1)


@app.command()
def scan(since: SinceOpt = None) -> None:
    """Discover bills and run the keyword prefilter only (no LLM, no publishing).

    The "over" count is the bills, projects and plans whose road had already ended when this run
    first saw them.
    """
    c = _container()
    try:
        pipeline = c.pipeline(dry_run=True)
        effective = pipeline.resolve_since(_utc(since))
        term = c.term()
        result = c.discovery_service().discover(term, effective)
        over = result.over
        wykaz_service = c.wykaz_discovery_service()
        wykaz_new = 0
        named_by_register: list[int] = []
        if wykaz_service is not None:
            wykaz_result = wykaz_service.discover(term, effective)
            wykaz_new, named_by_register = wykaz_result.new, wykaz_result.on_rcl
            over += wykaz_result.over
        rcl_service = c.rcl_discovery_service()
        rcl_new = rcl_hits = 0
        if rcl_service is not None:
            rcl_result = rcl_service.discover(term, effective, from_register=named_by_register)
            rcl_new, rcl_hits = rcl_result.new, rcl_result.prefilter_hits
            over += rcl_result.over
        text_service = c.text_prefilter_service()
        text_hits = (
            text_service.run(limit=c.settings.text_prefilter_max_per_run).hits
            if text_service
            else 0
        )
        pending = c.repo.list_by_status([BillStatus.ANALYSIS_PENDING], limit=500)
    finally:
        c.close()
    typer.echo(
        f"term={term} since={effective.isoformat()} seen={result.seen} new={result.new} "
        f"pre_print={result.pre_print_new} title_hits={result.prefilter_hits} "
        f"rcl_new={rcl_new} rcl_hits={rcl_hits} text_hits={text_hits} "
        f"wykaz_new={wykaz_new} over={over}"
    )
    for bill in pending:
        label = bill.number if not bill.has_process else f"druk {bill.number}"
        typer.echo(f"  {label:>14}  [{', '.join(bill.prefilter_hits)}]  {bill.summary.title}")


@app.command()
def reprefilter(
    limit: Annotated[int, typer.Option(help="How many skipped bills to scan.")] = 50,
    include_text_skipped: Annotated[
        bool,
        typer.Option("--include-text-skipped", help="Also re-scan bills already rejected by text."),
    ] = False,
) -> None:
    """Scan the texts of bills the title prefilter skipped: print PDFs, and for RCL projects the
    newest text on legislacja.rcl.gov.pl (a skipped project keeps no documents, they are read
    again).

    Bills that pass become analysis candidates for the next `run`. A bill the operator silenced is
    left alone: `/skip` writes `SKIPPED_PREFILTER` like a keyword miss, and re-scanning it is the
    one thing `/skip` promises will not happen (`/unskip` is the way back).
    """
    c = _container()
    report = BackfillReport(
        started_at=c.clock.now(), limit=limit, include_text_skipped=include_text_skipped
    )
    try:
        service = c.text_prefilter_service()
        if service is None:
            typer.echo("text prefilter is disabled (LEXINFORM_TEXT_PREFILTER_ENABLED)", err=True)
            raise typer.Exit(code=2)
        statuses = [BillStatus.SKIPPED_PREFILTER]
        if include_text_skipped:
            statuses.append(BillStatus.SKIPPED_TEXT_PREFILTER)
        skipped = [
            b
            for b in c.repo.list_by_status(statuses, limit=limit)
            if (b.has_process or (b.is_rcl and c.rcl is not None))
            and b.last_error != SILENCED_BY_OPERATOR
        ]
        # `Container._once` memoises on a plain dict: a worker must never ask it for a service.
        reader = c.rcl_reader() if c.rcl is not None else None
        workers = c.settings.sejm_concurrency

        def scanned(bill: Bill) -> tuple[Bill, PrefilterLoad]:
            """Network only: the text a skipped RCL row no longer keeps, then the file to scan."""
            if bill.is_rcl and reader is not None:
                bill = bill.model_copy(update={"rcl": _rcl_text(reader, bill.number)})
            return bill, service.load(bill)

        passed: list[Bill] = []
        for outcome in fan_out(skipped, scanned, workers=workers):
            bill = outcome.item
            try:
                bill, loaded = outcome.result()
            except ServiceUnavailableError as exc:
                report.errors.append(exc.describe())
                log.error("aborting the backfill: %s", exc.describe())
                break
            except Exception as exc:
                loaded = service.problem(bill, exc)
            else:
                if bill.is_rcl and bill.rcl is not None:
                    c.repo.save_rcl(bill.term, bill.number, bill.rcl)
            ok = service.decide(bill, loaded)
            if ok and bill.is_rcl:
                passed.append(bill)
            fresh = c.repo.get(bill.term, bill.number)
            hits = tuple(fresh.prefilter_hits) if fresh else ()
            report.outcomes.append(
                BackfillOutcome(
                    number=bill.number,
                    title=bill.summary.title,
                    accepted=ok,
                    hits=hits,
                    reason=None if ok else (fresh.last_error if fresh else None),
                )
            )
            verdict = "PASS" if ok else "skip"
            typer.echo(f"  {verdict}  {bill.number:>14}  [{', '.join(hits)}]  {bill.summary.title}")
        _complete_rcl_projects(c, passed, workers=workers)
        report.finished_at = c.clock.now()
        _tell_the_log_channel(c, report)
    finally:
        c.close()
    typer.echo(f"scanned={report.scanned} accepted={len(report.accepted)}")


def _tell_the_log_channel(c: Container, report: BackfillReport) -> None:
    """A failure to say so must not fail the backfill, whose work is already written down.

    Building the notifier is inside the guard and not above it: `telegram_client` asks
    `require_telegram`, which wants the readers' channel too, so a step given only the log
    channel's credentials raised here and threw away 36 minutes of scanning (run of 15 Sept 2026).
    """
    try:
        notifier = c.run_notifier(dry_run=False)
        if notifier is not None:
            notifier.notify_backfill(report)
    except Exception as exc:
        log.warning("the backfill report did not reach the log channel: %s", exc)


@app.command(name="index-rcl-numbers")
def index_rcl_numbers(
    since: Annotated[
        datetime,
        typer.Option(
            "--since",
            formats=["%Y-%m-%d"],
            help="Read the listing back to this date (the register's numbers are reused, so the"
            " oldest date worth indexing is the oldest plan that could still be waiting).",
        ),
    ],
) -> None:
    """Write down which RCL project carries which number of the wykaz prac RM.

    Listing pages only — no timeline, no catalog, so this costs minutes and no tokens. A run
    does it for the projects it walks anyway; this fills in the years before the bot, which is
    what stops a plan whose project has been public since 2025 from getting a card that says
    there is no text yet.
    """
    c = _container()
    try:
        service = c.rcl_discovery_service()
        if service is None:
            typer.echo("RCL is disabled (LEXINFORM_RCL_ENABLED)", err=True)
            raise typer.Exit(code=2)
        seen = service.index_numbers(since.date())
    finally:
        c.close()
    typer.echo(f"indexed={seen}")


def _rcl_text(reader: RclProjectReader, number: str) -> RclProject:
    """Network only: the newest text of a skipped RCL row, whose stored skeleton keeps none."""
    return reader.with_text(reader.timeline(rcl_project_id(number)))


def _complete_rcl_projects(c: Container, bills: list[Bill], *, workers: int) -> None:
    """Store the whole project of every row the scan passed: it was read for its text alone."""
    if not bills:
        return
    lookup = c.bill_lookup()
    for outcome in fan_out(bills, lambda b: lookup.read_rcl_project(b.number), workers=workers):
        bill = outcome.item
        try:
            project = outcome.result()
        except ServiceUnavailableError as exc:
            log.error("RCL went down before %s was read whole: %s", bill.number, exc.describe())
            return
        except Exception as exc:
            log.warning("%s was not read whole: %s", bill.number, exc)
            continue
        c.repo.save_rcl(bill.term, bill.number, project)


@app.command()
def analyze(
    number: Annotated[str, typer.Argument(help=NUMBER_HELP)],
    force: Annotated[
        bool,
        typer.Option(
            "--force", help="Analyse even if the prefilter skipped it or it was analysed before."
        ),
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print the raw analysis JSON.")] = False,
) -> None:
    """Analyse one bill with the LLM and store the result."""
    c = _container()
    try:
        bill = _load_bill(c, number)
        if bill.status == BillStatus.ANALYZED and not force:
            typer.echo(f"druk {number} already analysed; use --force to redo", err=True)
        elif (
            bill.status in (BillStatus.SKIPPED_PREFILTER, BillStatus.SKIPPED_TEXT_PREFILTER)
            and not force
        ):
            typer.echo(
                f"druk {number} was skipped by the prefilter; use --force to analyse anyway",
                err=True,
            )
            raise typer.Exit(code=2)
        else:
            c.analysis_service().analyze_bill(bill)
        bill = _load_bill(c, number)
    finally:
        c.close()
    assert bill.analysis is not None
    if as_json:
        typer.echo(bill.analysis.model_dump_json(indent=2))
    else:
        a = bill.analysis.analysis
        typer.echo(f"druk {number}: relevant={a.relevant} score={a.score} category={a.category}")
        typer.echo(
            f"confidence={a.confidence:.2f} model={bill.analysis.model} "
            f"truncated={bill.analysis.truncated}"
        )
        typer.echo(f"\n{a.summary}\n")
        for change in a.key_changes:
            typer.echo(f"  • {change}")
        typer.echo(f"\n{a.practical_impact}\nrationale: {a.rationale}")


@app.command()
def preview(
    number: Annotated[str, typer.Argument(help=NUMBER_HELP)],
    to: Annotated[
        str | None, typer.Option("--to", help="Send to this chat id instead of printing.")
    ] = None,
) -> None:
    """Render the Telegram card for an analysed bill; optionally send it to a test chat."""
    c = _container()
    try:
        bill = _load_bill(c, number)
        if bill.analysis is None:
            typer.echo(f"{number} is not analysed yet; run `lexinform analyze {number}`", err=True)
            raise typer.Exit(code=2)
        print_info = c.gateway.get_print(bill.term, bill.number) if bill.has_process else None
        if to:
            result = c.telegram_publisher(channel_id=to).publish_new_bill(bill, print_info)
            typer.echo(f"sent message {result.message_id}")
        else:
            rendered = c.formatter.new_bill(bill, print_info)
            typer.echo(rendered.text)
            typer.echo(f"\n[{len(rendered.text)} chars]")
    finally:
        c.close()


@app.command()
def track(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Detect changes, print updates, roll back.")
    ] = False,
) -> None:
    """Check published bills for new legislative stages and post updates."""
    c = _container()
    try:
        s = c.settings
        pipeline = c.pipeline(dry_run=dry_run)
        report = pipeline.run(
            RunOptions(
                term=s.term,
                dry_run=dry_run,
                discover=False,
                publish=not dry_run,
                track=True,
                commands=False,
                max_analyze=0,
                max_publish=0,
                mode=RunMode.TRACK,
            )
        )
    finally:
        c.close()
    typer.echo(f"updates={report.updates} errors={report.errors}")


@app.command(name="collect-batches")
def collect_batches(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Collect and finish, roll back the writes.")
    ] = False,
) -> None:
    """Write down any batch a provider has finished, then analyse/publish/track what that frees.

    What the next scheduled `run` would do anyway, sooner: for the mikrus poller, or by hand.
    """
    c = _container()
    try:
        s = c.settings
        report = c.pipeline(dry_run=dry_run).run(
            RunOptions(
                term=s.term,
                dry_run=dry_run,
                discover=False,
                rcl=False,
                wykaz=False,
                commands=False,
                digest=False,
                mode=RunMode.COLLECT,
            )
        )
    finally:
        c.close()
    typer.echo(
        f"analyzed={report.analyzed} published={report.published}"
        f" updates={report.updates} errors={report.errors}"
    )
    raise typer.Exit(code=0 if report.ok else 1)


@app.command()
def commands(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the replies and cards, roll back, keep the inbox."),
    ] = False,
) -> None:
    """Answer the operator commands waiting in the inbox (LEXINFORM_INBOX_DIR) and nothing else.

    The daily run does the same at its start; this is what the relay's event runs.
    """
    c = _container()
    try:
        s = c.settings
        if c.command_inbox() is None:
            typer.echo(
                "no inbox: set LEXINFORM_INBOX_DIR to the directory of the inbox branch", err=True
            )
            raise typer.Exit(code=2)
        report = c.pipeline(dry_run=dry_run).run(
            RunOptions(
                term=s.term,
                dry_run=dry_run,
                discover=False,
                track=False,
                max_analyze=0,
                max_publish=0,
                min_score=s.min_score,
                mode=RunMode.COMMANDS,
            )
        )
    finally:
        c.close()
    for line in report.commands:
        typer.echo(line)
    typer.echo(
        f"handled={report.commands_handled} failed={report.commands_failed}"
        f" errors={len(report.errors)}"
    )
    raise typer.Exit(code=0 if report.ok else 1)


@app.command()
def listen(
    once: Annotated[
        bool, typer.Option("--once", help="One poll (up to the timeout), then exit.")
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", help="Read the channel, print the commands, file and confirm nothing."
        ),
    ] = False,
) -> None:
    """The relay: long-poll the technical channel for commands and file each one into the
    `inbox` branch on GitHub and start the workflow that answers it.

    Runs where something is always on (the VPS). Telegram lets one process poll a bot at a
    time: a second `listen` on the same token steals updates from the first, so test with
    `--dry-run`, which confirms nothing.
    """
    settings = _settings()
    c: Container | None = None
    try:
        c = build_container(settings)
        listener = c.command_listener(dry_run=dry_run)
        if once:
            filed = listener.poll_once()
            listener.confirm()
            for post in listener.filed:
                typer.echo(f"{post.update_id}: {post.text}")
            typer.echo(f"filed={filed}" + (" (dry run: nothing written)" if dry_run else ""))
        else:
            listener.run_forever()
    finally:
        if c is not None:
            c.close()


@app.command(name="poll-batches")
def poll_batches() -> None:
    """Ask GitHub to collect a finished batch, sooner than the next scheduled run.

    Meant for a systemd timer on the VPS (deploy/lexinform-batch-poll.{service,timer}), not the
    daily job. No database, no lexinform-specific state: it asks `llm_batch_provider` for its own
    list of batches, and a repository_dispatch is harmless to send more than once (`daily.yml`'s
    concurrency group keeps runs from racing, and a batch already collected has nothing left for
    `collect-batches` to do).
    """
    settings = _settings()
    if not settings.llm_batch_enabled:
        typer.echo("batching is off (LEXINFORM_LLM_BATCH_ENABLED)")
        return
    if not (settings.github_repo and settings.github_token):
        typer.echo("no GitHub repo/token to ask for a collect run", err=True)
        raise typer.Exit(code=2)
    if not _a_batch_is_done(settings):
        typer.echo("no finished batch")
        return
    writer = GitHubInboxWriter(settings.github_repo, settings.github_token)
    try:
        writer.dispatch("batch-ready")
    finally:
        writer.close()
    typer.echo("asked GitHub to collect")


def _a_batch_is_done(settings: Settings) -> bool:
    if settings.llm_batch_provider == "anthropic":
        claude = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        return any(b.processing_status == "ended" for b in claude.messages.batches.list(limit=20))
    gpt = openai.OpenAI(api_key=settings.openai_api_key)
    return any(b.status == "completed" for b in gpt.batches.list(limit=20))


@app.command()
def show(
    number: Annotated[str, typer.Argument(help=NUMBER_HELP)],
) -> None:
    """Show what the Sejm API (or RCL) and the local database know about a bill."""
    c = _container()
    try:
        number = _resolve_number(c, number)
        local = c.bill_lookup().find(number)
        if is_rcl_number(number):
            _show_rcl(c, number, local)
            return
        if is_pre_print_number(number):
            _show_pre_print(c, number, local)
            return
        if is_wykaz_number(number):
            _show_wykaz(c, number, local)
            return
        term = local.term if local is not None else c.term()
        detail = c.gateway.get_process(term, number)
        print_info = c.gateway.get_print(term, number)
    finally:
        c.close()
    typer.echo(f"{detail.title}\n{detail.web_url}")
    typer.echo(
        f"type={detail.document_type_enum} applicant={detail.applicant_type} "
        f"passed={detail.passed} closure={detail.closure_date}"
    )
    if detail.description:
        typer.echo(f"description: {detail.description}")
    typer.echo(f"\nstages (fingerprint {stage_fingerprint(detail.stages)[:12]}):")
    for stage in flatten_stages(detail.stages):
        typer.echo(
            f"  {stage.date or '          '}  {stage.stage_type:<28} {stage.stage_name}"
            + (f" — {stage.decision}" if stage.decision else "")
        )
    typer.echo("\nattachments:")
    for att in print_info.attachments:
        typer.echo(f"  {att.name}  {att.url}")
    for extra in print_info.additional_prints:
        typer.echo(f"  + {extra.number}: {extra.title}")
    if detail.eli:
        typer.echo(f"\npublished: {detail.display_address} ({detail.eli}) {detail.isap_url or ''}")
    if local and local.act:
        typer.echo(
            f"act: promulgated {local.act.promulgation_date}, in force {local.act.entry_into_force}"
        )
    typer.echo(
        "\nlocal: "
        + (
            f"status={local.status} hits={local.prefilter_hits} attempts={local.analysis_attempts}"
            if local
            else "not in database"
        )
    )


DaysOpt = Annotated[int, typer.Option("--days", min=1, help="How many days back to look.")]


@app.command()
def runs(days: DaysOpt = 30) -> None:
    """List the recorded runs of the last days: what each found, posted and cost.

    A run recorded before the per-model breakdown existed carries tokens but no usage: its cost
    is unknown, not zero, and is shown as such.
    """
    c = _container()
    try:
        reports = c.repo.list_runs(since=c.clock.now() - timedelta(days=days))
    finally:
        c.close()
    if not reports:
        typer.echo(f"no runs recorded in the last {days} days")
        return
    typer.echo(
        "started (UTC)     mode      ok   disc  anal  publ  upd  errs  tokens in/out       cost"
    )
    for r in reports:
        cost = cost_usd(r.llm_usage) if r.llm_usage or not r.llm_input_tokens else None
        tokens = f"{format_tokens(r.llm_input_tokens)}/{format_tokens(r.llm_output_tokens)}"
        typer.echo(
            f"{r.started_at:%Y-%m-%d %H:%M}  {r.mode:<8}  {'ok ' if r.ok else 'ERR'}  "
            f"{r.discovered:>4}  {r.analyzed:>4}  {r.published:>4}  {r.updates:>3}  "
            f"{len(r.errors):>4}  {tokens:<18}{format_usd(cost)}"
        )


@app.command()
def digest(
    ref: Annotated[
        str | None, typer.Option("--ref", help="ISO week (2026-W38); the week just ended.")
    ] = None,
    publish: Annotated[
        bool, typer.Option("--publish", help="Post it to the readers' channel, not as a draft.")
    ] = False,
) -> None:
    """Draft the week's digest into the technical channel, or publish an approved one.

    The draft carries a button; pressing it in the channel files this same command with
    `--publish`, and the week is built again from the database before it goes out.
    """
    c = _container()
    try:
        service = c.digest_service(dry_run=False)
        if service is None:
            typer.echo(
                "the digest is off (LEXINFORM_DIGEST_ENABLED) or has no technical channel to"
                " draft into (LEXINFORM_TELEGRAM_LOG_CHANNEL_ID)",
                err=True,
            )
            raise typer.Exit(code=2)
        week = ref or service.current_ref()
        done = service.publish(week) if publish else service.draft(week)
    finally:
        c.close()
    if done.failed:
        typer.echo(done.note, err=True)
        raise typer.Exit(code=1)
    where = "channel" if publish else "technical channel"
    sent = done.published or done.drafted
    typer.echo(
        f"{done.ref} posted to the {where} as message {done.message_id}" if sent else done.note
    )


@app.command()
def cost(
    days: DaysOpt = 30,
    top: Annotated[int, typer.Option("--top", min=0, help="Most expensive analyses.")] = 5,
) -> None:
    """LLM spend of the last days: per model, per run, and the most expensive analyses."""
    c = _container()
    try:
        reports = c.repo.list_runs(since=c.clock.now() - timedelta(days=days))
        priciest = c.repo.most_expensive_analyses(limit=top)
    finally:
        c.close()
    usage: dict[str, TokenUsage] = {}
    for r in reports:
        for model, u in r.llm_usage.items():
            usage[model] = usage.get(model, TokenUsage()).plus(u)
    total = cost_usd(usage)
    per_run = total / len(reports) if total is not None and reports else None
    typer.echo(
        f"{len(reports)} run(s) in the last {days} days: {format_usd(total)} total, "
        f"{format_usd(per_run)} per run"
    )
    for model, u in sorted(usage.items()):
        typer.echo(
            f"  {model}: in {format_tokens(u.input)} · cache read {format_tokens(u.cache_read)} · "
            f"cache write {format_tokens(u.cache_creation)} · out {format_tokens(u.output)} · "
            f"{format_usd(cost_usd({model: u}))}"
        )
    if reports:
        dearest = max(reports, key=lambda r: cost_usd(r.llm_usage) or 0.0)
        typer.echo(
            f"most expensive run: {dearest.started_at:%Y-%m-%d %H:%M} "
            f"({format_usd(cost_usd(dearest.llm_usage))}, {dearest.analyzed} analysed)"
        )
    if priciest:
        typer.echo("most expensive analyses (input tokens of the stored analysis):")
    for bill in priciest:
        record = bill.analysis
        assert record is not None
        typer.echo(
            f"  {bill.number}: {format_tokens(record.input_tokens or 0)} in ({record.model}) "
            f"{format_usd(cost_usd({record.model: usage_of(record)}))}  {bill.summary.title[:70]}"
        )


YesOpt = Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")]


@app.command()
def republish(
    number: Annotated[str, typer.Argument(help=NUMBER_HELP)],
    yes: YesOpt = False,
) -> None:
    """Post the card of a bill again (after a failed, unknown or accidentally deleted post).

    Forgets the channel's `new_bill` row and sends the card through the normal path, so the
    pending-before-send protocol still applies and later replies hang under the new card. A print
    considered jointly with a carded one gets its reply again: the normal path decides which.
    """
    c = _container()
    try:
        bill = _load_bill(c, number)
        if bill.analysis is None or not bill.analysis.analysis.relevant:
            typer.echo(f"{number} has no relevant analysis; nothing to publish", err=True)
            raise typer.Exit(code=2)
        publishing = c.publishing_service(dry_run=False)
        existing = publishing.card_of(bill)
        state = f"{existing.status} (message {existing.message_id})" if existing else "none"
        typer.echo(f"{number}: current card in {c.channel_id()}: {state}")
        if not yes and not typer.confirm("Send the card again?"):
            raise typer.Exit(code=1)
        publishing.forget_card(bill)
        ok = publishing.publish_bill(bill)
        fresh = publishing.card_of(bill)
        typer.echo(
            f"sent message {fresh.message_id}" if ok and fresh else "sending failed, see the log"
        )
        raise typer.Exit(code=0 if ok else 1)
    finally:
        c.close()


@app.command()
def forget(
    number: Annotated[str, typer.Argument(help=NUMBER_HELP)],
    yes: YesOpt = False,
) -> None:
    """Drop the card the channel remembers for a bill, and post nothing in its place.

    For a card deleted from the channel by hand: the `sent` row keeps the bill followed and the
    refresher edits a message that is not there once a run. `republish` clears the same rows by
    sending the card again, which is wrong when it was deleted on purpose.

    Both card kinds go, the card and the "alternative bill" reply, so what the bill gets next
    is the publishing rule's decision: an analysed, relevant, important enough bill is a
    candidate again on the next run; a silenced one simply stops being followed.
    """
    c = _container()
    try:
        bill = _load_bill(c, number)
        # Reading and dropping the rows is repository work; asking for the live publisher would
        # only demand a bot token for a command whose whole point is that it sends nothing.
        publishing = c.publishing_service(dry_run=True)
        existing = publishing.card_of(bill)
        if existing is None:
            typer.echo(f"{number}: no card recorded in {c.channel_id()}; nothing to forget")
            raise typer.Exit(code=0)
        typer.echo(
            f"{number}: card in {c.channel_id()}: {existing.kind} {existing.status}"
            f" (message {existing.message_id})"
        )
        if not yes and not typer.confirm("Forget it, posting nothing?"):
            raise typer.Exit(code=1)
        publishing.forget_card(bill)
        typer.echo("forgotten; the next run decides whether the bill gets a card again")
    finally:
        c.close()


@app.command()
def reset(
    number: Annotated[str, typer.Argument(help=NUMBER_HELP)],
    to: Annotated[
        BillStatus, typer.Option("--to", help="Status to put the bill into.")
    ] = BillStatus.ANALYSIS_PENDING,
    yes: YesOpt = False,
) -> None:
    """Put a bill back into a status with a clean retry budget (e.g. re-run a failed analysis).

    `--to analysis_pending` re-analyses the bill on the next run; `--to skipped_prefilter`
    silences a false positive. Existing analyses and posts are left untouched. A skipped RCL row
    keeps only the project's skeleton, so its documents are read again when it is revived.
    """
    c = _container()
    try:
        bill = _load_bill(c, number)
        typer.echo(f"{number}: {bill.status} (attempts {bill.analysis_attempts}) -> {to}")
        if not yes and not typer.confirm("Apply?"):
            raise typer.Exit(code=1)
        c.repo.reset_bill(bill.term, bill.number, to)
        if (
            to is BillStatus.ANALYSIS_PENDING
            and bill.rcl is not None
            and not bill.rcl.text_documents()
        ):
            c.repo.save_rcl(bill.term, bill.number, _read_rcl_project(c, bill.number))
            typer.echo("project documents re-read from RCL")
        typer.echo("done")
    finally:
        c.close()


@db_app.command("init")
def db_init() -> None:
    """Create the database file and schema."""
    c = _container()
    c.close()
    typer.echo(f"database ready at {c.settings.db_path}")


@db_app.command("dump")
def db_dump(output: Annotated[Path, typer.Argument(help="Path of the SQL dump to write.")]) -> None:
    """Write the whole database as a text SQL script (for the git `state` branch)."""
    c = _container()
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(c.repo.dump(), encoding="utf-8")
    finally:
        c.close()
    typer.echo(f"dumped to {output}")


@db_app.command("restore")
def db_restore(
    source: Annotated[Path, typer.Argument(help="SQL dump produced by `db dump`.")],
    missing_ok: Annotated[
        bool, typer.Option("--missing-ok", help="Exit 0 with an empty DB if the file is absent.")
    ] = False,
) -> None:
    """Replace the database contents with a dump."""
    if not source.exists():
        if missing_ok:
            c = _container()
            c.close()
            typer.echo(f"{source} not found; starting with an empty database")
            return
        typer.echo(f"{source} not found", err=True)
        raise typer.Exit(code=2)
    c = _container()
    try:
        c.repo.restore(source.read_text(encoding="utf-8"))
        c.repo.migrate()
    finally:
        c.close()
    typer.echo(f"restored from {source}")


def _report_startup_failure(settings: Settings, message: str) -> None:
    """Log a startup problem and, when a log channel is configured, post it there too."""
    logging.getLogger(__name__).error(message)
    if not (settings.telegram_log_channel_id and settings.telegram_bot_token):
        return
    try:
        now = datetime.now(UTC)
        report = RunReport(
            started_at=now, finished_at=now, since=now, mode=RunMode.RUN, errors=[message]
        )
        client = TelegramBotClient(
            settings.telegram_bot_token, base_url=settings.telegram_api_base_url
        )
        TelegramRunNotifier(
            client,
            MessageFormatter(settings.output_language),
            channel_id=settings.telegram_log_channel_id,
        ).notify(report, [])
    except Exception as exc:
        logging.getLogger(__name__).error("could not post startup failure to log channel: %s", exc)


def _show_pre_print(c: Container, number: str, local: Bill | None) -> None:
    sub = local.submission if local and local.submission else _find_submission(c, number)
    typer.echo(f"{sub.title}\n{sub.pdf_url}")
    typer.echo(
        f"received={sub.date_of_receipt} applicant={sub.applicant} status={sub.status}"
        f" print={sub.print_number or '-'}"
        f" consultation={sub.consultation_start}..{sub.consultation_end}"
    )
    if sub.description:
        typer.echo(f"description: {sub.description}")
    typer.echo(
        "\nlocal: "
        + (
            f"status={local.status} hits={local.prefilter_hits} linked={local.linked_number}"
            if local
            else "not in database"
        )
    )


def _show_wykaz(c: Container, number: str, local: Bill | None) -> None:
    entry = local.wykaz if local and local.wykaz else None
    if entry is None:
        entry = _or_exit(lambda: c.bill_lookup().read_wykaz_entry(number))
    typer.echo(f"{entry.title}\n{entry.web_url}")
    typer.echo(
        f"number={entry.number} kind={entry.kind} type={entry.doc_type} organ={entry.organ}"
        f" status={entry.status or '-'} published={entry.published_at:%Y-%m-%d %H:%M}"
        f" planned={entry.planned_adoption or '-'} rcl={entry.rcl_project_id or '-'}"
    )
    if entry.person:
        typer.echo(f"responsible: {entry.person}")
    for label, text in (("cele", entry.goals), ("istota", entry.essence)):
        if text:
            typer.echo(f"\n{label}:\n{text}")
    if entry.resignation:
        typer.echo(f"\nrezygnacja: {entry.resignation}")
    typer.echo(
        "\nlocal: "
        + (
            f"status={local.status} hits={local.prefilter_hits} linked={local.linked_number}"
            if local
            else "not in the database"
        )
    )


def _show_rcl(c: Container, number: str, local: Bill | None) -> None:
    project = local.rcl if local and local.rcl else _read_rcl_project(c, number)
    typer.echo(f"{project.title}\n{project.web_url}")
    typer.echo(
        f"applicant={project.applicant} wykaz={project.wykaz_number} status={project.status}"
        f" created={project.created} modified={project.modified}"
        f" rm={project.rm_number or '-'} print={project.print_number or '-'}"
    )
    typer.echo(f"keywords: {', '.join(project.keywords)}; działy: {', '.join(project.departments)}")
    typer.echo("\nstages:")
    for stage in project.stages:
        folders = ", ".join(f"{f.kind} {len(f.documents)}" for f in stage.folders if f.documents)
        typer.echo(
            f"  {stage.modified or '          '}  {stage.state:<11} {stage.number:>2}. {stage.name}"
            + (f"  [{folders}]" if folders else "")
        )
    consultation = project.consultation
    if consultation is not None:
        typer.echo(
            f"\nconsultation: deadline={consultation.deadline} ({consultation.days} days from"
            f" {consultation.letter_date or 'the letter on RCL'}) email={consultation.email}"
            f" positions={consultation.positions} letter={consultation.letter_url}"
        )
    typer.echo("\ntext documents:")
    for role, doc in project.text_documents().items():
        typer.echo(f"  {role:<14} {doc.name}  {doc.url}")
    typer.echo(
        "\nlocal: "
        + (
            f"status={local.status} hits={local.prefilter_hits} linked={local.linked_number}"
            if local
            else "not in database"
        )
    )


def _read_rcl_project(c: Container, number: str) -> RclProject:
    """The whole project from RCL: timeline, every reached stage's catalog, the letter."""
    return _or_exit(lambda: c.bill_lookup().read_rcl_project(number))


def _resolve_number(c: Container, number: str) -> str:
    """A wykaz number (UC164) names the RCL row it belongs to; anything else is passed on."""
    return _or_exit(lambda: c.bill_lookup().resolve_number(number))


def _load_bill(c: Container, number: str) -> Bill:
    """The bill from the database, fetched from the API (or RCL) and prefiltered on first sight."""
    return _or_exit(lambda: c.bill_lookup().load(number))


def _find_submission(c: Container, number: str) -> BillSubmission:
    return _or_exit(lambda: c.bill_lookup().find_submission(c.term(), number))


def _or_exit[T](action: Callable[[], T]) -> T:
    """A bill nobody knows is a message and exit code 1, not a traceback."""
    try:
        return action()
    except BillNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
