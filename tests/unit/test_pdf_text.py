from __future__ import annotations

from lexinform.adapters.pdf_text import PypdfTextExtractor, TextBudget
from tests.conftest import FIXTURES


def test_extracts_text_from_real_print() -> None:
    text = PypdfTextExtractor().extract((FIXTURES / "print_3039.pdf").read_bytes())
    assert "Druk nr 3039" in text
    assert len(text) > 5000
    assert text.count("\f") >= 5  # pages stay separated for the section trimmer


def test_budget_passthrough_when_short() -> None:
    result = TextBudget(100).apply("short")
    assert result.text == "short" and not result.truncated


def test_budget_keeps_head_and_justification() -> None:
    act = "Art. 1. " * 500
    justification = "\nUzasadnienie\n" + "Projekt ma na celu. " * 500
    result = TextBudget(2000).apply(act + justification)
    assert result.truncated
    assert result.text.startswith("Art. 1.")
    assert "Uzasadnienie" in result.text
    assert len(result.text) <= 2000 + 40  # marker allowance


def test_budget_plain_cut_without_justification() -> None:
    result = TextBudget(50).apply("x" * 500)
    assert result.truncated and len(result.text) == 50
