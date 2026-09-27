import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from lexinform.models import Bill, Publication, SourceCheck, Stage, StatusChange
from lexinform.models.analysis import AnalysisRecord
from lexinform.models.batch import BatchIntent, LlmBatchItem
from lexinform.models.enums import BillStatus, PublicationKind, PublicationStatus
from lexinform_web.ingestion.contract import (
    ActFact,
    AgendaFact,
    AppliedAnalysis,
    AspectCheck,
    ConsultationFact,
    Coverage,
    EventExplanation,
    Identity,
    IdentityRelation,
    ImportDocumentV1,
    ImportedBill,
    ImportedEvent,
    PendingWork,
    ProcessBaseline,
    PublicLink,
    RclStageFact,
    SnapshotOrigin,
    SourceFacts,
    StageFact,
    TelegramReference,
    WykazEvidence,
)


def identity_of(bill: Bill) -> Identity:
    if bill.is_wykaz:
        entry = bill.wykaz
        if entry is None:
            raise ValueError("Wykaz entry evidence is missing")
        url = urlsplit(PublicLink(url=entry.web_url).url)
        if url.hostname != "www.gov.pl" or not url.path.startswith("/web/premier/"):
            raise ValueError("Wykaz identity requires a gov.pl/premier entry URL")
        if entry.number != bill.number.removeprefix("WPL/"):
            raise ValueError("Wykaz entry number disagrees with bill")
        canonical_url = urlunsplit(("https", "www.gov.pl", url.path.rstrip("/"), "", ""))
        evidence = (entry.number, entry.published_at.isoformat(), canonical_url)
        digest = hashlib.sha256(json.dumps(evidence, ensure_ascii=False).encode()).hexdigest()
        return Identity(source="wykaz", scope="gov.pl/premier", external_id=f"entry:{digest}")
    if bill.is_rcl:
        return Identity(source="rcl", scope="global", external_id=bill.number.removeprefix("RCL/"))
    if bill.is_pre_print:
        return Identity(source="rpw", scope="global", external_id=bill.number)
    return Identity(source="sejm", scope=str(bill.term), external_id=bill.number)


def stage_fact(stage: Stage) -> StageFact:
    return StageFact(
        name=stage.stage_name,
        kind=stage.stage_type,
        date=stage.date,
        decision=stage.decision,
        committee=stage.committee_name or stage.committee_code,
        proposal=stage.proposal,
        children=tuple(stage_fact(child) for child in stage.children),
    )


def applied_analysis(record: AnalysisRecord) -> AppliedAnalysis:
    analysis = record.analysis
    return AppliedAnalysis(
        relevant=analysis.relevant,
        score=analysis.score,
        category=analysis.category,
        summary=analysis.summary,
        rationale=analysis.rationale,
        key_changes=tuple(analysis.key_changes),
        affected_groups=tuple(analysis.affected_groups),
        practical_impact=analysis.practical_impact,
        effective_date=analysis.effective_date,
        changes_since_previous=tuple(analysis.changes_since_previous),
        created_at=record.created_at,
        revision=record.revision,
        source_link=PublicLink(url=record.source_url) if record.source_url else None,
        text_sha256=record.text_sha256,
        text_checked_at=record.source_checked_at,
    )


