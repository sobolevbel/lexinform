"""Validate immutable digest parts against exact retained physical owners."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from lexinform.adapters.content_source_refs import ContentSourceRows
from lexinform.json_storage import read_json
from lexinform.models import DIGEST_NUMBER, DIGEST_TERM, ContentVersion, PublicationStatus
from lexinform.models.digest_content import (
    DigestContentPlan,
    DigestRenderedPart,
    DigestRenderedPlan,
    digest_event_owner_sha256,
)
from lexinform.models.observations import DeliveryPlan


@dataclass(frozen=True)
class ResolvedDigestRenderedContent:
    plan: DigestRenderedPlan
    parts: tuple[DigestRenderedPart, ...]


def _rendered_parts(
    reader: ContentSourceRows, plan: DigestRenderedPlan
) -> tuple[DigestRenderedPart, ...]:
    parts = []
    for ref in plan.parts:
        snapshot = reader.get_content_snapshot(ref.snapshot_id)
        if snapshot is None or snapshot.kind != "plan":
            raise ValueError("digest_rendered_part_snapshot_missing")
        part = DigestRenderedPart.model_validate_json(snapshot.payload_json)
        if (
            part.publication_id != ref.publication_id
            or part.model_dump_json() != snapshot.payload_json
        ):
            raise ValueError("digest_rendered_part_snapshot_identity")
        parts.append(part)
    if any(part.approve_label for part in parts[:-1]):
        raise ValueError("digest rendered approval belongs only to the last part")
    ids = {part.publication_id for part in parts}
    events = [event for part in parts for event in part.event_ids]
    if len(events) != len(set(events)) or ids.intersection(events):
        raise ValueError("digest_rendered_duplicate_event_owner")
    return tuple(parts)


def verify_digest_content(
    connection: sqlite3.Connection, version: ContentVersion, *, require_bound: bool = False
) -> DigestContentPlan | ResolvedDigestRenderedContent | None:
    if version.digest_profile is None:
        return None
    reader = ContentSourceRows(connection)
    snapshot = reader.get_content_snapshot(version.plan_snapshot_id)
    if snapshot is None or snapshot.kind != "plan":
        raise ValueError("digest_content_snapshot_missing")
    if version.digest_profile == "digest-rendered-v1":
        rendered = DigestRenderedPlan.model_validate_json(snapshot.payload_json)
        if snapshot.payload_json != rendered.model_dump_json():
            raise ValueError("digest_rendered_plan_serialization")
        resolved: DigestContentPlan | ResolvedDigestRenderedContent = ResolvedDigestRenderedContent(
            rendered, _rendered_parts(reader, rendered)
        )
        channel_id, delivery_ref, week_ref = (
            rendered.channel_id,
            rendered.delivery_ref,
            rendered.week_ref,
        )
    else:
        plan = DigestContentPlan.model_validate(read_json(snapshot.payload_json))
        if snapshot.payload_json != plan.model_dump_json():
            raise ValueError("digest_content_plan_serialization")
        resolved = plan
        channel_id, delivery_ref, week_ref = plan.channel_id, plan.delivery_ref, plan.digest.ref
    identities = tuple(part.publication_id for part in resolved.parts)
    if version.root_publication_id != identities[0]:
        raise ValueError("digest_content_root_mismatch")
    receipts: list[int] = []
    previous_created = version.recorded_at
    for index, part in enumerate(resolved.parts):
        cursor = connection.execute("SELECT * FROM publications WHERE id=?", (part.publication_id,))
        row = cursor.fetchone()
        if row is None:
            raise ValueError("digest_content_part_missing")
        columns = {name[0]: value for name, value in zip(cursor.description, row, strict=True)}
        created = datetime.fromisoformat(columns["created_at"])
        if (
            (columns["term"], columns["number"], columns["kind"], columns["channel_id"])
            != (DIGEST_TERM, DIGEST_NUMBER, "digest", channel_id)
            or columns["ref"]
            != (delivery_ref if index == 0 else f"{delivery_ref}:part:{index + 1}")
            or created.utcoffset() is None
            or (index == 0 and created != version.recorded_at)
            or created < previous_created
            or columns["status_change_id"] is not None
            or columns["document_message_ids"] not in (None, "[]")
        ):
            raise ValueError("digest_content_part_owner")
        previous_created = created
        status = PublicationStatus(columns["status"])
        if status is PublicationStatus.SENT:
            message = columns["message_id"]
            if type(message) is not int or message <= 0 or not columns["sent_at"]:
                raise ValueError("digest_content_receipt")
            sent = datetime.fromisoformat(columns["sent_at"])
            if sent.utcoffset() is None:
                raise ValueError("digest_content_receipt_time")
            receipts.append(message)
        elif (
            status
            not in (
                PublicationStatus.QUEUED,
                PublicationStatus.PENDING,
                PublicationStatus.UNKNOWN,
                PublicationStatus.FAILED,
                PublicationStatus.DISMISSED,
            )
            or columns["message_id"] is not None
            or columns["sent_at"] is not None
        ):
            raise ValueError("digest_content_uncertain_receipt")
        raw = columns["delivery_json"]
        if raw is not None:
            delivery = DeliveryPlan.model_validate(read_json(raw))
            if (
                delivery.rendered_text != part.text
                or delivery.digest_event_ids != part.event_ids
                or delivery.approve_label != part.approve_label
                or delivery.digest_ref != week_ref
                or delivery.digest_parts != (identities if index == 0 else ())
                or delivery.revision_of is not None
                or delivery.edit_message_id is not None
                or delivery.bill_json
                or delivery.change_json
            ):
                raise ValueError("digest_content_frozen_part")
        elif status not in (PublicationStatus.SENT, PublicationStatus.DISMISSED):
            raise ValueError("digest_content_unretained_pending_part")
        events = connection.execute(
            "SELECT source_publication_id FROM digest_event_links WHERE owner_publication_id=?"
            " ORDER BY source_publication_id",
            (part.publication_id,),
        ).fetchall()
        if [row[0] for row in events] != sorted(part.event_ids):
            raise ValueError("digest_content_event_binding")
        owners = []
        for event in part.event_ids:
            owner = connection.execute(
                "SELECT channel_id,kind,term,number FROM publications WHERE id=?", (event,)
            ).fetchone()
            if owner is None or owner[0] != channel_id or owner[1] == "digest":
                raise ValueError("digest_content_event_owner")
            if isinstance(resolved, DigestContentPlan):
                entries = [
                    entry
                    for entry in (
                        *resolved.digest.cards,
                        *resolved.digest.updates,
                        *resolved.digest.highlights,
                    )
                    if event in entry.source_publication_ids
                ]
                if not entries or any(
                    (entry.term, entry.number, entry.kind.value) != (owner[2], owner[3], owner[1])
                    for entry in entries
                ):
                    raise ValueError("digest_content_event_owner")
            owners.append((event, owner[2], owner[3], owner[1]))
        if isinstance(
            part, DigestRenderedPart
        ) and part.event_owner_sha256 != digest_event_owner_sha256(owners):
            raise ValueError("digest_rendered_event_owner_hash")
    if len(set(receipts)) != len(receipts):
        raise ValueError("digest_content_duplicate_receipt")
    bindings = connection.execute(
        "SELECT publication_id FROM publication_content WHERE content_id=?", (version.content_id,)
    ).fetchall()
    if (bindings or require_bound) and {row[0] for row in bindings} != set(identities):
        raise ValueError("digest_content_physical_binding")
    return resolved
