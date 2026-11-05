import datetime as dt

import pytest
from tools.replay_committee_recommendation import official_recommendation_stages, report_evidence

from lexinform.models import Stage

URL = "https://api.sejm.gov.pl/sejm/term10/prints/587/587.pdf"
POSITION = Stage(
    stage_type="SenatePosition",
    stage_name="Stanowisko Senatu",
    print_number="584",
    position="wniósł poprawki",
    date=dt.date(2024, 7, 25),
)
REPORT = Stage(
    stage_type="CommitteeReport",
    stage_name="Sprawozdanie komisji",
    print_number="587",
    report_file=URL,
    proposal="przyjąć część poprawek",
    date=dt.date(2024, 7, 26),
)
HEADING = (
    "Druk nr 587 X kadencja S P R A W O Z D A N I E KOMISJI "
    "o uchwale Senatu w sprawie ustawy (druk nr 584)"
)
RECOMMENDATION = (
    " Marszałek Sejmu, zgodnie z regulaminem, skierował uchwałę do komisji. "
    "Komisje wnoszą: W y s o k i S e j m raczy poprawki Senatu zawarte w: pkt 1 – przyjąć."
)


def committee(*reports: Stage) -> Stage:
    return Stage(stage_name="Praca w komisjach", stage_type="CommitteeWork", children=reports)


def test_exact_named_senate_target_agrees_with_official_sequence() -> None:
    assert official_recommendation_stages(
        (POSITION, committee(REPORT)), report="587", resolution="584", report_url=URL
    ) == (REPORT, POSITION)
    assert report_evidence(HEADING + RECOMMENDATION, "587", "584")["page"] == 1


@pytest.mark.parametrize(
    "stages",
    [
        (committee(REPORT), POSITION),
        (POSITION, POSITION, committee(REPORT)),
        (POSITION, committee(REPORT, REPORT)),
        (POSITION, committee(REPORT.model_copy(update={"date": POSITION.date}))),
        (POSITION, committee(REPORT.model_copy(update={"decision": "uchwalono"}))),
        (POSITION, committee(REPORT.model_copy(update={"proposal": "uchwalono"}))),
        (POSITION, committee(REPORT.model_copy(update={"report_file": URL + "?copy"}))),
        (POSITION, committee(REPORT.model_copy(update={"sub_committee": True}))),
    ],
)
def test_typed_sequence_requires_unambiguous_non_adoptive_official_report(
    stages: tuple[Stage, ...],
) -> None:
    with pytest.raises(ValueError, match="[Oo]fficial"):
        official_recommendation_stages(stages, report="587", resolution="584", report_url=URL)


@pytest.mark.parametrize(
    "first_page",
    [
        HEADING.replace("584", "585") + RECOMMENDATION,
        HEADING + " (druk nr 584)" + RECOMMENDATION,
        HEADING + " (druk nr 585)" + RECOMMENDATION,
        HEADING + " (druki nr 585 i 586)" + RECOMMENDATION,
        HEADING.replace("(druk nr 584)", "") + RECOMMENDATION + " (druk nr 584)",
        HEADING.replace("Druk nr 587", "Druk nr 585") + RECOMMENDATION,
        HEADING + " Marszałek Sejmu, a Sejm uchwalił ustawę.",
        HEADING + RECOMMENDATION.replace("przyjąć", "przyjęto"),
    ],
)
def test_pdf_must_name_the_exact_target_in_heading_and_recommend_action(first_page: str) -> None:
    with pytest.raises(ValueError, match="Report"):
        report_evidence(first_page, "587", "584")
