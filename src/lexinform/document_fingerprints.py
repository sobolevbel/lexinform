"""Versioned text identities shared by document consumers."""

import hashlib
import re

from lexinform.law_technical import DECORATED_PAGE

_WHITESPACE = re.compile(r"\s+")


def text_digest(text: str) -> str:
    """Versioned full-text identity; bare numeric lines may be table values."""
    normalised = _WHITESPACE.sub(" ", DECORATED_PAGE.sub("", text)).strip()
    return "text-v2:" + hashlib.sha256(normalised.encode("utf-8")).hexdigest()
