from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, connections, transaction

from lexinform_web.accounts.models import User
from lexinform_web.matters.identities import SourceKey
from lexinform_web.matters.models import (
    Matter,
    MatterDecision,
    MatterRelation,
    PublicAlias,
    SourceIdentity,
)
from lexinform_web.matters.selectors import canonical_matter, matter_for_path
from lexinform_web.matters.services import identify, merge, relate, remember_path, split

pytestmark = pytest.mark.django_db


@dataclass(frozen=True)
class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 27, tzinfo=UTC)


CLOCK = FixedClock()


def sejm(number: str, term: str = "10") -> SourceKey:
    return SourceKey(SourceIdentity.Source.SEJM, term, number)


@pytest.fixture
def reconciler() -> User:
    user = User.objects.create_user(
        username="reviewer", email="reviewer@example.com", is_staff=True
    )
    user.user_permissions.add(Permission.objects.get(codename="reconcile_matter"))
    return user


def test_source_lifecycle_keeps_uuid_and_repeat_is_idempotent() -> None:
    keys = [
        SourceKey(SourceIdentity.Source.WYKAZ, "kprm", "entry:2026-01-01:some-project"),
        SourceKey(SourceIdentity.Source.RCL, "global", "12405609"),
        SourceKey(SourceIdentity.Source.RPW, "global", "RPW/30695/2026"),
        sejm("2172"),
    ]
    matter = identify(keys[0], CLOCK)
    for key in keys[1:]:
        assert identify(key, CLOCK, continues=matter.pk).pk == matter.pk
        assert identify(key, CLOCK).pk == matter.pk
    assert Matter.objects.count() == 1
    assert SourceIdentity.objects.count() == 4
    assert matter.created_at == CLOCK.now()
    assert matter.visibility == Matter.Visibility.DRAFT


def test_rollover_and_joint_consideration_do_not_merge_matters() -> None:
    old = identify(sejm("123", "10"), CLOCK)
    new = identify(sejm("123", "11"), CLOCK)
    other = identify(sejm("124", "11"), CLOCK)
    relation = relate(
        old.pk, new.pk, MatterRelation.Kind.CONTINUATION, "https://api.sejm.gov.pl/sejm/term11"
    )
    joint = relate(
        new.pk, other.pk, MatterRelation.Kind.JOINT, "https://api.sejm.gov.pl/sejm/term11"
    )
    duplicate = relate(other.pk, new.pk, MatterRelation.Kind.JOINT, joint.evidence_url)
    assert relation.from_matter_id == old.pk
    assert duplicate.pk == joint.pk
    assert Matter.objects.filter(canonical__isnull=True).count() == 3
    assert old.pk != new.pk


def test_same_display_number_does_not_identify_two_wykaz_entries() -> None:
    first = identify(SourceKey(SourceIdentity.Source.WYKAZ, "kprm", "entry:2025:uc104"), CLOCK)
    second = identify(SourceKey(SourceIdentity.Source.WYKAZ, "kprm", "entry:2026:uc104"), CLOCK)
    assert first.pk != second.pk
    with pytest.raises(ValueError, match="display number"):
        SourceKey(SourceIdentity.Source.WYKAZ, "kprm", "UC104")


def test_identity_conflict_requires_editorial_decision() -> None:
    first = identify(sejm("101"), CLOCK)
    second = identify(sejm("102"), CLOCK)
    with pytest.raises(ValueError, match="audited merge"):
        identify(sejm("101"), CLOCK, continues=second.pk)
    assert identify(sejm("101"), CLOCK).pk == first.pk
    assert MatterDecision.objects.count() == 0


def test_merge_split_preserve_public_urls_and_audit(reconciler: User) -> None:
    first = identify(sejm("201"), CLOCK)
    second = identify(sejm("202"), CLOCK)
    remember_path("/ru/bills/old-title/", first.pk)
    identity = SourceIdentity.objects.get(matter=first)

    merge(first.pk, second.pk, actor=reconciler, reason="Confirmed continuation", clock=CLOCK)

    assert matter_for_path(first.get_absolute_url()).pk == second.pk
    assert matter_for_path("/ru/bills/old-title/").pk == second.pk
    assert identify(sejm("201"), CLOCK).pk == second.pk
    decision = MatterDecision.objects.get(kind="merge")
    assert decision.actor_id == reconciler.pk
    assert decision.before == {"canonical": {str(first.pk): None}}
    assert decision.created_at == CLOCK.now()

    restored = split(
        second.pk,
        {identity.pk},
        actor=reconciler,
        reason="Correct mistaken linkage",
        clock=CLOCK,
        restore_id=first.pk,
    )

    assert restored.pk == first.pk
    assert matter_for_path(first.get_absolute_url()).pk == first.pk
    assert matter_for_path("/ru/bills/old-title/").pk == first.pk
    assert matter_for_path(second.get_absolute_url()).pk == second.pk
    assert identify(sejm("201"), CLOCK).pk == first.pk
    assert Matter.objects.count() == 2
    assert SourceIdentity.objects.count() == 2
    assert MatterDecision.objects.count() == 2