def project_bill(bill: Bill, checks: Sequence[SourceCheck] = ()) -> ImportedBill:
    summary = bill.summary
    consultation = bill.consultation
    act = bill.act
    baseline = bill.observed_process
    source_url = summary.web_url
    if bill.rcl:
        source_url = bill.rcl.web_url
    elif bill.wykaz:
        source_url = bill.wykaz.web_url
    aliases = {bill.number}
    if bill.linked_wykaz_number:
        aliases.add(bill.linked_wykaz_number)
    if bill.rcl and bill.rcl.wykaz_number:
        aliases.add(bill.rcl.wykaz_number)
    return ImportedBill(
        identity=identity_of(bill),
        bot_status=bill.status,
        facts=SourceFacts(
            title=summary.title,
            description=summary.description,
            source_link=PublicLink(url=source_url),
            document_date=summary.document_date,
            closure_date=summary.closure_date,
            passed=summary.passed,
            stages=tuple(stage_fact(stage) for stage in bill.stages),
            rcl_stages=tuple(
                RclStageFact(
                    number=stage.number,
                    name=stage.name,
                    state=stage.state,
                    started=stage.started,
                    ended=stage.ended,
                )
                for stage in bill.rcl.stages
            )
            if bill.rcl
            else (),
            agenda=tuple(
                AgendaFact(
                    kind=item.kind,
                    date=item.date,
                    end_date=item.end_date,
                    start_time=item.start_time,
                    committee=item.committee_name or item.committee_code,
                    text=item.text,
                    condition=item.condition,
                    closed=item.closed,
                    apply_email=item.apply_email,
                    apply_by=item.apply_by,
                    video=PublicLink(url=item.video_url) if item.video_url else None,
                )
                for item in bill.agenda
            ),
            source_status=(
                bill.rcl.status if bill.rcl else bill.wykaz.status if bill.wykaz else None
            ),
            consultation=(
                ConsultationFact(
                    source=consultation.source,
                    start=consultation.start,
                    end=consultation.end,
                    links=tuple(
                        PublicLink(url=url)
                        for url in (
                            consultation.form_url,
                            consultation.survey_url,
                            consultation.letter_url,
                        )
                        if url
                    ),
                    email=consultation.email,
                    results_published=consultation.results_published,
                )
                if consultation
                else None
            ),
            act=(
                ActFact(
                    eli=act.eli,
                    title=act.title,
                    display_address=act.display_address,
                    promulgation_date=act.promulgation_date,
                    entry_into_force=act.entry_into_force,
                    links=tuple(
                        PublicLink(url=url) for url in (act.text_pdf_url, act.isap_url) if url
                    ),
                )
                if act
                else None
            ),
            display_aliases=tuple(sorted(aliases)),
            wykaz_evidence=(
                WykazEvidence(
                    number=bill.wykaz.number,
                    first_published_at=bill.wykaz.published_at,
                    entry_link=PublicLink(url=bill.wykaz.web_url),
                )
                if bill.wykaz
                else None
            ),
        ),
        processed=(
            ProcessBaseline(
                stages=tuple(stage_fact(stage) for stage in baseline.stages),
                closure_date=baseline.closure_date,
                passed=baseline.passed,
                analysis_revision=baseline.analysis_revision,
                analysis_sha256=baseline.analysis_sha256,
            )
            if baseline
            else None
        ),
        applied_analysis=applied_analysis(bill.analysis) if bill.analysis else None,
        observed_closure_date=bill.observed_closure_date,
        awaiting_batch_since=bill.awaiting_batch_since,
        has_staged_analysis=bill.ready_analysis is not None,
        observation_mode=bill.observation_mode.value if bill.observation_mode else None,
        observation_basis=bill.observation_basis.value if bill.observation_basis else None,
        checks=tuple(
            AspectCheck(
                aspect=check.aspect.value,
                last_success_at=check.last_success_at,
                last_attempt_at=check.last_attempt_at,
                failures=check.failures,
                last_outage_at=check.last_outage_at,
            )
            for check in sorted(checks, key=lambda c: c.aspect.value)
        ),
    )


