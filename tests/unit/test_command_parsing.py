"""How a channel post becomes a command: numbers in every notation, links, options."""

import re
from pathlib import Path

import pytest

from lexinform.cli import app
from lexinform.models import (
    DISPATCHED,
    BillRef,
    Command,
    CommandName,
    RefKind,
    parse_command,
    parse_reference,
)


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

    assert command is not None, "parse_command returns None only for text with no leading slash"
    assert command.name is CommandName.ANALYZE
    assert command.ref == BillRef(kind=RefKind.DRUK, value="3039")
    assert command.force and command.publish and command.error is None


def test_dashed_modifiers_and_the_bot_suffix_are_accepted() -> None:
    command = parse_command(
        "/analyze@lexinform_bot --force https://legislacja.rcl.gov.pl/projekt/1"
    )

    assert command is not None, "parse_command returns None only for text with no leading slash"
    assert command.name is CommandName.ANALYZE and command.force
    assert command.ref == BillRef(kind=RefKind.RCL, value="RCL/1")


def test_a_post_without_a_slash_is_not_a_command() -> None:
    assert parse_command("analyze 3039") is None
    assert parse_command("") is None


def test_an_unknown_command_asks_for_help() -> None:
    command = parse_command("/delete 3039")

    assert command is not None, "parse_command returns None only for text with no leading slash"
    assert command.name is CommandName.HELP and command.error == "unknown command /delete"


def test_a_command_without_a_reference_says_so() -> None:
    command = parse_command("/show")

    assert command is not None, "parse_command returns None only for text with no leading slash"
    assert command.name is CommandName.SHOW and command.ref is None
    assert command.error == "/show needs a bill number or a link"


def test_an_unreadable_reference_is_reported_verbatim() -> None:
    command = parse_command("/skip the bill about visas")

    assert command is not None, "parse_command returns None only for text with no leading slash"
    assert command.error == "cannot read a bill number or a link in 'the bill about visas'"


def test_find_takes_words_and_not_a_bill() -> None:
    command = parse_command("/find ustawa o cudzoziemcach")

    assert command is not None, "parse_command returns None only for text with no leading slash"
    assert command.name is CommandName.FIND and command.ref is None
    assert command.query == "ustawa o cudzoziemcach" and command.error is None


def test_find_needs_something_to_look_for() -> None:
    command = parse_command("/find ab")

    assert command is not None, "parse_command returns None only for text with no leading slash"
    assert command.error == "/find needs at least 3 characters to look for"


def test_status_takes_no_arguments() -> None:
    command = parse_command("/status")

    assert command is not None, "parse_command returns None only for text with no leading slash"
    assert command.name is CommandName.STATUS and command.error is None


def test_help_takes_no_arguments() -> None:
    command = parse_command("/help me")

    assert command is not None, "parse_command returns None only for text with no leading slash"
    assert command.name is CommandName.HELP and command.error is None


def test_run_takes_the_workflows_own_inputs() -> None:
    """`/run` is the one command that names no bill: its words are `daily.yml`'s inputs, and
    `dry` alone is the flag."""
    command = parse_command("/run since=2026-09-01 dry reprefilter=50 index_rcl_since=2023-11-01")

    assert command is not None and command.name is CommandName.RUN and command.error is None
    assert command.inputs == {
        "since": "2026-09-01",
        "dry_run": "true",
        "reprefilter_limit": "50",
        "index_rcl_since": "2023-11-01",
    }


def test_the_text_skipped_backfill_rides_on_reprefilter() -> None:
    """The only way back for a bill the text stage rejected: nothing else reopens that skip."""
    command = parse_command("/run reprefilter=200 text_skipped")

    assert command is not None and command.error is None
    assert command.inputs == {
        "reprefilter_limit": "200",
        "reprefilter_text_skipped": "true",
    }