def test_merge_flattens_aliases_and_rejects_cycles(reconciler: User) -> None:
    first, second, third = [identify(sejm(str(number)), CLOCK) for number in (301, 302, 303)]
    merge(first.pk, second.pk, actor=reconciler, reason="first", clock=CLOCK)
    merge(second.pk, third.pk, actor=reconciler, reason="second", clock=CLOCK)
    first.refresh_from_db()
    assert first.canonical_id == third.pk
    with pytest.raises(ValueError, match="same identity"):
        merge(third.pk, first.pk, actor=reconciler, reason="would cycle", clock=CLOCK)
    assert canonical_matter(first.pk).pk == third.pk
    assert MatterDecision.objects.count() == 2


def test_split_subset_creates_new_draft_and_rejects_unrelated_identity(reconciler: User) -> None:
    first = identify(sejm("401"), CLOCK)
    identify(sejm("402"), CLOCK, continues=first.pk)
    second_identity = SourceIdentity.objects.get(external_id="402")
    first.visibility = Matter.Visibility.HIDDEN
    first.save()
    with pytest.raises(ValueError, match="subset"):
        split(
            first.pk, {second_identity.pk, 999999}, actor=reconciler, reason="invalid", clock=CLOCK
        )
    separated = split(
        first.pk, {second_identity.pk}, actor=reconciler, reason="separate initiative", clock=CLOCK
    )
    assert separated.visibility == Matter.Visibility.DRAFT
    assert identify(sejm("402"), CLOCK).pk == separated.pk
    assert matter_for_path(first.get_absolute_url()).visibility == Matter.Visibility.HIDDEN


def test_permissions_reason_and_alias_ownership(reconciler: User) -> None:
    first = identify(sejm("501"), CLOCK)
    second = identify(sejm("502"), CLOCK)
    editor = User.objects.create_user(username="editor", email="editor@example.com", is_staff=True)
    with pytest.raises(PermissionDenied):
        merge(first.pk, second.pk, actor=editor, reason="no permission", clock=CLOCK)
    with pytest.raises(ValueError, match="reason"):
        merge(first.pk, second.pk, actor=reconciler, reason=" ", clock=CLOCK)
    remember_path("/ru/old/", first.pk)
    with pytest.raises(ValueError, match="already belongs"):
        remember_path("/ru/old/", second.pk)
    with pytest.raises(ValueError, match="reserved"):
        remember_path(second.get_absolute_url(), first.pk)
    assert PublicAlias.objects.count() == 1
    assert MatterDecision.objects.count() == 0


def test_postgresql_enforces_natural_keys_and_nonempty_scope() -> None:
    matter = identify(sejm("601"), CLOCK)
    with pytest.raises(IntegrityError), transaction.atomic():
        SourceIdentity.objects.create(source="sejm", scope="10", external_id="601", matter=matter)
    with pytest.raises(IntegrityError), transaction.atomic():
        SourceIdentity.objects.create(source="rcl", scope="", external_id="12", matter=matter)
    with pytest.raises(IntegrityError), transaction.atomic():
        Matter.objects.filter(pk=matter.pk).update(canonical=matter)


def test_failed_transaction_rolls_back_merge_and_its_audit(reconciler: User) -> None:
    first = identify(sejm("801"), CLOCK)
    second = identify(sejm("802"), CLOCK)
    with pytest.raises(RuntimeError), transaction.atomic():
        merge(first.pk, second.pk, actor=reconciler, reason="rolled back", clock=CLOCK)
        raise RuntimeError("abort whole operation")
    assert canonical_matter(first.pk).pk == first.pk
    assert MatterDecision.objects.count() == 0


def test_partial_restore_cannot_move_unselected_identities(reconciler: User) -> None:
    first = identify(sejm("901"), CLOCK)
    identify(sejm("902"), CLOCK, continues=first.pk)
    second = identify(sejm("903"), CLOCK)
    merge(first.pk, second.pk, actor=reconciler, reason="merge", clock=CLOCK)
    selected = SourceIdentity.objects.get(external_id="901")
    with pytest.raises(ValueError, match="all identities"):
        split(
            second.pk,
            {selected.pk},
            actor=reconciler,
            reason="partial restore",
            clock=CLOCK,
            restore_id=first.pk,
        )
    assert canonical_matter(first.pk).pk == second.pk
    assert identify(sejm("902"), CLOCK).pk == second.pk
    assert MatterDecision.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_discovery_creates_one_identity() -> None:
    def discover(_: int) -> UUID:
        try:
            return identify(sejm("701"), CLOCK).pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(discover, range(4)))
    assert len(set(ids)) == 1
    assert Matter.objects.count() == 1
    assert SourceIdentity.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_opposite_merges_cannot_create_cycle(reconciler: User) -> None:
    first = identify(sejm("1001"), CLOCK)
    second = identify(sejm("1002"), CLOCK)

    def reconcile(pair: tuple[UUID, UUID]) -> bool:
        try:
            actor = User.objects.get(pk=reconciler.pk)
            merge(*pair, actor=actor, reason="concurrent decision", clock=CLOCK)
            return True
        except ValueError:
            return False
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reconcile, [(first.pk, second.pk), (second.pk, first.pk)]))
    assert sum(results) == 1
    assert canonical_matter(first.pk).pk == canonical_matter(second.pk).pk
    assert MatterDecision.objects.count() == 1
