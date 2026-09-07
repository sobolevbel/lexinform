"""Command-line interface."""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from lexinform import __version__
from lexinform.adapters.telegram import TelegramBotClient, TelegramRunNotifier
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.container import Container, build_container
from lexinform.logging_setup import configure_logging
from lexinform.models import (
    Bill,
    BillStatus,
    ProcessSummary,
    RunReport,
    flatten_stages,
    is_pre_print_number,
    stage_fingerprint,
)
from lexinform.services.pipeline import RunOptions
from lexinform.settings import Settings

app = typer.Typer(
    help="Tracks Polish Sejm bills that affect foreigners and posts summaries to Telegram.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
db_app = typer.Typer(help="Database maintenance (init, dump, restore).", no_args_is_help=True)
app.add_typer(db_app, name="db")

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
    """Discover bills and run the keyword prefilter only (no LLM, no publishing)."""
    c = _container()
    try:
        pipeline = c.pipeline(dry_run=True)
        effective = pipeline.resolve_since(_utc(since))
        result = c.discovery_service().discover(c.settings.term, effective)
        text_service = c.text_prefilter_service()
        text_hits = (
            text_service.run(c.settings.term, limit=c.settings.text_prefilter_max_per_run).hits
            if text_service
            else 0
        )
        pending = c.repo.list_by_status(c.settings.term, [BillStatus.ANALYSIS_PENDING], limit=500)
    finally:
        c.close()
    typer.echo(
        f"since={effective.isoformat()} seen={result.seen} new={result.new} "
        f"pre_print={result.pre_print_new} title_hits={result.prefilter_hits} "
        f"text_hits={text_hits}"
    )
    for bill in pending:
        typer.echo(
            f"  druk {bill.number:>6}  [{', '.join(bill.prefilter_hits)}]  {bill.summary.title}"
        )


@app.command()
def reprefilter(
    limit: Annotated[int, typer.Option(help="How many skipped bills to scan.")] = 50,
    include_text_skipped: Annotated[
        bool,
        typer.Option("--include-text-skipped", help="Also re-scan bills already rejected by text."),
    ] = False,
) -> None:
    """Scan the print PDFs of bills the title prefilter skipped.

    Bills that pass become analysis candidates for the next `run` (use `run --no-publish` after a
    large backfill to avoid flooding the channel).
    """
    c = _container()
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
            for b in c.repo.list_by_status(c.settings.term, statuses, limit=limit)
            if not b.is_pre_print
        ]
        accepted = 0
        for bill in skipped:
            ok = service.check(bill)
            accepted += int(ok)
            fresh = c.repo.get(bill.term, bill.number)
            hits = ", ".join(fresh.prefilter_hits) if fresh else ""
            verdict = "PASS" if ok else "skip"
            typer.echo(f"  {verdict}  druk {bill.number:>6}  [{hits}]  {bill.summary.title}")
    finally:
        c.close()
    typer.echo(f"scanned={len(skipped)} accepted={accepted}")


@app.command()
def analyze(
    number: Annotated[str, typer.Argument(help="Print (druk) number, e.g. 3039.")],
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
        typer.echo(
            f"druk {number}: relevant={a.relevant} score={a.score} category={a.category.value}"
        )
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
    number: Annotated[str, typer.Argument(help="Print (druk) number.")],
    to: Annotated[
        str | None, typer.Option("--to", help="Send to this chat id instead of printing.")
    ] = None,
) -> None:
    """Render the Telegram card for an analysed bill; optionally send it to a test chat."""
    c = _container()
    try:
        bill = _load_bill(c, number)
        if bill.analysis is None:
            typer.echo(
                f"druk {number} is not analysed yet; run `lexinform analyze {number}`", err=True
            )
            raise typer.Exit(code=2)
        print_info = c.gateway.get_print(c.settings.term, number)
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
                max_analyze=0,
                max_publish=0,
                mode="track",
            )
        )
    finally:
        c.close()
    typer.echo(f"updates={report.updates} errors={report.errors}")


