import logging

from lexinform.errors import ServiceUnavailableError
from lexinform.models import Bill, BillStatus, BillSubmission, rcl_number
from lexinform.ports import BillRepository, ProjectResolver

log = logging.getLogger(__name__)

NOT_FOLLOWED = frozenset(
    {
        BillStatus.SKIPPED_PREFILTER,
        BillStatus.SKIPPED_TEXT_PREFILTER,
        BillStatus.SKIPPED_CLOSED,
        BillStatus.LINKED,
    }
)


def rcl_predecessor(
    repo: BillRepository, projects: ProjectResolver | None, rm_number: str | None
) -> Bill | None:
    """A print remains discoverable when its optional RCL predecessor cannot be resolved."""
    if not rm_number:
        return None
    bill = repo.find_by_rm_number(rm_number)
    if bill is None and projects is not None:
        try:
            project_id = projects.resolve_project_id(rm_number)
        except ServiceUnavailableError as exc:
            log.warning("RCL lookup of %s skipped: %s", rm_number, exc.describe())
            return None
        except Exception as exc:
            log.warning("RCL lookup of %s failed: %s", rm_number, exc)
            return None
        if project_id is not None:
            bill = repo.find_rcl(rcl_number(project_id))
    if bill is None or bill.rcl is None or bill.status in NOT_FOLLOWED:
        return None
    return bill


def absorb_submission(
    repo: BillRepository, projects: ProjectResolver | None, sub: BillSubmission
) -> Bill | None:
    """The RCL row a government `/bills` entry continues, which keeps the entry as its own."""
    if not sub.senders_number:
        return None
    stored = repo.find_by_rm_number(sub.senders_number)
    if stored is not None and stored.status is BillStatus.LINKED:
        return stored
    project = rcl_predecessor(repo, projects, sub.senders_number)
    if project is None:
        return None
    assert project.rcl is not None, "rcl_predecessor returns only a row with its project"
    repo.save_submission(project.term, project.number, sub)
    if project.rcl.rm_number != sub.senders_number:
        rcl = project.rcl.model_copy(update={"rm_number": sub.senders_number})
        repo.save_rcl(project.term, project.number, rcl)
    fresh = repo.get(project.term, project.number)
    assert fresh is not None, "the project row was read a moment ago"
    return fresh
