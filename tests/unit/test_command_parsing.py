"""How a channel post becomes a command: numbers in every notation, links, modifiers."""

import pytest

from lexinform.models import BillRef, CommandName, RefKind, parse_command, parse_reference


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3039", BillRef(kind=RefKind.DRUK, value="3039")),
        ("druk 3039", BillRef(kind=RefKind.DRUK, value="3039")),
        ("druk nr 0950", BillRef(kind=RefKind.DRUK, value="950")),
        ("RPW/29075/2026", BillRef(kind=RefKind.RPW, value="RPW/29075/2026")),
        ("rpw/29075/2026", BillRef(kind=RefKind.RPW, value="RPW/29075/2026")),
        ("RCL/12414100", BillRef(kind=RefKind.RCL, value="RCL/12414100")),
        ("UC164", BillRef(kind=RefKind.WYKAZ, value="UC164")),
        ("ud 247", BillRef(kind=RefKind.WYKAZ, value="UD247")),
        ("UDER66", BillRef(kind=RefKind.WYKAZ, value="UDER66")),
        ("RM-0610-139-26", BillRef(kind=RefKind.RM, value="RM-0610-139-26")),
        ("rm-0610-139-26", BillRef(kind=RefKind.RM, value="RM-0610-139-26")),
    ],
)
def test_numbers_in_every_notation(text: str, expected: BillRef) -> None:
    assert parse_reference(text) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://www.sejm.gov.pl/Sejm10.nsf/PrzebiegProc.xsp?nr=3039",
            BillRef(kind=RefKind.DRUK, value="3039", term=10),
        ),
        (
            "https://www.sejm.gov.pl/Sejm10.nsf/druk.xsp?nr=3039",
            BillRef(kind=RefKind.DRUK, value="3039", term=10),
        ),
        (
            "www.sejm.gov.pl/Sejm9.nsf/PrzebiegProc.xsp?nr=12",
            BillRef(kind=RefKind.DRUK, value="12", term=9),
        ),
        (
            "https://api.sejm.gov.pl/sejm/term10/processes/3039",
            BillRef(kind=RefKind.DRUK, value="3039", term=10),
        ),
        (
            "https://api.sejm.gov.pl/sejm/term10/prints/3039/3039.pdf",
            BillRef(kind=RefKind.DRUK, value="3039", term=10),
        ),
        (
            "https://www.sejm.gov.pl/Sejm10.nsf/agent.xsp?symbol=KONSULTOWANY_PROJEKT&NrProjektu=RPW/29075/2026",
            BillRef(kind=RefKind.RPW, value="RPW/29075/2026", term=10),
        ),
        (
            "https://legislacja.rcl.gov.pl/projekt/12414100",
            BillRef(kind=RefKind.RCL, value="RCL/12414100"),
        ),
        (
            "https://legislacja.rcl.gov.pl/projekt/12414100/katalog/13223875#13223875",
            BillRef(kind=RefKind.RCL, value="RCL/12414100"),
        ),
        (
            "https://legislacja.rcl.gov.pl/lista/2/projekt/12414100",
            BillRef(kind=RefKind.RCL, value="RCL/12414100"),
        ),
        (
            "https://legislacja.rcl.gov.pl/getIdFromLegislacja?number=RM-0610-139-26",
            BillRef(kind=RefKind.RM, value="RM-0610-139-26"),
        ),
        (
            "<https://legislacja.rcl.gov.pl/projekt/12414100>",
            BillRef(kind=RefKind.RCL, value="RCL/12414100"),
        ),
    ],
)
def test_links_to_the_bill_on_the_three_sites(url: str, expected: BillRef) -> None:
    assert parse_reference(url) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "3039-A",
        "abc",
        "https://www.sejm.gov.pl/Sejm10.nsf/agent.xsp?symbol=POSEL&Id=123",
        "https://legislacja.rcl.gov.pl/lista?typeId=2",
        "https://example.gov.pl/projekt/1",
        "12414100",  # an RCL id needs its prefix or a link; a print number has five digits at most
    ],
)
def test_anything_else_is_not_a_reference(text: str) -> None:
    assert parse_reference(text) is None


def test_analyze_with_modifiers_in_any_order() -> None:
    command = parse_command("/analyze force 3039 publish")

    assert command is not None
    assert command.name is CommandName.ANALYZE
    assert command.ref == BillRef(kind=RefKind.DRUK, value="3039")
    assert command.force and command.publish and command.error is None


def test_dashed_modifiers_and_the_bot_suffix_are_accepted() -> None:
    command = parse_command(
        "/analyze@lexinform_bot --force https://legislacja.rcl.gov.pl/projekt/1"
    )

    assert command is not None
    assert command.name is CommandName.ANALYZE and command.force
    assert command.ref == BillRef(kind=RefKind.RCL, value="RCL/1")


def test_a_post_without_a_slash_is_not_a_command() -> None:
    assert parse_command("analyze 3039") is None
    assert parse_command("") is None


def test_an_unknown_command_asks_for_help() -> None:
    command = parse_command("/delete 3039")

    assert command is not None
    assert command.name is CommandName.HELP and command.error == "unknown command /delete"


def test_a_command_without_a_reference_says_so() -> None:
    command = parse_command("/show")

    assert command is not None
    assert command.name is CommandName.SHOW and command.ref is None
    assert command.error == "/show needs a bill number or a link"


def test_an_unreadable_reference_is_reported_verbatim() -> None:
    command = parse_command("/skip the bill about visas")

    assert command is not None
    assert command.error == "cannot read a bill number or a link in 'the bill about visas'"


def test_help_takes_no_arguments() -> None:
    command = parse_command("/help me")

    assert command is not None
    assert command.name is CommandName.HELP and command.error is None


def test_druk_label_names_the_kind() -> None:
    assert BillRef(kind=RefKind.DRUK, value="3039").label == "druk 3039"
    assert BillRef(kind=RefKind.WYKAZ, value="UC164").label == "UC164"
