from lexinform.models import PLENARY_COMMITTEE_CODE, Committee, Stage
from lexinform.services.tracking.stages import StageEnricher
from tests.fakes import FakeSejmGateway


def test_plenary_referral_never_requests_a_committee_endpoint() -> None:
    gateway = FakeSejmGateway(outages={"get_committee"})
    stage = Stage(
        stage_type="Referral", stage_name="Skierowanie", committee_code=PLENARY_COMMITTEE_CODE
    )
    enricher = StageEnricher(gateway)

    assert enricher.enrich(10, stage) == stage
    assert enricher.name_committees(10, (stage,)) == (stage,)
    assert not gateway.calls


def test_actual_committee_referral_still_receives_its_name() -> None:
    gateway = FakeSejmGateway(
        committees={"ASW": Committee(term=10, code="ASW", name="Komisja ASW")}
    )
    stage = Stage(stage_type="Referral", stage_name="Skierowanie", committee_code="ASW")
    enricher = StageEnricher(gateway)

    enriched = enricher.enrich(10, stage)

    assert enriched.committee_name == "Komisja ASW"
    assert gateway.calls == ["get_committee:ASW"]