@app.command()
def show(
    number: Annotated[str, typer.Argument(help="Print (druk) number, or RPW/… before one.")],
) -> None:
    """Show what the Sejm API and the local database know about a bill."""
    c = _container()
    try:
        local = c.repo.get(c.settings.term, number)
        if is_pre_print_number(number):
            _show_pre_print(c, number, local)
            return
        detail = c.gateway.get_process(c.settings.term, number)
        print_info = c.gateway.get_print(c.settings.term, number)
    finally:
        c.close()
    typer.echo(f"{detail.title}\n{detail.web_url}")
    typer.echo(
        f"type={detail.document_type_enum.value} applicant={detail.applicant_type.value} "
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
    typer.echo(
        "\nlocal: "
        + (
            f"status={local.status.value} hits={local.prefilter_hits} "
            f"attempts={local.analysis_attempts}"
            if local
            else "not in database"
        )
    )


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
        report = RunReport(started_at=now, finished_at=now, since=now, mode="run", errors=[message])
        client = TelegramBotClient(
            settings.telegram_bot_token, base_url=settings.telegram_api_base_url
        )
        TelegramRunNotifier(
            client,
            MessageFormatter(settings.output_language),
            channel_id=settings.telegram_log_channel_id,
        ).notify(report, [])
    except Exception as exc:  # nothing more we can do
        logging.getLogger(__name__).error("could not post startup failure to log channel: %s", exc)


def _show_pre_print(c: Container, number: str, local: Bill | None) -> None:
    sub = local.submission if local and local.submission else None
    if sub is None:
        sub = next((b for b in c.gateway.iter_bills(c.settings.term) if b.number == number), None)
    if sub is None:
        typer.echo(f"{number}: not found in /bills", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"{sub.title}\n{sub.pdf_url}")
    typer.echo(
        f"received={sub.date_of_receipt} applicant={sub.applicant.value} status={sub.status}"
        f" print={sub.print_number or '-'}"
        f" consultation={sub.consultation_start}..{sub.consultation_end}"
    )
    if sub.description:
        typer.echo(f"description: {sub.description}")
    typer.echo(
        "\nlocal: "
        + (
            f"status={local.status.value} hits={local.prefilter_hits} linked={local.linked_number}"
            if local
            else "not in database"
        )
    )


def _load_bill(c: Container, number: str) -> Bill:
    bill = c.repo.get(c.settings.term, number)
    if bill is None and is_pre_print_number(number):
        sub = next((b for b in c.gateway.iter_bills(c.settings.term) if b.number == number), None)
        if sub is None:
            typer.echo(f"{number}: not found in /bills", err=True)
            raise typer.Exit(code=1)
        bill = c.repo.upsert_summary(ProcessSummary.from_submission(sub), now=c.clock.now())
        c.repo.save_submission(bill.term, bill.number, sub)
        hits = c.prefilter.match(sub.title, sub.description)
        c.repo.set_status(
            bill.term,
            bill.number,
            BillStatus.ANALYSIS_PENDING if hits else BillStatus.SKIPPED_PREFILTER,
            prefilter_hits=hits,
        )
        bill = c.repo.get(c.settings.term, number)
        assert bill is not None
    if bill is None:
        detail = c.gateway.get_process(c.settings.term, number)
        bill = c.repo.upsert_summary(detail, now=c.clock.now())
        hits = c.prefilter.match(detail.title, detail.description)
        c.repo.set_status(
            bill.term,
            bill.number,
            BillStatus.ANALYSIS_PENDING if hits else BillStatus.SKIPPED_PREFILTER,
            prefilter_hits=hits,
        )
        bill = c.repo.get(c.settings.term, number)
        assert bill is not None
    assert isinstance(bill, Bill)
    return bill


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
