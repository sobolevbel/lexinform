"""Command-line interface."""

import logging
import re
import sys
from datetime import UTC, datetime, timedelta
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
    BillSubmission,
    ProcessSummary,
    PublicationKind,
    RclProject,
    RunReport,
    TokenUsage,
    flatten_stages,
    is_pre_print_number,
    is_rcl_number,
    normalize_wykaz_number,
    process_summary,
    rcl_fingerprint,
    rcl_project_id,
    rcl_stages,
    stage_fingerprint,
    usage_of,
)
from lexinform.pricing import cost_usd
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
    """Discover bills and run the keyword prefilter only (no LLM, no publishing)."""
    c = _container()
    try:
        pipeline = c.pipeline(dry_run=True)
        effective = pipeline.resolve_since(_utc(since))
        term = c.term()
        result = c.discovery_service().discover(term, effective)
        rcl_service = c.rcl_discovery_service()
        rcl_new = rcl_hits = 0
        if rcl_service is not None:
            rcl_result = rcl_service.discover(term, effective)
            rcl_new, rcl_hits = rcl_result.new, rcl_result.prefilter_hits
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
        f"rcl_new={rcl_new} rcl_hits={rcl_hits} text_hits={text_hits}"
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
            for b in c.repo.list_by_status(statuses, limit=limit)
            if b.has_process or (b.is_rcl and c.rcl is not None)
        ]
        accepted = 0
        for bill in skipped:
            if bill.is_rcl:
                bill = _with_rcl_text(c, bill)
            ok = service.check(bill)
            accepted += int(ok)
            if ok and bill.is_rcl:
                # The analysis wants every catalog and the consultation letter, not just the text.
                c.repo.save_rcl(bill.term, bill.number, _read_rcl_project(c, bill.number))
            fresh = c.repo.get(bill.term, bill.number)
            hits = ", ".join(fresh.prefilter_hits) if fresh else ""
            verdict = "PASS" if ok else "skip"
            typer.echo(f"  {verdict}  {bill.number:>14}  [{hits}]  {bill.summary.title}")
    finally:
        c.close()
    typer.echo(f"scanned={len(skipped)} accepted={accepted}")


def _with_rcl_text(c: Container, bill: Bill) -> Bill:
    """A skipped RCL row keeps only the project's skeleton: read its newest text again."""
    reader = c.rcl_reader()
    project = reader.with_text(reader.timeline(rcl_project_id(bill.number)))
    c.repo.save_rcl(bill.term, bill.number, project)
    return c.repo.get(bill.term, bill.number) or bill


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
    number: Annotated[str, typer.Argument(help="Print (druk) number, RPW/…, RCL/{id} or UC164.")],
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
    number: Annotated[
        str,
        typer.Argument(help="Print (druk) number, RPW/… before one, or RCL/{id} / UC164 on RCL."),
    ],
) -> None:
    """Show what the Sejm API (or RCL) and the local database know about a bill."""
    c = _container()
    try:
        number = _resolve_number(c, number)
        local = c.find_bill(number)
        if is_rcl_number(number):
            _show_rcl(c, number, local)
            return
        if is_pre_print_number(number):
            _show_pre_print(c, number, local)
            return
        term = local.term if local is not None else c.term()
        detail = c.gateway.get_process(term, number)
        print_info = c.gateway.get_print(term, number)
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
    if detail.eli:
        typer.echo(f"\npublished: {detail.display_address} ({detail.eli}) {detail.isap_url or ''}")
    if local and local.act:
        typer.echo(
            f"act: promulgated {local.act.promulgation_date}, in force {local.act.entry_into_force}"
        )
    typer.echo(
        "\nlocal: "
        + (
            f"status={local.status.value} hits={local.prefilter_hits} "
            f"attempts={local.analysis_attempts}"
            if local
            else "not in database"
        )
    )


DaysOpt = Annotated[int, typer.Option("--days", min=1, help="How many days back to look.")]


