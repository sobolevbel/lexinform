import pytest

from lexinform.models.documents import DocumentPart
from lexinform.rcl_amendments import rcl_self_amendment_part, rcl_self_amendment_print_number

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