def test_run_alone_starts_the_ordinary_run() -> None:
    command = parse_command("/run")

    assert command is not None and (command.name, command.inputs) == (CommandName.RUN, {})


def test_a_run_option_is_checked_here_and_not_hours_later_by_the_workflow() -> None:
    """The workflow would take `since=вчера` as free text and quietly do the wrong thing."""
    bad_date = parse_command("/run since=вчера")
    bad_count = parse_command("/run reprefilter=many")
    unknown = parse_command("/run turbo")
    missing = parse_command("/run since")

    assert bad_date is not None and bad_date.error is not None and "date" in bad_date.error
    assert bad_count is not None and bad_count.error is not None and "number" in bad_count.error
    assert unknown is not None and unknown.error is not None and "unknown option" in unknown.error
    assert missing is not None and missing.error is not None and "needs a value" in missing.error


def test_druk_label_names_the_kind() -> None:
    assert BillRef(kind=RefKind.DRUK, value="3039").label == "druk 3039"
    assert BillRef(kind=RefKind.WYKAZ, value="UC164").label == "UC164"


@pytest.mark.parametrize(
    "text",
    [
        "/delivery",
        "/delivery -1",
        "/delivery 0",
        "/delivery 1 confirm",
        "/delivery 1 confirm -1",
        "/delivery 1 confirm 0",
        "/delivery 1 retry extra",
        "/delivery 1 dismiss",
        "/delivery 1 sent",
        "/delivery 1 confirm 1 extra",
    ],
)
def test_invalid_delivery_commands_cannot_mutate(text: str) -> None:
    command = parse_command(text)
    assert command is not None and command.error is not None


@pytest.mark.parametrize(
    "text",
    [
        "/delivery 12",
        "/delivery 12 retry",
        "/delivery 12 confirm 777",
        "/delivery@bot 12 dismiss hearing has passed",
    ],
)
def test_delivery_commands_address_publication_ids(text: str) -> None:
    command = parse_command(text)
    assert command is not None and command.error is None
    assert command.delivery_id == 12 and command.ref is None


def test_every_cli_command_worth_asking_for_is_a_command_here() -> None:
    """Relay and batch recovery commands operate the run machinery outside the chat."""
    cli = {
        info.name or (info.callback.__name__.replace("_", "-") if info.callback else "")
        for info in app.registered_commands
    }

    assert cli - {name.value for name in CommandName} == {
        "listen",
        "commands",
        "poll-batches",
        "batch-intents",
        "recover-batch-intent",
        "attach-batch-intents",
    }
    assert {group.name for group in app.registered_groups} == {"db"}


def test_the_workflow_offers_exactly_the_commands_the_relay_starts() -> None:
    """`command` is a `type: choice`, so a name the relay sends and `daily.yml` does not list is
    a dispatch GitHub refuses — hours after the operator was told the run had started."""
    workflow = Path(__file__).parents[2] / ".github" / "workflows" / "daily.yml"
    match = re.search(r"^\s*options: \[(.+)\]$", workflow.read_text(), re.MULTILINE)

    assert match is not None, "daily.yml lists the command input's choices as options: [...]"
    assert {choice.strip() for choice in match.group(1).split(",")} == {
        name.value for name in DISPATCHED
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/scan", {"command": "scan"}),
        ("/scan since=2026-09-01", {"command": "scan", "options": "--since 2026-09-01"}),
        ("/track", {"command": "track"}),
        ("/track dry", {"command": "track", "dry_run": "true"}),
        ("/reprefilter", {"command": "reprefilter"}),
        (
            "/reprefilter limit=200 text_skipped",
            {"command": "reprefilter", "options": "--limit 200 --include-text-skipped"},
        ),
        (
            "/index-rcl-numbers since=2023-11-01",
            {"command": "index-rcl-numbers", "options": "--since 2023-11-01"},
        ),
        (
            "/index since=2023-11-01",
            {"command": "index-rcl-numbers", "options": "--since 2023-11-01"},
        ),
    ],
)
def test_a_phase_of_a_run_is_started_as_that_phase(text: str, expected: dict[str, str]) -> None:
    """Each is a `daily.yml` dispatch naming the CLI command and its flags, never an inbox file."""
    command = parse_command(text)

    assert command is not None and command.error is None
    assert command.name in DISPATCHED and command.inputs == expected


