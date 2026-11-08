"""Read the original Sejm bill explicitly named in an RCL self-amendment."""

from collections.abc import Callable

from lexinform.models.document_reads import LoadedFile
from lexinform.models.documents import DocumentPart
from lexinform.models.rcl import RclProject, rcl_num_key
from lexinform.models.sejm import ProcessDetail
from lexinform.ports import SejmGateway
from lexinform.rcl_amendments import rcl_self_amendment_print_number


def read_rcl_amendment_original(
    gateway: SejmGateway,
    read: Callable[[str], LoadedFile],
    *,
    project: RclProject,
    proposal: DocumentPart,
) -> tuple[ProcessDetail, LoadedFile] | None:
    """Read one exact-process primary Word attachment with native standalone evidence."""
    rm_number = rcl_num_key(project.rm_number or "")
    term = project.sejm_term
    number = rcl_self_amendment_print_number(proposal.text)
    if not rm_number or number is None or term is None or not proposal.members or proposal.losses:
        return None
    process = gateway.get_process(term, number)
    if (process.term, process.number, rcl_num_key(process.rcl_num or "")) != (
        term,
        number,
        rm_number,
    ):
        return None
    info = gateway.get_print(term, number)
    if (info.term, info.number, info.official_single_process_parent) != (
        term,
        number,
        number,
    ) or not info.official_term_confirmed:
        return None
    attachments = tuple(
        attachment
        for attachment in info.attachments
        if attachment.name.casefold() == f"{number}-ustawa.docx"
    )
    if len(attachments) != 1 or attachments[0].print_number != number:
        return None
    attachment = attachments[0]
    original = read(attachment.url)
    if (
        not original.native_complete_standalone
        or original.sha256 is None
        or len(original.selection.parts) != 1
    ):
        return None
    (part,) = original.selection.parts
    if (part.role, part.url, part.sha256) != ("bill", attachment.url, original.sha256):
        return None
    return process, original
