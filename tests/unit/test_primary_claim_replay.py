from collections import Counter
from pathlib import Path

import pytest
from tools.replay_primary_claims import EDITS, check_text, frozen_matcher, run

from lexinform.models.reviews import citation_document


def test_untrusted_baseline_fails_before_parsing_or_compiling() -> None:
    with pytest.raises(ValueError, match="Frozen review source SHA differs"):
        frozen_matcher(b"this is not trusted Python", b"also not Python")


@pytest.mark.parametrize("existing", [True, False])
def test_output_must_be_new_and_outside_corpus(tmp_path: Path, existing: bool) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    output = tmp_path / "existing.json" if existing else corpus / "new.json"
    if existing:
        output.write_text("preserved")

    with pytest.raises(ValueError, match="new and outside"):
        run(corpus, output)

    assert output.read_text() == "preserved" if existing else not output.exists()


def test_eligible_derivatives_check_all_classes_without_retaining_source_text() -> None:
    text = "Art. 1. Od 2027 r. pracownik może otrzymać 30 zł według art. 2."
    counts: Counter[str] = Counter()

    observed = check_text(text, "synthetic-test", citation_document, counts)

    assert observed["status"] == "checked"
    assert counts["parity_checks"] == 6
    assert counts["quote_present"] == 1
    assert counts["citation_mismatch"] == 5
    assert all(counts[name + "_negative_controls"] == 1 for name in EDITS)
    assert set(observed) == {"status", "text_sha256"}


def test_no_article_source_is_explicitly_excluded_without_retaining_text() -> None:
    counts: Counter[str] = Counter()

    observed = check_text(
        "Nie jest to tekst z artykułami.", "synthetic-test", citation_document, counts
    )

    assert observed["status"] == "no_art_locator"
    assert counts == {"excluded_no_art_locator": 1}
    assert set(observed) == {"status", "text_sha256"}


def test_output_symlink_is_rejected_before_reading_the_corpus(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    target = tmp_path / "uncreated.json"
    output = tmp_path / "report.json"
    output.symlink_to(target)

    with pytest.raises(ValueError, match="symbolic link"):
        run(corpus, output)

    assert not target.exists()
    assert output.is_symlink()
