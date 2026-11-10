import json

from lexinform.adapters.review_prompts import review_prompt
from lexinform.models import ReviewContext, ReviewDocument


def test_consultation_prompt_keeps_duplicate_source_metadata_without_repeating_its_text() -> None:
    original = ReviewDocument(
        identity="rcl/780621",
        url="https://rcl.test/780621",
        role="response",
        text="Uwaga nieuwzględniona.",
        sha256="a" * 64,
        normalized_sha256="b" * 64,
        file_sha256="c" * 64,
    )
    alias = original.model_copy(
        update={"identity": "rcl/780641", "url": "https://rcl.test/780641", "author": "Minister"}
    )
    independent = alias.model_copy(update={"identity": "rcl/42", "file_sha256": "d" * 64})
    context = ReviewContext(
        number="RCL/12398102",
        title="Projekt ustawy",
        purpose="consultation_review",
        documents=(original, alias, independent),
    )

    first, copied, other = json.loads(review_prompt(context))["documents"]

    assert first["text"] == other["text"] == original.text
    assert "text" not in copied
    assert (copied["identity"], copied["same_text_as"]) == (alias.identity, original.identity)
    assert (copied["url"], copied["author"]) == (alias.url, alias.author)
    assert context.documents[1].text == original.text
