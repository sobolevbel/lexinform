import pytest
from pydantic import ValidationError

from lexinform.models.digest_content import DigestRenderedPart, digest_event_owner_sha256
from lexinform.telegram_text import MESSAGE_LIMIT


def test_rendered_part_bounds_exact_html_with_markup() -> None:
    text = "<b>" + "x" * (MESSAGE_LIMIT - len("<b></b>")) + "</b>"
    assert (
        DigestRenderedPart(
            publication_id=1, text=text, event_owner_sha256=digest_event_owner_sha256([])
        ).text
        == text
    )
    with pytest.raises(ValidationError, match="Telegram or event bounds"):
        DigestRenderedPart(
            publication_id=1,
            text=text.replace("</b>", "x</b>"),
            event_owner_sha256=digest_event_owner_sha256([]),
        )
