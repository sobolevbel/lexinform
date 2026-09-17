import datetime as dt

from pydantic import BaseModel, ConfigDict

from lexinform.models.bill import Bill, StatusChange
from lexinform.models.events import fills_in_the_past, has_news
from lexinform.models.evidence import decision_changes
from lexinform.models.observations import ObservedProcess
from lexinform.models.sejm import ProcessDetail, Stage, TextDocument, diff_stages


class BillPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    previous: ObservedProcess | None
    observed: ObservedProcess
    analysis_document: TextDocument | None
    new_stages: tuple[Stage, ...]
    closure_detected: bool
    content_changed: bool
    decision_changed: bool = False

    def should_publish(self, change: StatusChange, *, act_published: bool = False) -> bool:
        known = self.previous.stages if self.previous is not None else ()
        return has_news(change, act_published=act_published) and (
            self.decision_changed or not fills_in_the_past(known, self.observed.stages)
        )


def observation_of(bill: Bill) -> ObservedProcess | None:
    if bill.observed_process is not None:
        return bill.observed_process
    if bill.stages_fingerprint is None:
        return None
    return observe(bill, closure_date=bill.observed_closure_date)


def observe(bill: Bill, *, closure_date: dt.date | None) -> ObservedProcess:
    record = bill.analysis
    document = (
        TextDocument(url=record.source_url, kind=record.source_kind)
        if record is not None and record.source_url is not None
        else None
    )
    return ObservedProcess(
        stages=bill.stages,
        closure_date=closure_date,
        passed=bill.summary.passed,
        document=document,
        analysis_sha256=record.text_sha256 if record is not None else None,
        analysis_revision=record.revision if record is not None else 0,
        seen_supplements=bill.seen_supplements,
    )


def plan_bill(
    bill: Bill,
    detail: ProcessDetail,
    *,
    analysis_document: TextDocument | None,
    closure_announced: bool,
) -> BillPlan:
    previous = observation_of(bill)
    fresh = bill.model_copy(update={"summary": detail, "stages": detail.stages})
    observed = observe(fresh, closure_date=detail.closure_date)
    added = diff_stages(previous.stages, observed.stages) if previous else []
    decisions = decision_changes(previous.stages, observed.stages) if previous else ()
    added.extend(stage for stage in decisions if stage not in added)
    return BillPlan(
        previous=previous,
        observed=observed,
        analysis_document=analysis_document,
        new_stages=tuple(added),
        decision_changed=bool(decisions),
        closure_detected=(
            previous is not None
            and observed.closure_date is not None
            and observed.closure_date != previous.closure_date
            and not closure_announced
        ),
        content_changed=(
            previous is not None
            and observed.analysis_revision != previous.analysis_revision
            and observed.analysis_sha256 != previous.analysis_sha256
        ),
    )