def test_the_runs_own_options_reach_the_cli_as_flags() -> None:
    """Seven of `lexinform run`'s options are no input of the workflow: they ride in `options`,
    which the relay builds out of values it has already checked."""
    command = parse_command("/run dry no_publish no_rcl full_track max_analyze=5 min_score=4")

    assert command is not None and command.error is None
    assert command.inputs == {
        "dry_run": "true",
        "options": "--no-publish --no-rcl --full-track --max-analyze 5 --min-score 4",
    }


def test_a_count_outside_its_range_is_refused_here_too() -> None:
    command = parse_command("/run min_score=9")

    assert command is not None and command.error == "/run: min_score takes 1–5, not 9"


def test_index_rcl_numbers_has_no_default_date_to_fall_back_on() -> None:
    """`--since` is the one option of a CLI command that is not optional."""
    command = parse_command("/index-rcl-numbers")

    assert command is not None, "parse_command returns None only for text with no leading slash"
    assert command.error == "/index-rcl-numbers needs since=… (since is not optional)"


def test_reset_takes_any_status_and_refuses_what_is_not_one() -> None:
    good = parse_command("/reset 3039 to=skipped_prefilter")
    bare = parse_command("/reset 3039")
    bad = parse_command("/reset 3039 to=maybe")

    assert good is not None and good.options == {"to": "skipped_prefilter"}
    assert bare is not None and bare.error is None and bare.options == {}
    assert bad is not None and bad.error is not None and "takes a status" in bad.error


def test_preview_sends_to_a_chat_and_not_to_anything_else() -> None:
    to_channel = parse_command("/preview 3039 to=-1001234567")
    to_name = parse_command("/preview 3039 to=@lexinform_test")
    nonsense = parse_command("/preview 3039 to=somewhere")

    assert to_channel is not None and to_channel.options == {"to": "-1001234567"}
    assert to_name is not None and to_name.options == {"to": "@lexinform_test"}
    assert nonsense is not None and nonsense.error is not None and "chat id" in nonsense.error


def test_the_report_commands_take_a_window() -> None:
    runs = parse_command("/runs days=7")
    cost = parse_command("/cost days=7 top=3")

    assert runs is not None and runs.count("days", 30) == 7
    assert cost is not None and (cost.count("days", 30), cost.count("top", 5)) == (7, 3)
    assert parse_command("/cost") is not None


def test_a_link_carrying_an_equals_sign_is_not_read_as_an_option() -> None:
    """`key=value` is an option only where the command knows the key; a Sejm link has plenty."""
    command = parse_command("/analyze https://www.sejm.gov.pl/Sejm10.nsf/druk.xsp?nr=3039 force")

    assert command is not None and command.error is None
    assert command.ref == BillRef(kind=RefKind.DRUK, value="3039", term=10) and command.force


def test_an_option_of_another_command_is_answered_and_not_swallowed() -> None:
    """`/show BILL force` used to do nothing and say nothing, which reads like it did something.

    The word is named, not the whole line: the reference before it parsed perfectly well.
    """
    command = parse_command("/show 3039 force")

    assert command is not None, "parse_command returns None only for text with no leading slash"
    assert command.error == "/show: unknown option force — it takes none"
    assert parse_command("/analyze 3039 turbo") == Command(
        name=CommandName.ANALYZE,
        error="/analyze: unknown option turbo — it takes force, publish, json",
    )


def test_analyze_asks_for_the_raw_verdict() -> None:
    command = parse_command("/analyze 3039 json")

    assert command is not None and command.as_json and command.error is None
