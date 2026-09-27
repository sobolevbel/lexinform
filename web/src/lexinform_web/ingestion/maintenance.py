import datetime as dt
from dataclasses import dataclass, field, replace

from django.db import transaction
from django.db.models import Q

from lexinform.ports import Clock
from lexinform_web.accounts.models import User
from lexinform_web.ingestion.acquisition import GitSnapshotSource, HistoryError
from lexinform_web.ingestion.activation import (
    Activation,
    CorpusDropError,
    ReconciliationRequiredError,
    StaleActivationError,
    import_state,
)
from lexinform_web.ingestion.models import (
    ActiveImport,
    ImportGeneration,
    ImportIssue,
    ImportRun,
    SourceSnapshot,
)
from lexinform_web.ingestion.restore import SnapshotError
from lexinform_web.matters.models import SourceIdentity

KEEP_GENERATIONS = 10
KEEP_FOR = dt.timedelta(days=7)

ISSUE_OF: dict[type[SnapshotError], ImportIssue.Kind] = {
    HistoryError: ImportIssue.Kind.HISTORY,
    CorpusDropError: ImportIssue.Kind.CORPUS_DROP,
    ReconciliationRequiredError: ImportIssue.Kind.RECONCILIATION,
}


@dataclass(frozen=True)
class ImportReport:
    status: ImportRun.Status
    generation: int | None = None
    counts: dict[str, int] = field(default_factory=dict)
    error_kind: str = ""
    message: str = ""
    opened_issues: int = 0
    pruned_generations: int = 0


def open_issue(
    kind: ImportIssue.Kind,
    key: str,
    detail: str,
    now: dt.datetime,
    identity: SourceIdentity | None = None,
) -> bool:
    """Open the issue or refresh the one already open under this key; True if it is new."""
    updated = ImportIssue.objects.filter(key=key, resolved_at__isnull=True).update(
        last_seen_at=now, detail=detail
    )
    if updated:
        return False
    ImportIssue.objects.create(
        kind=kind,
        key=key,
        identity=identity,
        detail=detail,
        opened_at=now,
        last_seen_at=now,
    )
    return True


def check_operator(actor: User | None, reason: str) -> None:
    if actor is None or not reason.strip():
        raise ValueError("an operator and a reason are required")
    if not actor.is_active or not actor.is_staff or not actor.has_perm("matters.reconcile_matter"):
        raise ValueError("the operator lacks the reconciliation permission")


def resolve_issues(
    issues: list[ImportIssue], *, actor: User, resolution: str, now: dt.datetime
) -> int:
    check_operator(actor, resolution)
    return ImportIssue.objects.filter(
        pk__in=[issue.pk for issue in issues], resolved_at__isnull=True
    ).update(resolved_at=now, resolved_by=actor, resolution=resolution)


def prune_generations(now: dt.datetime) -> int:
    """Drop the snapshots of old inactive generations; the rows and every event revision stay."""
    active = ActiveImport.objects.filter(pk=1).values_list("generation_id", flat=True).first()
    newest = ImportGeneration.objects.order_by("-pk").values_list("pk", flat=True)[
        :KEEP_GENERATIONS
    ]
    stale = list(
        ImportGeneration.objects.filter(pruned_at__isnull=True, created_at__lt=now - KEEP_FOR)
        .exclude(pk__in=[*newest, *([active] if active else [])])
        .values_list("pk", flat=True)
    )
    with transaction.atomic():
        SourceSnapshot.objects.filter(generation_id__in=stale).delete()
        ImportGeneration.objects.filter(pk__in=stale).update(pruned_at=now)
    return len(stale)


def record_absences(activation: Activation, now: dt.datetime) -> int:
    opened = 0
    for identity in SourceIdentity.objects.filter(pk__in=activation.absent):
        opened += int(
            open_issue(
                ImportIssue.Kind.ABSENT,
                f"absent:{identity.source}:{identity.scope}:{identity.external_id}",
                f"missing from snapshot {activation.generation.source_commit}; last facts kept",
                now,
                identity,
            )
        )
    returned = ImportIssue.objects.filter(
        ~Q(identity_id__in=activation.absent),
        kind=ImportIssue.Kind.ABSENT,
        resolved_at__isnull=True,
    )
    returned.update(resolved_at=now, resolution="present again in the snapshot")
    return opened


def run_import(
    source: GitSnapshotSource,
    *,
    clock: Clock,
    public_channel: str | None = None,
    accept_drop: bool = False,
    rebaseline: str | None = None,
    actor: User | None = None,
    reason: str = "",
) -> ImportReport:
    """One timer tick: import, queue what needs an operator, prune, and write the run down."""
    if accept_drop or rebaseline:
        check_operator(actor, reason)
    started = clock.now()
    try:
        outcome = import_state(
            source,
            clock=clock,
            public_channel=public_channel,
            accept_drop=accept_drop,
            rebaseline=rebaseline,
        )
    except StaleActivationError as error:
        report = ImportReport(ImportRun.Status.FAILED, error_kind="stale", message=str(error))
    except SnapshotError as error:
        kind = next(
            (issue for cls, issue in ISSUE_OF.items() if isinstance(error, cls)),
            ImportIssue.Kind.INVALID,
        )
        new_issue = open_issue(kind, f"import:{kind}", str(error), clock.now())
        report = ImportReport(
            ImportRun.Status.FAILED,
            error_kind=kind.value,
            message=str(error),
            opened_issues=int(new_issue),
        )
    else:
        if isinstance(outcome, Activation):
            now = clock.now()
            opened = 0
            if outcome.status == "activated":
                opened = record_absences(outcome, now)
                ImportIssue.objects.filter(
                    key__startswith="import:", resolved_at__isnull=True
                ).update(
                    resolved_at=now,
                    resolved_by=actor,
                    resolution=reason or "a later snapshot was accepted",
                )
            report = ImportReport(
                ImportRun.Status(outcome.status),
                generation=outcome.generation.pk,
                counts=dict(outcome.generation.counts),
                opened_issues=opened,
            )
        else:
            report = ImportReport(ImportRun.Status(outcome))
    pruned = prune_generations(clock.now()) if report.status != "already_running" else 0
    ImportRun.objects.create(
        started_at=started,
        finished_at=clock.now(),
        status=report.status,
        generation_id=report.generation,
        error_kind=report.error_kind,
        message=report.message,
        pruned_generations=pruned,
    )
    return replace(report, pruned_generations=pruned)