@app.command()
def runs(days: DaysOpt = 30) -> None:
    """List the recorded runs of the last days: what each found, posted and cost."""
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
        # Reports stored before the per-model breakdown existed carry tokens but no usage:
        # their cost is unknown, not zero.
        cost = cost_usd(r.llm_usage) if r.llm_usage or not r.llm_input_tokens else None
        typer.echo(
            f"{r.started_at:%Y-%m-%d %H:%M}  {r.mode:<8}  {'ok ' if r.ok else 'ERR'}  "
            f"{r.discovered:>4}  {r.analyzed:>4}  {r.published:>4}  {r.updates:>3}  "
            f"{len(r.errors):>4}  {_k(r.llm_input_tokens) + '/' + _k(r.llm_output_tokens):<18}"
            f"{_money(cost)}"
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
        f"{len(reports)} run(s) in the last {days} days: {_money(total)} total, "
        f"{_money(per_run)} per run"
    )
    for model, u in sorted(usage.items()):
        typer.echo(
            f"  {model}: in {_k(u.input)} · cache read {_k(u.cache_read)} · "
            f"cache write {_k(u.cache_creation)} · out {_k(u.output)} · "
            f"{_money(cost_usd({model: u}))}"
        )
    if reports:
        dearest = max(reports, key=lambda r: cost_usd(r.llm_usage) or 0.0)
        typer.echo(
            f"most expensive run: {dearest.started_at:%Y-%m-%d %H:%M} "
            f"({_money(cost_usd(dearest.llm_usage))}, {dearest.analyzed} analysed)"
        )
    if priciest:
        typer.echo("most expensive analyses (input tokens of the stored analysis):")
    for bill in priciest:
        record = bill.analysis
        assert record is not None
        typer.echo(
            f"  {bill.number}: {_k(record.input_tokens or 0)} in ({record.model}) "
            f"{_money(cost_usd({record.model: usage_of(record)}))}  {bill.summary.title[:70]}"
        )


def _k(tokens: int) -> str:
    return f"{tokens / 1000:.1f}k" if tokens >= 1000 else str(tokens)


def _money(usd: float | None) -> str:
    if usd is None:
        return "$?"  # a model missing from the price list
    return f"${usd:.2f}" if usd >= 0.01 or usd == 0 else f"${usd:.3f}"


YesOpt = Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")]


@app.command()
def republish(
    number: Annotated[str, typer.Argument(help="Print (druk) or RPW number.")],
    yes: YesOpt = False,
) -> None:
    """Post the card of a bill again (after a failed, unknown or accidentally deleted post).

    Forgets the channel's `new_bill` publication row and sends the card through the normal
    path, so the pending-before-send protocol and the retry bookkeeping still apply. Updates,
    act notices and reminders keep replying to the new card from now on.
    """
    c = _container()
    try:
        bill = _load_bill(c, number)
        if bill.analysis is None or not bill.analysis.analysis.relevant:
            typer.echo(f"{number} has no relevant analysis; nothing to publish", err=True)
            raise typer.Exit(code=2)
        channel = c.channel_id()
        existing = c.repo.get_publication(
            bill.term, bill.number, PublicationKind.NEW_BILL.value, channel
        )
        state = f"{existing.status.value} (message {existing.message_id})" if existing else "none"
        typer.echo(f"{number}: current card in {channel}: {state}")
        if not yes and not typer.confirm("Send the card again?"):
            raise typer.Exit(code=1)
        # A bill considered jointly with one that has a card gets its "alternative bill" reply
        # again instead of a card; both rows are forgotten so the normal path decides.
        for kind in (PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL):
            c.repo.delete_publication(bill.term, bill.number, kind.value, channel)
        ok = c.publishing_service(dry_run=False).publish_bill(bill)
        fresh = next(
            (
                pub
                for kind in (PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL)
                if (pub := c.repo.get_publication(bill.term, bill.number, kind.value, channel))
            ),
            None,
        )
        typer.echo(
            f"sent message {fresh.message_id}" if ok and fresh else "sending failed, see the log"
        )
        raise typer.Exit(code=0 if ok else 1)
    finally:
        c.close()


