import datetime as dt

import pytest

from lexinform.document_composition import BundleAssembly, RequiredPartFailure, amendment_part
from lexinform.models.document_reads import LoadedFile
from lexinform.models.documents import DocumentPart, DocumentSelection
from lexinform.models.sejm import ProcessSummary
from lexinform.rcl_amendments import (
    is_rcl_amendment_transmittal,
    rcl_amendment_base_index,
    rcl_self_amendment_part,
    rcl_self_amendment_print_number,
)

TITLE = "Projekt ustawy o ochronie cudzoziemców"
TARGET = "o ochronie cudzoziemców"
BODY = "W projekcie ustawy art. 3 otrzymuje brzmienie: „Art. 3. Termin wynosi 30 dni.”"


def member(target: str = TARGET, body: str = BODY) -> DocumentPart:
    return DocumentPart(
        members=("proposal.docx",),
        text=f"AUTOPOPRAWKA\ndo projektu ustawy {target}\n{body}",
    )


@pytest.mark.parametrize(
    "target",
    [TARGET, f"{TARGET} (druk nr 300)", "o ochronie osób (UD 34)"],
)
def test_identifies_single_project_amendment(target: str) -> None:
    proposal = member(target)
    letter = DocumentPart(members=("pismo.pdf",), text="Przedkładam autopoprawkę do projektu.")
    assert (
        rcl_self_amendment_part((letter, proposal), project_title=TITLE, wykaz_number="UD34")
        is proposal
    )


@pytest.mark.parametrize(
    ("target", "body"),
    [
        (f"{TARGET} (UD35)", BODY),
        (f"{TARGET} (UD34) oraz projektu ustawy o pomocy (UD35)", BODY),
        (f"{TARGET} oraz projektu ustawy o pomocy", BODY),
        ("o ochronie osób", BODY),
        (TARGET, "Propozycja obejmuje uzupełnienie Oceny Skutków Regulacji."),
        (TARGET, f"W projekcie ustawy uzupełnia się OSR.\nUZASADNIENIE\n{BODY}"),
    ],
)
def test_declines_wrong_or_non_normative_target(target: str, body: str) -> None:
    assert (
        rcl_self_amendment_part((member(target, body),), project_title=TITLE, wykaz_number="UD34")
        is None
    )


def test_declines_non_archive_incomplete_or_duplicate_member() -> None:
    proposal = member()
    for parts in (
        (proposal.model_copy(update={"members": ()}),),
        (proposal.model_copy(update={"losses": ("partial read",)}),),
        (proposal, proposal),
    ):
        assert rcl_self_amendment_part(parts, project_title=TITLE, wykaz_number=None) is None


@pytest.mark.parametrize(
    ("reference", "number"),
    [
        ("(druk nr 764) (UD34)", "764"),
        ("(druku sejmowego nr 764)", "764"),
        ("(druk sejmowy nr 764)", "764"),
        ("(nr druku 764)", "764"),
        ("(druk nr 764-A)", None),
        ("(druk 764) (druk 765-A)", None),
        ("(druk 764) oraz projektu ustawy o pomocy", None),
        ("(nr druku sejmowego nie został nadany)", None),
        ("", None),
    ],
)
def test_reads_only_one_explicit_print_reference(reference: str, number: str | None) -> None:
    text = member(f"{TARGET} {reference}", f"{BODY}\nDruk nr 1000.").text
    assert rcl_self_amendment_print_number(text) == number


@pytest.mark.parametrize(
    ("rm_number", "texts", "expected"),
    [
        ("rm-0610-20-24", ("Art. 1. Termin wynosi 30 dni.\n",), 0),
        ("RM-0610-21-24", ("Art. 1. Termin wynosi 30 dni.",), None),
        ("RM-0610-20-24", ("Art. 1. Termin wynosi 31 dni.",), None),
        ("RM-0610-20-24", (None, "Art. 1. Termin wynosi 30 dni."), None),
        ("RM-0610-20-24", ("Art. 1. Termin wynosi 30 dni.",) * 2, None),
        ("RM-0610-20-24", ("Art. 1. Termin wynosi 31 dni.", "Art. 1. Termin wynosi 30 dni."), 1),
    ],
)
def test_resolves_only_one_complete_base_for_the_explicit_print(
    rm_number: str, texts: tuple[str | None, ...], expected: int | None
) -> None:
    process = ProcessSummary(
        term=10,
        number="764",
        title=TITLE,
        document_type="Projekt ustawy",
        rcl_num="RM-0610-20-24",
        change_date=dt.datetime(2024, 1, 1, tzinfo=dt.UTC),
    )
    original = DocumentSelection(
        parts=(DocumentPart(role="bill", text="Art. 1. Termin wynosi 30 dni."),)
    )
    candidates = tuple(
        DocumentSelection(parts=(DocumentPart(role="bill", text=text),))
        if text is not None
        else DocumentSelection(state="waiting")
        for text in texts
    )
    assert (
        rcl_amendment_base_index(
            member(f"{TARGET} (druk 764)"),
            project_rm_number=rm_number,
            print_process=process,
            original=original,
            candidates=candidates,
        )
        == expected
    )


@pytest.mark.parametrize("mixed", [False, True])
def test_rcl_archive_does_not_relabel_standalone_support(mixed: bool) -> None:
    support = DocumentPart(members=("osr.docx",), role="osr", text="OCENA SKUTKÓW REGULACJI")
    proposal = member(body=f"{BODY}\nUZASADNIENIE\nWniosek.").model_copy(
        update={"role": "justification"}
    )
    parts = (support, proposal) if mixed else (support,)
    file = LoadedFile(
        "\n".join(part.text for part in parts),
        selection=DocumentSelection(state="unsupported", parts=parts),
    )
    bundle = BundleAssembly(LoadedFile("bill"), ("bill",), (), ())

    result = amendment_part(bundle, "https://rcl.test/amendment.zip", file, rcl=True)

    if mixed:
        assert isinstance(result, BundleAssembly)
        assert result.evidence == (proposal.model_copy(update={"role": "self_amendment"}),)
        assert result.texts == bundle.texts
    else:
        assert isinstance(result, RequiredPartFailure)
        assert result.blocked.selection.state == "waiting"
        assert result.reason == "returned_no_text"


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [
        (" wraz z uzasadnieniem.", True),
        (" i proponuję nadać art. 6 następujące brzmienie.", False),
        (" wraz z projektem, w którym skreśla się przepis.", False),
        (" w następującym brzmieniu: § 1. Termin wynosi 30 dni.", False),
    ],
)
def test_transmittal_excludes_letters_that_contain_proposed_wording(
    suffix: str, expected: bool
) -> None:
    text = "Szanowny Panie, w załączeniu przekazuję projekt autopoprawki" + suffix
    assert is_rcl_amendment_transmittal(text) is expected


def test_rcl_archive_does_not_send_transmittal_as_a_proposal() -> None:
    letter = DocumentPart(
        members=("Autopopr.pdf",),
        role="bill",
        text="W załączeniu przekazuję projekt autopoprawki wraz z uzasadnieniem.",
    )
    bundle = BundleAssembly(LoadedFile("bill"), ("bill",), (), ())
    file = LoadedFile(letter.text, selection=DocumentSelection(parts=(letter,)))

    result = amendment_part(bundle, "https://rcl.test/amendment.zip", file, rcl=True)

    assert isinstance(result, RequiredPartFailure)
    assert result.blocked.selection.state == "waiting"
