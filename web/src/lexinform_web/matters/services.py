from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.db.models import Q

from lexinform.ports import Clock
from lexinform_web.accounts.models import User
from lexinform_web.matters.identities import SourceKey
from lexinform_web.matters.models import (
    Matter,
    MatterDecision,
    MatterRelation,
    PublicAlias,
    SourceIdentity,
)
from lexinform_web.matters.selectors import canonical_matter


@contextmanager
def identity_transaction() -> Iterator[None]:
    with transaction.atomic():
        # Serialize graph mutations so concurrent opposite merges cannot create a cycle.
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [730241901])
        yield


def identify(key: SourceKey, clock: Clock, *, continues: UUID | None = None) -> Matter:
    with identity_transaction():
        existing = SourceIdentity.objects.filter(
            source=key.source, scope=key.scope, external_id=key.external_id
        ).first()
        target = canonical_matter(continues) if continues else None
        if existing:
            matter = canonical_matter(existing.matter_id)
            if target and matter.pk != target.pk:
                raise ValueError(
                    "identity already belongs to another matter; audited merge required"
                )
            return matter
        matter = target or Matter.objects.create(created_at=clock.now())
        SourceIdentity.objects.create(
            source=key.source, scope=key.scope, external_id=key.external_id, matter=matter
        )
        return matter


def relate(
    from_id: UUID, to_id: UUID, kind: MatterRelation.Kind, evidence_url: str
) -> MatterRelation:
    with identity_transaction():
        if from_id == to_id:
            raise ValueError("a matter cannot be related to itself")
        if kind in {MatterRelation.Kind.JOINT, MatterRelation.Kind.ALTERNATIVE}:
            from_id, to_id = sorted([from_id, to_id])
        relation = MatterRelation(
            from_matter_id=from_id, to_matter_id=to_id, kind=kind, evidence_url=evidence_url
        )
        relation.clean_fields()
        existing = MatterRelation.objects.filter(
            from_matter_id=from_id, to_matter_id=to_id, kind=kind
        ).first()
        if existing:
            return existing
        relation.save()
        return relation


def check_editor(actor: User, reason: str) -> None:
    if not actor.is_active or not actor.is_staff or not actor.has_perm("matters.reconcile_matter"):
        raise PermissionDenied("matter reconciliation permission required")
    if not reason.strip():
        raise ValueError("an editorial reason is required")


def merge(source_id: UUID, target_id: UUID, *, actor: User, reason: str, clock: Clock) -> Matter:
    check_editor(actor, reason)
    with identity_transaction():
        source = canonical_matter(source_id)
        target = canonical_matter(target_id)
        if source.pk == target.pk:
            raise ValueError("matters already resolve to the same identity")
        members = list(Matter.objects.filter(Q(pk=source.pk) | Q(canonical=source)).order_by("pk"))
        before = {str(m.pk): str(m.canonical_id) if m.canonical_id else None for m in members}
        Matter.objects.filter(pk__in=[m.pk for m in members]).update(canonical=target)
        MatterDecision.objects.create(
            kind=MatterDecision.Kind.MERGE,
            actor=actor,
            reason=reason,
            before={"canonical": before},
            after={"canonical": {str(m.pk): str(target.pk) for m in members}},
            created_at=clock.now(),
        )
        return target


def split(
    public_id: UUID,
    identity_ids: set[int],
    *,
    actor: User,
    reason: str,
    clock: Clock,
    restore_id: UUID | None = None,
) -> Matter:
    check_editor(actor, reason)
    with identity_transaction():
        original = canonical_matter(public_id)
        members = Matter.objects.filter(Q(pk=original.pk) | Q(canonical=original))
        identities = SourceIdentity.objects.filter(matter__in=members)
        selected = list(identities.filter(pk__in=identity_ids).order_by("pk"))
        if (
            not identity_ids
            or len(selected) != len(identity_ids)
            or len(selected) == identities.count()
        ):
            raise ValueError("split requires a proper nonempty subset of this matter's identities")
        if restore_id:
            restored = Matter.objects.get(pk=restore_id)
            owned = set(SourceIdentity.objects.filter(matter=restored).values_list("pk", flat=True))
            if restored.canonical_id != original.pk or not owned.issubset(identity_ids):
                raise ValueError("restore must include all identities of a merged public id")
            restored.canonical = None
            restored.save(update_fields=["canonical"])
        else:
            restored = Matter.objects.create(created_at=clock.now())
        before = {str(identity.pk): str(identity.matter_id) for identity in selected}
        SourceIdentity.objects.filter(pk__in=identity_ids).update(matter=restored)
        MatterDecision.objects.create(
            kind=MatterDecision.Kind.SPLIT,
            actor=actor,
            reason=reason,
            before={"identities": before, "canonical": str(original.pk)},
            after={
                "identities": {str(identity.pk): str(restored.pk) for identity in selected},
                "restored_public_id": str(restored.pk),
            },
            created_at=clock.now(),
        )
        return restored


def remember_path(path: str, public_id: UUID) -> PublicAlias:
    if not path.startswith("/") or path.startswith(("//", "/b/")) or "?" in path or "#" in path:
        raise ValueError("alias must be a local non-reserved path without query or fragment")
    with identity_transaction():
        Matter.objects.get(pk=public_id)
        alias, _ = PublicAlias.objects.get_or_create(path=path, defaults={"matter_id": public_id})
        if alias.matter_id != public_id:
            raise ValueError("public alias already belongs to another matter")
        return alias
