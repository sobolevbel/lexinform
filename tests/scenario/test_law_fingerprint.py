"""A new document of the same act is not read again; a changed act is, and a scan always is."""

from lexinform.law_digest import law_digest
from lexinform.models import Attachment
from tests.fakes import FakeTextExtractor
from tests.harness import RCL, TERM, World, print_url
from tests.scenario.test_tracking_rcl import RM, _druk_the_listing_does_not_link
from tests.unit.test_law_digest import DRUK_TEXT, RCL_TEXT

REASONS = "Projekt realizuje cele rządu w zakresie migracji. " * 60
RCL_FILE = RCL_TEXT + REASONS
DRUK_FILE = DRUK_TEXT + REASONS
CHANGED_DRUK_FILE = DRUK_FILE.replace("3 lata", "2 lata")


def _carded_project_then_its_druk(w: World, druk: bytes) -> int:
    """A carded project read from `RCL_FILE`; its druk, printed as `druk`, joined in discovery."""
    project = w.add_rcl_project()
    for document in project.text_documents().values():
        w.rcl.files[document.url] = b"%PDF-rcl"
    w.run()
    card = w.card_id(RCL)
    w.rcl.rm_numbers[RM] = project.id
    w.clock.advance(days=1)
    _druk_the_listing_does_not_link(w, project.title)
    w.gateway.files[print_url("3100")] = druk
    return card


def _texts(**more: str) -> FakeTextExtractor:
    by_content = {
        b"%PDF-rcl": RCL_FILE,
        b"%PDF-druk": DRUK_FILE,
        b"%PDF-changed": CHANGED_DRUK_FILE,
    }
    by_content.update({key.encode(): text for key, text in more.items()})
    return FakeTextExtractor(by_content=by_content, page_count=4)


def test_the_druk_of_the_same_act_is_not_read_again() -> None:
    w = World(extractor=_texts())
    card = _carded_project_then_its_druk(w, b"%PDF-druk")

    report = w.run()

    assert (report.linked, report.reanalyzed) == (1, 0)
    assert not [ctx for ctx in w.llm.contexts if ctx.number == "3100"]
    bill, change, reply_to = w.publisher.updates[-1]
    assert (bill.number, reply_to) == ("3100", card) and not change.content_changed
    stored = w.bill("3100").analysis
    assert stored is not None and stored.source_url == print_url("3100")
    assert stored.revision == 1 and stored.law_sha256 == law_digest(DRUK_FILE)


def test_the_druk_of_a_changed_act_is_read_again() -> None:
    w = World(extractor=_texts())
    _carded_project_then_its_druk(w, b"%PDF-changed")

    report = w.run()

    assert (report.linked, report.reanalyzed) == (1, 1)
    read = [ctx for ctx in w.llm.contexts if ctx.number == "3100"]
    assert len(read) == 1 and read[0].previous_summary is not None


def _analysed_print_then_a_new_version(w: World, new: bytes) -> str:
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.gateway.files[print_url("3039")] = b"%PDF-druk"
    w.run()
    w.clock.advance(days=2)
    revised = print_url("3039").replace("3039.pdf", "3039_v2.pdf")
    w.gateway.prints["3039"] = w.gateway.prints["3039"].model_copy(
        update={
            "change_date": w.clock.now().replace(tzinfo=None),
            "attachments": (Attachment(print_number="3039", name="3039_v2.pdf", url=revised),),
        }
    )
    w.gateway.files[revised] = new
    return revised


def test_an_analysis_stored_before_fingerprints_is_matched_through_its_own_file() -> None:
    w = World(extractor=_texts(**{"%PDF-v2": DRUK_TEXT + "Nowe uzasadnienie. " * 150}))
    revised = _analysed_print_then_a_new_version(w, b"%PDF-v2")
    stored = w.bill("3039").analysis
    assert stored is not None and stored.law_sha256 is not None
    w.repo.save_analysis(TERM, "3039", stored.model_copy(update={"law_sha256": None}))

    report = w.run()

    assert report.reanalyzed == 0
    assert len([ctx for ctx in w.llm.contexts if ctx.number == "3039"]) == 1
    again = w.bill("3039").analysis
    assert again is not None and (again.source_url, again.revision) == (revised, 1)
    assert again.law_sha256 == stored.law_sha256


def test_a_new_version_that_is_a_scan_is_read_again() -> None:
    w = World(extractor=_texts(**{"%PDF-v2": ""}))
    _analysed_print_then_a_new_version(w, b"%PDF-v2")

    report = w.run()

    assert report.reanalyzed == 1
    read = [ctx for ctx in w.llm.contexts if ctx.number == "3039"]
    assert len(read) == 2 and read[-1].scan is not None
