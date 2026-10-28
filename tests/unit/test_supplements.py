from lexinform.models import PrintInfo, new_supplements, supplement_kind


def test_the_kinds_worth_telling_are_told_apart_by_their_title() -> None:
    assert supplement_kind("Stanowisko Rządu do druku nr 3039.") == "government_position"
    assert supplement_kind("Do druku nr 3039 - ocena skutków regulacji") == "impact_assessment"
    assert supplement_kind("Do druku nr 3039 - opinia SN") is None
    assert supplement_kind("Do druku nr 3039 - opinia PG RP ( nie zgłoszono uwag ).") is None
    assert supplement_kind("Do druku nr 3039 - zmiana posła sprawozdawcy") is None


def test_new_supplements_are_those_the_print_has_and_the_bill_has_not_seen() -> None:
    print_info = PrintInfo(
        term=10,
        number="3039",
        title="Projekt",
        additional_prints=(
            PrintInfo(term=10, number="3039-s", title="Stanowisko Rządu"),
            PrintInfo(term=10, number="3039-001", title="OSR"),
        ),
    )

    assert [p.number for p in new_supplements(print_info, ())] == ["3039-s", "3039-001"]
    assert [p.number for p in new_supplements(print_info, ["3039-s"])] == ["3039-001"]
    assert new_supplements(None, ()) == ()
