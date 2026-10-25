"""Compatible plain Word view over ordered physical XML block records."""

import re
from collections.abc import Iterable

from lexinform.sections import PAGE_BREAK

_TIDY_PAGE_BREAK = re.compile(rf"\n?{PAGE_BREAK}\n?")


def legacy_word_text(blocks: Iterable[str]) -> str:
    return _TIDY_PAGE_BREAK.sub(PAGE_BREAK, "\n".join(blocks))