@app.command()
def reset(
    number: Annotated[str, typer.Argument(help="Print (druk) or RPW number.")],
    to: Annotated[
        BillStatus, typer.Option("--to", help="Status to put the bill into.")
    ] = BillStatus.ANALYSIS_PENDING,
    yes: YesOpt = False,
) -> None:
    """Put a bill back into a status with a clean retry budget (e.g. re-run a failed analysis).

    `--to analysis_pending` re-analyses the bill on the next run; `--to skipped_prefilter`
    silences a false positive. Existing analyses and posts are left untouched.
    """
    c = _container()
    try:
        bill = _load_bill(c, number)
        typer.echo(
            f"{number}: {bill.status.value} (attempts {bill.analysis_attempts}) -> {to.value}"
        )
        if not yes and not typer.confirm("Apply?"):
            raise typer.Exit(code=1)
        c.repo.reset_bill(bill.term, bill.number, to)
        if (
            to is BillStatus.ANALYSIS_PENDING
            and bill.rcl is not None
            and not bill.rcl.text_documents()
        ):
            # A skipped RCL row keeps only the project's skeleton: fetch the documents again.
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
    sub = local.submission if local and local.submission else _find_submission(c, number)
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
            f"status={local.status.value} hits={local.prefilter_hits} linked={local.linked_number}"
            if local
            else "not in database"
        )
    )


def _read_rcl_project(c: Container, number: str) -> RclProject:
    """The whole project from RCL: timeline, every reached stage's catalog, the letter."""
    reader = c.rcl_reader()
    return reader.complete(reader.timeline(rcl_project_id(number)))


_WYKAZ_NUMBER = re.compile(r"^U[A-Z]*\s?\d+$", re.IGNORECASE)


def _resolve_number(c: Container, number: str) -> str:
    """A wykaz number (UC164) names the RCL row it belongs to; anything else is passed on."""
    if not _WYKAZ_NUMBER.match(number):
        return number
    wykaz = normalize_wykaz_number(number) or number
    bill = c.find_rcl_by_wykaz(wykaz)
    if bill is None:
        typer.echo(f"{wykaz}: no RCL project with this number in the database", err=True)
        raise typer.Exit(code=1)
    return bill.number


def _load_bill(c: Container, number: str) -> Bill:
    """The bill from the database, fetched from the API (or RCL) and prefiltered on first sight."""
    number = _resolve_number(c, number)
    bill = c.find_bill(number)
    if bill is not None:
        return bill
    term = c.term()
    if is_rcl_number(number):
        project = _read_rcl_project(c, number)
        summary: ProcessSummary = process_summary(project, term=term)
        bill = c.repo.upsert_summary(summary, now=c.clock.now())
        c.repo.save_rcl(bill.term, bill.number, project)
        c.repo.save_stages(bill.term, bill.number, rcl_stages(project), rcl_fingerprint(project))
    elif is_pre_print_number(number):
        sub = _find_submission(c, number)
        summary = ProcessSummary.from_submission(sub)
        bill = c.repo.upsert_summary(summary, now=c.clock.now())
        c.repo.save_submission(bill.term, bill.number, sub)
    else:
        summary = c.gateway.get_process(term, number)
        bill = c.repo.upsert_summary(summary, now=c.clock.now())
    hits = c.prefilter.match(summary.title, summary.description)
    c.repo.set_status(
        bill.term,
        bill.number,
        BillStatus.ANALYSIS_PENDING if hits else BillStatus.SKIPPED_PREFILTER,
        prefilter_hits=hits,
    )
    stored = c.repo.get(term, number)
    assert stored is not None
    return stored


def _find_submission(c: Container, number: str) -> BillSubmission:
    sub = next((b for b in c.gateway.iter_bills(c.term()) if b.number == number), None)
    if sub is None:
        typer.echo(f"{number}: not found in /bills", err=True)
        raise typer.Exit(code=1)
    return sub


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
