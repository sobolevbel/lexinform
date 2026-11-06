import pytest
from pydantic import ValidationError

from lexinform.models.analysis import BillContext
from lexinform.models.enums import ApplicantType
from lexinform.models.word_formula_input import WordFormulaInputEntry, encode_formula_tree
from lexinform.word_formulas import MathNode


def test_absent_formula_input_does_not_change_bill_context_serialization() -> None:
    context = BillContext(
        number="RCL/1",
        title="Project",
        description=None,
        document_date=None,
        applicant_type=ApplicantType.GOVERNMENT,
        text="Text",
        truncated=False,
        text_source="documents",
        source_kind="rcl",
    )

    assert "word_formula_input" not in context.model_dump(mode="json")
    assert "word_formula_input" not in context.model_dump_json()


def _entry(tree: str, *, emitted_gap: int = 5) -> WordFormulaInputEntry:
    return WordFormulaInputEntry(
        paragraph_path=(0, 1),
        formula_path=(0, 1, 0),
        subtree_sha256="0" * 64,
        source_anchor=(4, 10),
        emitted_anchor=(3, 9),
        source_gap=6,
        emitted_gap=emitted_gap,
        tree=tree,
    )


def test_formula_input_requires_canonical_structure_and_same_anchor_offset() -> None:
    tree = encode_formula_tree(
        MathNode("oMath", children=(MathNode("r", children=(MathNode("t", text="÷100 %"),)),))
    )
    assert _entry(tree).emitted_gap == 5
    with pytest.raises(ValidationError, match="anchors do not agree"):
        _entry(tree, emitted_gap=6)
    with pytest.raises(ValidationError, match="Expected canonical"):
        _entry(" " + tree)
    reversed_fraction = MathNode(
        "oMath", children=(MathNode("f", children=(MathNode("den"), MathNode("num"))),)
    )
    with pytest.raises(ValidationError, match="operand order"):
        _entry(encode_formula_tree(reversed_fraction))


def test_offline_nary_nodes_are_outside_the_frozen_formula_profile() -> None:
    tree = encode_formula_tree(MathNode("oMath", children=(MathNode("nary"),)))

    with pytest.raises(ValidationError, match="Invalid Word formula node"):
        _entry(tree)
