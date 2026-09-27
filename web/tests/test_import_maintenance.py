import datetime as dt
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Permission
from django.core.management import CommandError, call_command
from django.test import override_settings

from import_builders import T0, MovingClock, bill, document, event, ref, sejm
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import Bill
from lexinform_web.accounts.models import User
from lexinform_web.ingestion.acquisition import GitSnapshotSource, HistoryError
from lexinform_web.ingestion.activation import CorpusDropError, accepted_reference, activate
from lexinform_web.ingestion.maintenance import (
    prune_generations,
    record_absences,
    resolve_issues,
    run_import,
)
from lexinform_web.ingestion.models import (
    ActiveImport,
    EventRevision,
    ImportGeneration,
    ImportIssue,
    ImportRun,
    SourceSnapshot,
)

pytestmark = pytest.mark.django_db
LEGACY = Path(__file__).parent / "fixtures/import_legacy.json"


def operator(*, permitted: bool = True) -> User:
    user = User.objects.create_user(username="ops", email="ops@example.test", is_staff=True)
    if permitted:
        user.user_permissions.add(Permission.objects.get(codename="reconcile_matter"))
    return User.objects.get(pk=user.pk)


def git(remote: Path, *arguments: str) -> str:
    return subprocess.run(
        [
            "git",
            "-C",
            str(remote),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.test",
            *arguments,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def commit_state(remote: Path, number: str, *, amend: bool = False) -> str:
    repository = SqliteBillRepository(":memory:")
    repository.migrate()
    legacy = Bill.model_validate(json.loads(LEGACY.read_text())["bill"])
    summary = legacy.summary.model_copy(update={"number": number})
    repository.upsert_summary(summary, now=T0)
    (remote / "lexinform.sql").write_text(repository.dump())
    repository.close()
    git(remote, "add", "lexinform.sql")
    git(remote, "commit", *(["--amend"] if amend else []), "-m", f"Snapshot {number}")
    return git(remote, "rev-parse", "HEAD")


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    path = tmp_path / "remote"
    path.mkdir()
    git(path, "init", "--initial-branch=state", "--object-format=sha1")
    return path


def test_corpus_drop_is_refused_until_accepted() -> None:
    clock = MovingClock()
    activate(
        document("a", *(bill(sejm(str(n + 1))) for n in range(10))), expected=None, clock=clock
    )
    smaller = document("b", *(bill(sejm(str(n + 1))) for n in range(7)))

    with pytest.raises(CorpusDropError):
        activate(smaller, expected=ref("a"), clock=clock)
    assert accepted_reference() == ref("a")
    assert activate(smaller, expected=ref("a"), clock=clock, accept_drop=True).status == "activated"


def test_a_small_drop_passes_the_guard() -> None:
    clock = MovingClock()
    activate(
        document("a", *(bill(sejm(str(n + 1))) for n in range(10))), expected=None, clock=clock
    )
    smaller = document("b", *(bill(sejm(str(n + 1))) for n in range(8)))

    assert activate(smaller, expected=ref("a"), clock=clock).status == "activated"


def test_absent_rows_are_queued_once_and_closed_when_they_return() -> None:
    clock = MovingClock()
    activate(document("a", bill(sejm("1")), bill(sejm("2"))), expected=None, clock=clock)
    gone = activate(
        document("b", bill(sejm("1"))), expected=ref("a"), clock=clock, accept_drop=True
    )
    still = activate(document("c", bill(sejm("1"))), expected=ref("b"), clock=clock)

    assert record_absences(gone, T0) == 1
    assert record_absences(still, T0) == 0
    issue = ImportIssue.objects.get()
    assert issue.kind == ImportIssue.Kind.ABSENT and issue.identity is not None
    back = activate(document("d", bill(sejm("1")), bill(sejm("2"))), expected=ref("c"), clock=clock)
    record_absences(back, T0)
    assert ImportIssue.objects.get().resolved_at == T0


def test_retention_keeps_ten_a_week_the_active_one_and_every_event() -> None:
    clock = MovingClock()
    previous = None
    for index, commit in enumerate("0123456789ab"):
        clock.at = T0 + dt.timedelta(hours=index)
        activate(
            document(commit, bill(sejm("1"), f"Title {commit}"), events=(event(sejm("1")),)),
            expected=previous,
            clock=clock,
        )
        previous = ref(commit)

    assert prune_generations(T0 + dt.timedelta(days=1)) == 0
    assert prune_generations(T0 + dt.timedelta(days=30)) == 2
    assert prune_generations(T0 + dt.timedelta(days=30)) == 0
    pruned = ImportGeneration.objects.filter(pruned_at__isnull=False)
    assert sorted(g.source_commit[0] for g in pruned) == ["0", "1"]
    assert not SourceSnapshot.objects.filter(generation__in=pruned).exists()
    assert EventRevision.objects.filter(generation__in=pruned).count() == 1
    assert ActiveImport.objects.get().generation not in pruned


def test_a_rejected_snapshot_opens_one_issue_and_writes_the_run(tmp_path: Path) -> None:
    source = GitSnapshotSource(tmp_path, tmp_path / "cache")
    with patch(
        "lexinform_web.ingestion.maintenance.import_state",
        side_effect=HistoryError("state history diverged"),
    ):
        first = run_import(source, clock=MovingClock())
        second = run_import(source, clock=MovingClock())

    assert first.status == second.status == ImportRun.Status.FAILED
    assert first.opened_issues == 1 and second.opened_issues == 0
    assert ImportIssue.objects.get().kind == ImportIssue.Kind.HISTORY
    assert ImportRun.objects.filter(error_kind="history").count() == 2


def test_overrides_need_a_permitted_operator_and_a_reason(tmp_path: Path) -> None:
    source = GitSnapshotSource(tmp_path, tmp_path / "cache")
    with pytest.raises(ValueError):
        run_import(source, clock=MovingClock(), accept_drop=True)
    with pytest.raises(ValueError):
        run_import(
            source,
            clock=MovingClock(),
            accept_drop=True,
            actor=operator(permitted=False),
            reason="audited",
        )
    assert not ImportRun.objects.exists()


def test_rewritten_history_waits_for_an_audited_rebaseline(remote: Path, tmp_path: Path) -> None:
    source = GitSnapshotSource(remote, tmp_path / "cache")
    commit_state(remote, "1")
    assert run_import(source, clock=MovingClock()).status == ImportRun.Status.ACTIVATED
    rewritten = commit_state(remote, "2", amend=True)

    refused = run_import(source, clock=MovingClock())
    assert refused.status == ImportRun.Status.FAILED and refused.error_kind == "history"
    wrong = run_import(
        source, clock=MovingClock(), rebaseline="0" * 40, actor=operator(), reason="audited"
    )
    assert wrong.status == ImportRun.Status.FAILED
    rebased = run_import(
        source,
        clock=MovingClock(),
        rebaseline=rewritten,
        actor=User.objects.get(),
        reason="audited rewrite",
    )

    assert rebased.status == ImportRun.Status.ACTIVATED
    reference = accepted_reference()
    assert reference is not None and reference.commit == rewritten
    issue = ImportIssue.objects.get(kind=ImportIssue.Kind.HISTORY)
    assert issue.resolution == "audited rewrite" and issue.resolved_by == User.objects.get()
    assert run_import(source, clock=MovingClock()).status == ImportRun.Status.UNCHANGED


def test_command_prints_the_report_and_fails_on_a_rejected_snapshot(
    remote: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit_state(remote, "1")
    with override_settings(STATE_REMOTE=str(remote), STATE_CACHE=str(tmp_path / "cache")):
        call_command("import_state")
        assert '"status": "activated"' in capsys.readouterr().out
        call_command("import_state")
        assert '"status": "unchanged"' in capsys.readouterr().out
        commit_state(remote, "2", amend=True)
        with pytest.raises(CommandError, match="history"):
            call_command("import_state")

        call_command("resolve_import_issue")
        listed = capsys.readouterr().out
        assert "history" in listed
        issue = ImportIssue.objects.get()
        operator()
        call_command(
            "resolve_import_issue",
            str(issue.pk),
            operator="ops@example.test",
            resolution="checked",
        )
        assert ImportIssue.objects.get().resolved_at is not None


def test_resolving_needs_permission() -> None:
    issue = ImportIssue.objects.create(
        kind=ImportIssue.Kind.INVALID,
        key="import:invalid",
        detail="x",
        opened_at=T0,
        last_seen_at=T0,
    )
    with pytest.raises(ValueError):
        resolve_issues([issue], actor=operator(permitted=False), resolution="fine", now=T0)
