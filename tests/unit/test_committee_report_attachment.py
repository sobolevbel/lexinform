import json
from pathlib import Path

import pytest

from lexinform.adapters.sejm_api import parse_print
from lexinform.committee_recommendation import committee_report_attachment
from lexinform.models import Attachment, PrintInfo, Stage

URL = "https://api.sejm.gov.pl/sejm/term10/prints/3383/3383.pdf"
REPORT = Stage(
    stage_type="CommitteeReport",
    stage_name="Sprawozdanie komisji",
    print_number="3383",
    report_file=URL,
    proposal="przyjąć poprawki",
)


def catalogue() -> PrintInfo:
    path = Path(__file__).parents[1] / "fixtures/sejm/committee_3383_print.json"
    return parse_print(json.loads(path.read_text()), term=10)


def test_catalogue_preserves_the_actual_attachment_name() -> None:
    info = catalogue()
    selected = committee_report_attachment(REPORT, info, term=10, process_number="2644")
    assert selected is not None, "the official catalogue names one PDF for this report"
    assert selected.name == "3383 .pdf"
    assert selected.url == URL.replace("3383.pdf", "3383%20.pdf")
    assert REPORT.report_file == URL


@pytest.mark.parametrize(
    "changes",
    [
        {"number": "3384"},
        {"term": 9},
        {"official_term_confirmed": False},
        {"process_prints": ()},
        {"process_prints": ("2645",)},
        {"title": "Opinia NBP"},
        {"attachments": ()},
        {"attachments": (Attachment(print_number="3384", name="3383 .pdf", url=URL),)},
        {"attachments": (Attachment(print_number="3383", name="3383-s.pdf", url=URL),)},
        {"attachments": (Attachment(print_number="3383", name="3383-001.pdf", url=URL),)},
        {"attachments": (Attachment(print_number="3383", name="3384.pdf", url=URL + "x"),)},
        {"attachments": (Attachment(print_number="3383", name="report.pdf", url=URL + "x"),)},
    ],
)
def test_unconfirmed_or_foreign_catalogues_cannot_replace_the_stage_link(
    changes: dict[str, object],
) -> None:
    info = catalogue().model_copy(update=changes)
    assert committee_report_attachment(REPORT, info, term=10, process_number="2644") is None


@pytest.mark.parametrize("declared_present", [False, True])
def test_multiple_pdfs_require_the_exact_declared_attachment(declared_present: bool) -> None:
    info = catalogue()
    extra = Attachment(
        print_number="3383", name="3383.pdf", url=URL if declared_present else URL + "?other"
    )
    info = info.model_copy(update={"attachments": (*info.attachments, extra)})
    selected = committee_report_attachment(REPORT, info, term=10, process_number="2644")
    assert selected == (extra if declared_present else None)


def test_joint_report_accepts_an_explicit_parent_among_its_processes() -> None:
    info = catalogue().model_copy(update={"process_prints": ("2644", "2645")})
    assert committee_report_attachment(REPORT, info, term=10, process_number="2644")


@pytest.mark.parametrize(
    "name", ["3383-A .pdf", "druk nr 3383-A.PDF", "3383 A.pdf", "3383 - A - poprawki.pdf"]
)
def test_unique_report_names_are_read_from_the_catalogue(name: str) -> None:
    info = parse_print(
        {
            "number": "3383-A",
            "term": 10,
            "title": "Dodatkowe sprawozdanie komisji",
            "processPrint": ["2644"],
            "attachments": [name],
        },
        term=10,
    )
    stage = REPORT.model_copy(update={"print_number": "3383-A"})
    assert (
        committee_report_attachment(stage, info, term=10, process_number="2644")
        == (info.attachments[0])
    )
