"Join projection receipts to frozen physical input references, never current Bill contents."

import sqlite3
from datetime import datetime
from typing import Any

from lexinform.adapters.content_applications import verify_content_applications
from lexinform.adapters.content_history import read_content_version, verify_card_content
from lexinform.adapters.content_source_refs import ContentSourceRows
from lexinform.adapters.secondary_dependencies import (
    ProjectedSecondaryRecord,
    SecondaryPurpose,
    validate_secondary_dependency,
)
from lexinform.json_storage import read_json
from lexinform.models import Bill, ComputationResult, DeliveryPlan, ResultApplication, StatusChange
from lexinform.models.batch import BatchKind
from lexinform.models.commands import DeliveryResolution
from lexinform.name_binding import mnemonic_matches_result
from lexinform.saved_clock import saved_clock_age

_REQUIRED = {
    "id",
    "term",
    "number",
    "kind",
    "status",
    "channel_id",
    "message_id",
    "status_change_id",
    "sent_at",
    "delivery_json",
    "ref",
}
_ERRORS = (ValueError, TypeError, RecursionError, sqlite3.Error, KeyError, IndexError)


def _rows(connection: sqlite3.Connection, sql: str) -> list[sqlite3.Row]:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        return cursor.execute(sql).fetchall()
    finally:
        cursor.close()


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


