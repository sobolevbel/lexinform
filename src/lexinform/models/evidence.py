import hashlib
import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from lexinform.models.enums import VetoOutcome
from lexinform.models.sejm import Stage, reading_decision, senate_moved_rejection


class DecisionState(StrEnum):
    ABSENT = "absent"
    PENDING = "pending"
    UNKNOWN = "unknown"
    KNOWN = "known"


class SenateOutcome(StrEnum):
    NO_AMENDMENTS = "no_amendments"
    AMENDMENTS = "amendments"
    REJECTION = "rejection"
    CONSIDERED = "considered"
    REJECTION_ACCEPTED = "rejection_accepted"


class DecisionEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: DecisionState
    stage: Stage | None = None
    veto: VetoOutcome | None = None
    senate: SenateOutcome | None = None


def veto_evidence(stages: tuple[Stage, ...]) -> DecisionEvidence:
    evidence = DecisionEvidence(state=DecisionState.ABSENT)
    for stage in stages:
        if stage.stage_type == "Veto":
            evidence = DecisionEvidence(
                state=DecisionState.PENDING, stage=stage, veto=VetoOutcome.PENDING
            )
        elif stage.stage_type == "PresidentMotionConsideration":
            outcome = veto_decision(stage)
            evidence = DecisionEvidence(
                state=DecisionState.UNKNOWN
                if outcome is VetoOutcome.PENDING
                else DecisionState.KNOWN,
                stage=stage,
                veto=outcome,
            )
        elif end_names_veto_sustained(stage):
            evidence = DecisionEvidence(
                state=DecisionState.KNOWN, stage=stage, veto=VetoOutcome.SUSTAINED
            )
    return evidence


def end_names_veto_sustained(stage: Stage) -> bool:
    return stage.stage_type == "End" and "nie uchwalona ponownie" in stage.stage_name.lower()


def veto_decision(stage: Stage) -> VetoOutcome:
    decision = (stage.decision or "").strip().lower()
    if "nie uchwalon" in decision:
        return VetoOutcome.SUSTAINED
    if decision.startswith("uchwalono ponownie"):
        return VetoOutcome.OVERRIDDEN
    return VetoOutcome.PENDING


def senate_evidence(stage: Stage) -> DecisionEvidence:
    outcome = None
    if stage.stage_type == "SenatePosition":
        position = (stage.position or "").strip().lower()
        if "nie wniósł" in position:
            outcome = SenateOutcome.NO_AMENDMENTS
        elif senate_moved_rejection(stage):
            outcome = SenateOutcome.REJECTION
        elif "popraw" in position:
            outcome = SenateOutcome.AMENDMENTS
    elif stage.stage_type == "SenatePositionConsideration":
        decision = (stage.decision or "").strip().lower()
        if decision.startswith("przyjęto") and "uchwałę senatu" in decision:
            outcome = SenateOutcome.REJECTION_ACCEPTED
        elif decision.startswith(("przyjęto", "odrzucono")):
            outcome = SenateOutcome.CONSIDERED
    return DecisionEvidence(
        state=DecisionState.KNOWN if outcome is not None else DecisionState.UNKNOWN,
        stage=stage,
        senate=outcome,
    )


def terminal_stage(stages: tuple[Stage, ...]) -> Stage | None:
    veto = veto_evidence(stages)
    if veto.veto is VetoOutcome.SUSTAINED:
        return veto.stage
    for stage in reversed(stages):
        if stage.stage_type == "SejmReading" and "odrzuc" in reading_decision(stage):
            return stage
        if (
            stage.stage_type == "SenatePositionConsideration"
            and senate_evidence(stage).senate is SenateOutcome.REJECTION_ACCEPTED
        ):
            return stage
    return None


def decision_changes(known: tuple[Stage, ...], found: tuple[Stage, ...]) -> tuple[Stage, ...]:
    previous = {
        (stage.stage_type, stage.date, stage.print_number): senate_evidence(stage).senate
        for stage in known
        if stage.stage_type == "SenatePosition"
    }
    return tuple(
        stage
        for stage in found
        if stage.stage_type == "SenatePosition"
        and (stage.stage_type, stage.date, stage.print_number) in previous
        and senate_evidence(stage).senate is not None
        and senate_evidence(stage).senate
        != previous[(stage.stage_type, stage.date, stage.print_number)]
    )


def decision_fingerprint(stages: tuple[Stage, ...]) -> str:
    facts = [
        (
            stage.stage_type,
            stage.date.isoformat() if stage.date else None,
            stage.print_number,
            senate_evidence(stage).senate,
        )
        for stage in stages
        if stage.stage_type == "SenatePosition"
    ]
    return hashlib.sha256(json.dumps(facts).encode()).hexdigest()
