"""pypdf extraction on a real Sejm print."""

from lexinform.adapters.pdf_text import PypdfTextExtractor
from tests.conftest import FIXTURES


def test_real_print_yields_text_with_page_breaks() -> None:
    text = PypdfTextExtractor().extract((FIXTURES / "print_3039.pdf").read_bytes())

    assert "Druk nr 3039" in text
    assert len(text) > 5000
    assert text.count("\f") >= 5  # pages stay separated for the section trimmer