class TimingDeliveryLinks:
    def __init__(self, connection: sqlite3.Connection, observed_at: datetime) -> None:
        self.observed_at = observed_at
        self.connection = connection
        self.available = _columns(connection, "publications") >= _REQUIRED
        self.missing = [] if self.available else ["publications"]
        self.publications = (
            {row["id"]: row for row in _rows(connection, "SELECT * FROM publications ORDER BY id")}
            if self.available
            else {}
        )
        self.plans: dict[int, DeliveryPlan] = {}
        for identity, row in self.publications.items():
            try:
                if type(identity) is int and identity > 0:
                    self.plans[identity] = DeliveryPlan.model_validate(
                        read_json(row["delivery_json"])
                    )
            except _ERRORS:
                continue
        self.operation_plans = dict(self.plans)
        self.resolutions_available = {"publication_id", "resolution_json"} <= _columns(
            connection, "delivery_resolutions"
        )
        if not self.resolutions_available:
            self.missing.append("delivery_resolutions")
        self.manual: set[int] = set()
        self.resolution_invalid = False
        if self.resolutions_available:
            for row in _rows(
                connection, "SELECT publication_id,resolution_json FROM delivery_resolutions"
            ):
                try:
                    resolution = DeliveryResolution.model_validate(
                        read_json(row["resolution_json"])
                    )
                    if resolution.publication_id != row["publication_id"]:
                        raise ValueError("resolution_identity_mismatch")
                    if resolution.action == "confirm":
                        self.manual.update((resolution.publication_id, *resolution.publication_ids))
                except _ERRORS:
                    self.resolution_invalid = True
        self.digest_available = {"source_publication_id", "owner_publication_id"} <= _columns(
            connection, "digest_event_links"
        )
        if not self.digest_available:
            self.missing.append("digest_event_links")
        self.digests = (
            {
                row[0]: row[1]
                for row in connection.execute(
                    "SELECT source_publication_id,owner_publication_id FROM digest_event_links"
                )
            }
            if self.digest_available
            else {}
        )

    @staticmethod
    def policy(purpose: BatchKind) -> str:
        return {
            "analysis": "frozen_analysis_and_projected_generation",
            "reanalysis": "frozen_changed_analysis_and_projected_generation",
            "explanation": "no_durable_operator_reply_receipt",
            "amendments": "exact_frozen_change_or_profiled_revision_reference",
            "supplement": "exact_frozen_change_or_profiled_reference",
            "joint": "joint_bill_frozen_record",
            "name": "selected_bound_mnemonic_in_card_operation",
            "consultation_review": "own_review_kind_and_frozen_source_key",
            "commission_review": "own_review_kind_and_frozen_source_key",
            "act_application": "own_review_kind_and_frozen_source_key",
        }[purpose]

    def _record(
        self,
        record: ProjectedSecondaryRecord | None,
        result: ComputationResult,
        purpose: SecondaryPurpose,
    ) -> bool:
        if record is None or record.computation_id != result.result_id:
            return False
        validate_secondary_dependency(self.connection, record, purpose)
        return record.request_sha256 is not None and record.request_sha256 == result.request_sha256

    def _matches(
        self, identity: int, application: ResultApplication, result: ComputationResult
    ) -> bool:
        row, plan = self.publications[identity], self.plans[identity]
        if plan.update_group:
            members = [item for item in plan.update_group if item.publication_id == identity]
            if len(members) != 1:
                return False
            plan = plan.model_copy(
                update={"bill_json": members[0].bill_json, "change_json": members[0].change_json}
            )
        frozen = Bill.model_validate(read_json(plan.bill_json))
        if (application.term, application.number) != (frozen.term, frozen.number):
            return False
        target, purpose = application.target, result.purpose
        kind = row["kind"]
        change = (
            StatusChange.model_validate(read_json(plan.change_json)) if plan.change_json else None
        )
        if change is not None and (change.term, change.number) != (
            application.term,
            application.number,
        ):
            return False
        if purpose in ("analysis", "reanalysis"):
            record = frozen.analysis
            return (
                target is None
                and application.projected_generation is not None
                and application.projected_generation == frozen.analysis_generation
                and (
                    kind == "new_bill"
                    or purpose == "reanalysis"
                    and kind == "status_update"
                    and change is not None
                    and change.content_changed
                )
                and record is not None
                and record.computation_id == result.result_id
                and record.request_sha256 is not None
                and record.request_sha256 == result.request_sha256
            )
        if target is None:
            return False
        if purpose in ("amendments", "supplement"):
            if change is None:
                return False
            if target.kind == "status_change":
                if (
                    kind != "status_update"
                    or target.ref != str(change.id)
                    or row["status_change_id"] != change.id
                ):
                    return False
            elif target.kind == "publication":
                if (
                    purpose != "amendments"
                    or kind != "amendments_revision"
                    or target.ref != str(identity)
                    or row["status_change_id"] not in (None, change.id)
                ):
                    return False
            else:
                return False
            records = [change.amendments] if purpose == "amendments" else change.supplements
            return any(self._record(record, result, purpose) for record in records)
        if purpose == "joint":
            return (
                target.kind == "bill"
                and target.ref == "joint"
                and kind == "joint_bill"
                and self._record(frozen.joint, result, "joint")
            )
        if purpose == "name":
            card_edit = (
                kind == "message_part"
                and plan.revision_of is not None
                and plan.edit_message_id is not None
                and plan.continuation_index == 1
                and row["ref"].startswith(f"card/{plan.revision_of}/")
            )
            return (
                target.kind == "editorial"
                and target.ref == "mnemonic"
                and (kind == "new_bill" or card_edit)
                and frozen.editorial.mnemonic is not None
                and mnemonic_matches_result(frozen.editorial.mnemonic, result)
                and self._card_language(identity) == frozen.editorial.mnemonic.language
            )
        if purpose in ("consultation_review", "commission_review", "act_application"):
            review = {
                "consultation_review": frozen.consultation_review,
                "commission_review": frozen.commission_review,
                "act_application": frozen.act_application_review,
            }[purpose]
            kinds = (
                ("act_application", "effective_rules")
                if purpose == "act_application"
                else (purpose,)
            )
            return (
                kind in kinds
                and target.kind == "document_review"
                and review is not None
                and bool(review.source_key)
                and target.ref == f"{purpose}/{review.source_key}"
                and self._record(review, result, purpose)
            )
        return False

    def _versions(self) -> list[sqlite3.Row]:
        if not {"publication_id", "content_id"} <= _columns(
            self.connection, "publication_content"
        ) or not {"content_id", "version_json"} <= _columns(self.connection, "content_versions"):
            return []
        return _rows(
            self.connection,
            (
                "SELECT p.publication_id,v.* FROM publication_content p JOIN content_versions v "
                "ON v.content_id=p.content_id"
            ),
        )

    def _card_language(self, identity: int) -> str | None:
        for row in self._versions():
            if row["publication_id"] != identity:
                continue
            version = read_content_version(row)
            if (
                version.card_context_profile == "continuation-v1"
                and version.formatter_version in {"telegram-format-v2", "telegram-format-v3"}
                and verify_card_content(self.connection, version, retained=True) == identity
            ):
                return version.language
        return None

    def _profiled(
        self, application_id: int, application: ResultApplication, result: ComputationResult
    ) -> set[int]:
        if result.purpose not in ("amendments", "supplement", "name"):
            return set()
        found = set()
        for row in self._versions():
            try:
                version = read_content_version(row)
                identity = row["publication_id"]
                if type(identity) is not int or identity not in self.publications:
                    continue
                if result.purpose == "name":
                    if (
                        application.target is None
                        or application.target.kind != "editorial"
                        or application.target.ref != "mnemonic"
                        or self._card_language(identity) is None
                    ):
                        continue
                    snapshot = ContentSourceRows(self.connection).get_content_snapshot(
                        version.bill_context_id
                    )
                    if snapshot is None:
                        continue
                    frozen = Bill.model_validate(read_json(snapshot.payload_json))
                    selected = frozen.editorial.mnemonic
                    if (
                        (frozen.term, frozen.number) == (application.term, application.number)
                        and selected is not None
                        and mnemonic_matches_result(selected, result)
                        and version.language == selected.language
                    ):
                        found.add(identity)
                elif (
                    version.application_ref_profile == "amendment-projections-v1"
                    and application_id in version.application_ids
                ):
                    verify_content_applications(
                        self.connection, version, publication_id=identity, retained=True
                    )
                    found.add(identity)
                if identity in found:
                    snapshot = ContentSourceRows(self.connection).get_content_snapshot(
                        version.plan_snapshot_id
                    )
                    if snapshot is not None and snapshot.kind == "plan":
                        self.operation_plans[identity] = DeliveryPlan.model_validate(
                            read_json(snapshot.payload_json)
                        )
            except _ERRORS:
                continue
        return found

    def _owner(self, identity: int) -> tuple[int | None, str | None]:
        row, plan = self.publications[identity], self.operation_plans.get(identity)
        if plan is None:
            return identity, None
        digest = self.digests.get(identity)
        absorbed = plan.absorbed_by
        if digest is not None or absorbed is not None:
            owner = digest if digest is not None else absorbed
            if owner not in self.publications or owner not in self.plans:
                return None, "physical_owner_unavailable"
            parent = self.plans[owner]
            if self.publications[owner]["kind"] != (
                "digest" if digest is not None else "status_update"
            ):
                return None, "physical_owner_kind_unproven"
            members = parent.digest_event_ids if digest is not None else parent.held_change_ids
            member_identity = identity if digest is not None else row["status_change_id"]
            if member_identity not in members:
                return None, "physical_membership_unproven"
            return owner, "digest_event" if digest is not None else "absorbed_event"
        if plan.update_group:
            owners = [
                key
                for key, candidate in self.plans.items()
                if candidate.update_group
                and candidate.publication_ids[0] == key
                and identity in candidate.publication_ids
            ]
            if len(owners) != 1:
                return None, "group_membership_ambiguous"
            owner = owners[0]
            snapshots = [
                item for item in self.plans[owner].update_group if item.publication_id == identity
            ]
            if len(snapshots) != 1 or self.plans[owner].update_group != plan.update_group:
                return None, "group_snapshot_unproven"
            return owner, "update_group"
        return (
            (None, "digest_membership_unproven")
            if plan.destination != "direct"
            else (identity, "direct")
        )

    def _operation(self, identity: int) -> dict[str, Any]:
        owner, topology = self._owner(identity)
        base: dict[str, Any] = {
            "publication_id": identity,
            "physical_publication_id": owner,
            "topology": topology,
            "endpoint": None,
            "status": "unknown",
            "reason": topology if owner is None else "not_confirmed",
        }
        if owner is None:
            return base
        row, plan = self.publications[owner], self.operation_plans.get(owner)
        if plan is None:
            return {**base, "reason": "physical_payload_unavailable"}
        if not self.digest_available:
            return {**base, "reason": "digest_inventory_unavailable"}
        identities = (owner, *plan.continuation_ids)
        if len(set(identities)) != len(identities):
            return {**base, "reason": "multipart_membership_unproven"}
        group_ids = plan.publication_ids if plan.update_group else ()
        for group_id in group_ids:
            member = self.publications.get(group_id)
            member_plan = self.plans.get(group_id)
            if (
                member is None
                or member_plan is None
                or member_plan.update_group != plan.update_group
                or any(
                    member[field] != row[field]
                    for field in ("status", "channel_id", "message_id", "sent_at")
                )
            ):
                return {**base, "reason": "group_receipt_unproven"}
            if group_id in self.manual:
                return {**base, "reason": "manual_confirmation_not_send_clock"}
        previous = row
        for index, member_id in enumerate(identities):
            member = self.publications.get(member_id)
            member_plan = self.operation_plans.get(member_id)
            if (
                member is None
                or member_plan is None
                or member["status"] != "sent"
                or type(member["message_id"]) is not int
                or member["message_id"] <= 0
                or not member["channel_id"]
                or member["channel_id"] != row["channel_id"]
            ):
                return base
            if member_id in self.manual:
                return {**base, "reason": "manual_confirmation_not_send_clock"}
            if index and (
                member["kind"] != "message_part"
                or member_plan.continuation_root != owner
                or member_plan.continuation_parent != identities[index - 1]
                or member_plan.continuation_index != index + 1
                or member_plan.reply_to != previous["message_id"]
            ):
                return {**base, "reason": "multipart_membership_unproven"}
            if (
                member_plan.edit_message_id is not None
                and member_plan.edit_message_id != member["message_id"]
            ):
                return {**base, "reason": "edit_target_mismatch"}
            age = saved_clock_age(member["sent_at"], self.observed_at)
            if age.unknown:
                return {**base, "reason": f"operation_{age.unknown}"}
            if (
                index
                and saved_clock_age(
                    previous["sent_at"], datetime.fromisoformat(member["sent_at"])
                ).unknown
            ):
                return {**base, "reason": "multipart_clock_order_unproven"}
            previous = member
        if not self.resolutions_available or self.resolution_invalid:
            return {**base, "reason": "manual_resolution_inventory_unavailable"}
        if identity != owner:
            source = self.publications[identity]
            if any(
                source[field] != row[field]
                for field in ("status", "channel_id", "message_id", "sent_at")
            ):
                return {**base, "reason": "member_receipt_mismatch"}
        if not plan.edit_message_id and any(
            other["id"] not in (*identities, *group_ids)
            and other["message_id"] == row["message_id"]
            and other["channel_id"] == row["channel_id"]
            and self.operation_plans.get(other["id"], plan).edit_message_id is None
            and other["id"] != identity
            for other in self.publications.values()
        ):
            return {**base, "reason": "shared_message_membership_unproven"}
        return {
            **base,
            "status": "recorded_operation",
            "reason": "exact_frozen_reference",
            "operation": "edit" if plan.edit_message_id else "send",
            "endpoint": previous["sent_at"],
            "parts": len(identities),
            "evidence_scope": "frozen_input_reference_and_recorded_operation",
        }

    def for_application(
        self, identity: int, application: ResultApplication, result: ComputationResult
    ) -> tuple[list[dict[str, Any]], str]:
        if not self.available:
            return [], "physical_schema_unavailable"
        if result.validation != "validated":
            return [], "result_not_validated"
        if result.purpose == "explanation":
            return [], self.policy("explanation")
        profiled = self._profiled(identity, application, result)
        matched = set(profiled)
        invalid = False
        for publication_id in self.plans:
            try:
                if self._matches(publication_id, application, result):
                    matched.add(publication_id)
            except _ERRORS:
                invalid = True
        links = [
            {
                **self._operation(key),
                "association": "retained_selected_name"
                if key in profiled and result.purpose == "name"
                else "profiled_application_identity"
                if key in profiled
                else "known_request_frozen_slot",
            }
            for key in sorted(matched)
        ]
        return (
            links,
            "exact_frozen_reference"
            if links
            else "frozen_evidence_invalid_or_unavailable"
            if invalid
            else "frozen_reference_unavailable",
        )
