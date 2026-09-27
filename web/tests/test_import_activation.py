import datetime as dt
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Permission
from django.db import connections

from import_builders import (
    RCL,
    T0,
    MovingClock,
    bill,
    document,
    event,
    ref,
    sejm,
)
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import Bill
from lexinform.models.enums import BillStatus
from lexinform_web.accounts.models import User
from lexinform_web.ingestion.acquisition import GitSnapshotSource
from lexinform_web.ingestion.activation import (
    Activation,
    ReconciliationRequiredError,
    StaleActivationError,
    accepted_reference,
    activate,
    import_state,
)
from lexinform_web.ingestion.contract import (
    IdentityRelation,
)
from lexinform_web.ingestion.models import (
    ActiveImport,
    EventRevision,
    ImportGeneration,
    MatterEvent,
    MatterFacts,
    SourceSnapshot,
)
from lexinform_web.ingestion.selectors import active_generation, events_at, facts_at
from lexinform_web.matters.models import Matter, MatterRelation, SourceIdentity
from lexinform_web.matters.services import merge

pytestmark = pytest.mark.django_db


def test_first_activation_and_repeat_create_nothing_twice() -> None:
    clock = MovingClock()
    first = activate(document("a", bill(sejm("1"))), expected=None, clock=clock)
    repeat = activate(document("a", bill(sejm("1"))), expected=None, clock=clock)

    assert first.status == "activated"
    assert repeat.status == "unchanged"
    assert repeat.generation.pk == first.generation.pk
    assert accepted_reference() == ref("a")
    assert ImportGeneration.objects.count() == 1
    assert Matter.objects.count() == 1
    assert SourceSnapshot.objects.count() == 1
    assert first.generation.counts["matters"] == 1


def test_check_time_is_freshness_and_title_is_content() -> None:
    clock = MovingClock()
    activate(document("a", bill(sejm("1"))), expected=None, clock=clock)
    clock.at = T0 + dt.timedelta(hours=1)
    second = activate(
        document("b", bill(sejm("1"), checked=clock.at)), expected=ref("a"), clock=clock
    )
    clock.at = T0 + dt.timedelta(hours=2)
    third = activate(document("c", bill(sejm("1"), "Nowy tytuł")), expected=ref("b"), clock=clock)

    matter = Matter.objects.get()
    kept = facts_at(second.generation, matter.pk)
    changed = facts_at(third.generation, matter.pk)
    assert kept is not None and kept.content_updated_at == T0
    assert changed is not None and changed.content_updated_at == clock.at
    assert changed.title == "Nowy tytuł"


def test_stale_or_old_snapshot_changes_nothing() -> None:
    clock = MovingClock()
    activate(document("a", bill(sejm("1"))), expected=None, clock=clock)
    activate(document("b", bill(sejm("1"))), expected=ref("a"), clock=clock)

    with pytest.raises(StaleActivationError):
        activate(document("c", bill(sejm("2"))), expected=ref("a"), clock=clock)
    with pytest.raises(StaleActivationError):
        activate(document("c", bill(sejm("2"))), expected=None, clock=clock)
    assert accepted_reference() == ref("b")
    assert ImportGeneration.objects.count() == 2
    assert not SourceIdentity.objects.filter(external_id="2").exists()


def test_failure_before_commit_leaves_no_trace_and_retry_after_commit_is_noop() -> None:
    clock = MovingClock()
    activate(document("a", bill(sejm("1"))), expected=None, clock=clock)
    with (
        patch.object(MatterFacts.objects, "create", side_effect=RuntimeError("killed")),
        pytest.raises(RuntimeError),
    ):
        activate(document("b", bill(sejm("1")), bill(sejm("2"))), expected=ref("a"), clock=clock)
    assert accepted_reference() == ref("a")
    assert ImportGeneration.objects.count() == 1
    assert Matter.objects.count() == 1

    done = activate(document("b", bill(sejm("1")), bill(sejm("2"))), expected=ref("a"), clock=clock)
    again = activate(
        document("b", bill(sejm("1")), bill(sejm("2"))), expected=ref("a"), clock=clock
    )
    assert (done.status, again.status) == ("activated", "unchanged")
    assert Matter.objects.count() == 2


def test_lifecycle_link_keeps_the_matter_and_shows_the_print() -> None:
    clock = MovingClock()
    first = activate(document("a", bill(RCL, "Projekt RCL")), expected=None, clock=clock)
    matter = Matter.objects.get()
    second = activate(
        document(
            "b",
            bill(RCL, "Projekt RCL", status=BillStatus.LINKED),
            bill(sejm("3100"), "Druk 3100"),
            relations=(IdentityRelation(source=RCL, target=sejm("3100"), kind="lifecycle"),),
        ),
        expected=ref("a"),
        clock=clock,
    )

    assert Matter.objects.get().pk == matter.pk
    assert SourceIdentity.objects.filter(matter=matter).count() == 2
    facts = facts_at(second.generation, matter.pk)
    assert facts is not None and facts.title == "Druk 3100"
    old = facts_at(first.generation, matter.pk)
    assert old is not None and old.title == "Projekt RCL"


