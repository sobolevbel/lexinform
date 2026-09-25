import logging

from lexinform.errors import ServiceUnavailableError
from lexinform.models import Bill, BillStatus, rcl_number
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