def project_state(
    *,
    origin: SnapshotOrigin,
    bills: Sequence[Bill],
    changes: Sequence[StatusChange] = (),
    publications: Sequence[Publication] = (),
    public_channel: str | None = None,
    intents: Sequence[BatchIntent] = (),
    batch_items: Sequence[LlmBatchItem] = (),
    checks: Mapping[tuple[int, str], Sequence[SourceCheck]] | None = None,
) -> ImportDocumentV1:
    by_row = {(bill.term, bill.number): identity_of(bill) for bill in bills}
    if len(by_row) != len(bills):
        raise ValueError("duplicate state bill row")

    def reference(term: int, number: str) -> Identity:
        try:
            return by_row[term, number]
        except KeyError as error:
            raise ValueError("state contains a dangling bill reference") from error

    projected = tuple(
        project_bill(bill, (checks or {}).get((bill.term, bill.number), ())) for bill in bills
    )
    pending = [
        PendingWork(
            work_key=f"intent:{intent.provider}:{intent.request.custom_id}",
            identity=reference(intent.request.term, intent.request.number),
            kind=intent.request.call_kind,
            state="unknown" if intent.state == "submitting" else "queued",
            since=intent.created_at,
        )
        for intent in intents
    ]
    for item in batch_items:
        if item.consumed_at is not None:
            continue
        if item.call_kind not in {"analysis", "reanalysis", "joint", "supplement", "amendments"}:
            raise ValueError("unsupported batch call kind")
        pending.append(
            PendingWork(
                work_key=f"batch:{item.batch_id}:{item.custom_id}",
                identity=reference(item.term, item.number),
                kind=item.call_kind,
                state="result_staged" if item.result is not None else "submitted",
                since=None,
            )
        )
    relations: set[IdentityRelation] = set()
    for bill in bills:
        identity = reference(bill.term, bill.number)
        if bill.status == BillStatus.LINKED:
            if not bill.linked_number:
                raise ValueError("linked bill has no target")
            relations.add(
                IdentityRelation(
                    source=identity,
                    target=reference(bill.term, bill.linked_number),
                    kind="lifecycle",
                )
            )
        for number in bill.summary.prints_considered_jointly:
            other = reference(bill.term, number)
            if other != identity:
                pair = sorted((identity, other), key=lambda item: item.model_dump_json())
                relations.add(IdentityRelation(source=pair[0], target=pair[1], kind="joint"))
    events = tuple(
        ImportedEvent(
            event_key=f"state:{change.term}:{change.number}:{change.new_fingerprint}",
            identity=reference(change.term, change.number),
            observed_at=change.detected_at,
            stages=tuple(stage_fact(stage) for stage in change.new_stages),
            closure_detected=change.closure_detected,
            passed=change.passed,
            content_changed=change.content_changed,
            withdrawn=change.withdrawn,
            discontinued=change.discontinued,
            consultation_opened=change.consultation_opened,
            explanations=(
                (
                    EventExplanation(
                        kind="amendments",
                        source_link=PublicLink(url=change.amendments.source_url),
                        summary=change.amendments.amendments.summary,
                        points=tuple(change.amendments.amendments.changes),
                        created_at=change.amendments.created_at,
                    ),
                )
                if change.amendments
                else ()
            )
            + tuple(
                EventExplanation(
                    kind="supplement",
                    source_link=PublicLink(url=item.source_url),
                    title=item.title,
                    summary=item.digest.summary if item.digest else None,
                    points=tuple(item.digest.points) if item.digest else (),
                    created_at=item.created_at,
                )
                for item in change.supplements
            ),
        )
        for change in changes
    )
    links: set[TelegramReference] = set()
    if public_channel is not None:
        if not re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{3,31}", public_channel):
            raise ValueError("public channel must be an explicit @username")
        for publication in publications:
            if (
                publication.channel_id == public_channel
                and publication.status == PublicationStatus.SENT
                and publication.kind != PublicationKind.DIGEST
                and publication.message_id is not None
            ):
                links.add(
                    TelegramReference(
                        identity=reference(publication.term, publication.number),
                        link=PublicLink(
                            url=f"https://t.me/{public_channel[1:]}/{publication.message_id}"
                        ),
                    )
                )
    return ImportDocumentV1(
        origin=origin,
        bills=projected,
        relations=tuple(sorted(relations, key=lambda item: item.model_dump_json())),
        pending_work=tuple(pending),
        events=events,
        telegram=tuple(sorted(links, key=lambda item: item.link.url)),
        coverage=Coverage(
            total=len(projected),
            with_applied_analysis=sum(b.applied_analysis is not None for b in projected),
            analyzed_candidates=sum(
                b.bot_status == BillStatus.ANALYZED and b.applied_analysis is not None
                for b in projected
            ),
        ),
    )