def test_joint_prints_stay_separate_matters() -> None:
    joint = IdentityRelation(source=sejm("1929"), target=sejm("1933"), kind="joint")
    result = activate(
        document("a", bill(sejm("1929")), bill(sejm("1933")), relations=(joint,)),
        expected=None,
        clock=MovingClock(),
    )
    assert Matter.objects.count() == 2
    assert MatterRelation.objects.get().kind == MatterRelation.Kind.JOINT
    assert MatterFacts.objects.filter(generation=result.generation).count() == 2


def test_editorial_state_survives_import_and_merged_facts_go_to_canonical() -> None:
    clock = MovingClock()
    activate(document("a", bill(sejm("1")), bill(sejm("2"))), expected=None, clock=clock)
    first = SourceIdentity.objects.get(external_id="1").matter
    second = SourceIdentity.objects.get(external_id="2").matter
    Matter.objects.filter(pk=first.pk).update(visibility=Matter.Visibility.HIDDEN)
    editor = User.objects.create_user(username="ed", email="ed@example.com", is_staff=True)
    editor.user_permissions.add(Permission.objects.get(codename="reconcile_matter"))
    merge(second.pk, first.pk, actor=User.objects.get(pk=editor.pk), reason="same", clock=clock)

    result = activate(
        document("b", bill(sejm("1")), bill(sejm("2"), "Drugi")), expected=ref("a"), clock=clock
    )

    assert Matter.objects.get(pk=first.pk).visibility == Matter.Visibility.HIDDEN
    assert Matter.objects.count() == 2
    assert list(
        MatterFacts.objects.filter(generation=result.generation).values_list("matter", flat=True)
    ) == [first.pk]


def test_chain_across_two_matters_needs_reconciliation() -> None:
    clock = MovingClock()
    activate(document("a", bill(RCL), bill(sejm("3100"))), expected=None, clock=clock)
    linked = document(
        "b",
        bill(RCL, status=BillStatus.LINKED),
        bill(sejm("3100")),
        relations=(IdentityRelation(source=RCL, target=sejm("3100"), kind="lifecycle"),),
    )
    with pytest.raises(ReconciliationRequiredError):
        activate(linked, expected=ref("a"), clock=clock)
    assert accepted_reference() == ref("a")


def test_events_are_revised_only_on_change_and_read_per_generation() -> None:
    clock = MovingClock()
    one = sejm("1")
    first = activate(document("a", bill(one), events=(event(one),)), expected=None, clock=clock)
    same = activate(document("b", bill(one), events=(event(one),)), expected=ref("a"), clock=clock)
    changed = activate(
        document("c", bill(one), events=(event(one, withdrawn=True),)),
        expected=ref("b"),
        clock=clock,
    )
    gone = activate(document("d", bill(one)), expected=ref("c"), clock=clock)

    matter = Matter.objects.get()
    assert MatterEvent.objects.count() == 1
    assert EventRevision.objects.count() == 2
    assert first.generation.counts["new_events"] == 1
    assert same.generation.counts["new_events"] == same.generation.counts["revised_events"] == 0
    assert changed.generation.counts["revised_events"] == 1
    assert [r.content["withdrawn"] for r in events_at(same.generation, matter.pk)] == [False]
    assert [r.content["withdrawn"] for r in events_at(gone.generation, matter.pk)] == [True]


def test_missing_row_is_counted_not_withdrawn() -> None:
    clock = MovingClock()
    first = activate(document("a", bill(sejm("1")), bill(sejm("2"))), expected=None, clock=clock)
    second = activate(
        document("b", bill(sejm("1"))), expected=ref("a"), clock=clock, accept_drop=True
    )

    assert second.generation.counts["absent_identities"] == 1
    assert Matter.objects.count() == 2
    assert MatterFacts.objects.filter(generation=first.generation).count() == 2
    assert active_generation() == second.generation


@pytest.mark.django_db(transaction=True)
def test_concurrent_activations_accept_exactly_one() -> None:
    def run(commit: str) -> str:
        try:
            imported = document(commit, bill(sejm("1" if commit == "a" else "2")))
            return activate(imported, expected=None, clock=MovingClock()).status
        except StaleActivationError:
            return "stale"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(run, ["a", "b"]))
    assert results == ["activated", "stale"]
    assert ImportGeneration.objects.count() == 1
    assert ActiveImport.objects.get().generation == ImportGeneration.objects.get()


def test_import_state_fetches_projects_and_activates(tmp_path: Path) -> None:
    repository = SqliteBillRepository(":memory:")
    repository.migrate()
    raw = json.loads((Path(__file__).parent / "fixtures/import_legacy.json").read_text())
    legacy = Bill.model_validate(raw["bill"])
    repository.upsert_summary(legacy.summary, now=legacy.first_seen_at)
    remote = tmp_path / "remote"
    remote.mkdir()
    (remote / "lexinform.sql").write_text(repository.dump())
    for arguments in (
        ("init", "--initial-branch=state", "--object-format=sha1"),
        ("add", "lexinform.sql"),
        ("-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-m", "Snapshot"),
    ):
        subprocess.run(["git", "-C", str(remote), *arguments], check=True, capture_output=True)
    repository.close()
    source = GitSnapshotSource(remote, tmp_path / "cache")

    activated = import_state(source, clock=MovingClock())
    assert isinstance(activated, Activation) and activated.status == "activated"
    assert import_state(source, clock=MovingClock()) == "unchanged"
    assert ImportGeneration.objects.count() == 1
    assert SourceIdentity.objects.count() == 1
