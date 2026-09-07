"""Keyword prefilter: cheap, deliberately over-inclusive first pass before the LLM.

Patterns are Polish word stems anchored with word boundaries so that e.g. `wiz` does not match
`wizja` and `granic` does not match `ograniczeniu`. The LLM makes the final relevance call, so
false positives here only cost an LLM request, while false negatives lose a bill forever.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class KeywordPattern:
    name: str
    regex: re.Pattern[str]


def _p(name: str, pattern: str) -> KeywordPattern:
    return KeywordPattern(name, re.compile(pattern, re.IGNORECASE | re.UNICODE))


# fmt: off
KEYWORD_PATTERNS: tuple[KeywordPattern, ...] = (
    _p("cudzoziemcy",        r"\bcudzoziem\w*"),
    _p("obcokrajowcy",       r"\bobcokrajow\w*"),
    _p("migracja",           r"\b(i|e)?migra(cj|nt|cyjn)\w*"),
    _p("azyl",               r"\bazyl\w*"),
    _p("uchodzcy",           r"\buchod[źz]\w*"),
    _p("repatriacja",        r"\brepatria\w*"),
    _p("obywatelstwo",       r"\bobywatelstw\w*"),
    _p("obywatele_ukrainy",  r"\bobywatel\w*\s+ukrainy\b"),
    _p("wizy",               r"\bwiz(a|y|ie|ę|ą|om|ach|ami|ow\w*)\b"),
    _p("zezwolenie_pobyt",   r"\bzezwoleni\w*\s+na\s+pobyt\w*"),
    _p("pobyt_kwalifikowany", r"\bpobyt\w*\s+(czasow|sta[łl]|rezydent|tolerowan|humanitarn)\w*"),
    _p("legalizacja",        r"\blegalizac\w*"),
    _p("karta_polaka",       r"\bkar[tc]\w*\s+polaka\b"),
    _p("straz_graniczna",    r"\bstra[żz]\w*\s+graniczn\w*"),
    _p("granica_panstwowa",  r"\bgranic\w*\s+pa[ńn]stw\w*"),
    _p("ochrona_miedzynarodowa", r"\bochron\w*\s+(mi[ęe]dzynarodow|czasow|uzupe[łl]niaj\w*)"),
    _p("ochrona_cudzoziemcow", r"\budzielani\w*\s+cudzoziemcom\s+ochrony"),
    _p("zatrudnianie_cudzoziemcow", r"\b(zatrudni|powierz)\w*\s+(pracy\s+)?cudzoziemc\w*"),
    _p("eurodac",            r"\beurodac\b"),
    _p("ees",                r"\bsystem\w*\s+wjazdu/wyjazdu\b|\bEES\b"),
    _p("schengen",           r"\bschengen\w*"),
    _p("deportacja",         r"\bdeportac\w*|\bzobowi[ąa]zani\w*\s+do\s+powrotu\b"),
    _p("wydalenie",          r"\bwydaleni\w*\s+(z\s+)?terytorium\b"),
    _p("nostryfikacja",      r"\bnostryfikac\w*"),
    _p("pracownicy_delegowani", r"\bdelegowan\w*\s+(pracownik|kierowc)\w*"),
    _p("nieruchomosci_cudzoziemcy", r"\bnabywani\w*\s+nieruchomo[śs]ci\s+przez\s+cudzoziemc\w*"),
    _p("prawnicy_zagraniczni", r"\bprawnik\w*\s+zagraniczn\w*"),
    _p("status_uchodzcy",    r"\bstatus\w*\s+uchod[źz]c\w*"),
    _p("integracja_cudzoziemcow", r"\bintegracj\w*\s+(cudzoziemc|migrant|uchod)\w*"),
)
# fmt: on


_NBSP = " "


class KeywordPrefilter:
    """Matches text against the keyword patterns and returns the names of those that hit."""

    def __init__(self, patterns: tuple[KeywordPattern, ...] = KEYWORD_PATTERNS) -> None:
        self._patterns = patterns

    @staticmethod
    def _normalize(text: str) -> str:
        return text.replace(_NBSP, " ")

    def match(self, *texts: str | None) -> list[str]:
        haystack = self._normalize(" \n ".join(t for t in texts if t))
        if not haystack.strip():
            return []
        return [p.name for p in self._patterns if p.regex.search(haystack)]

    def match_counts(self, *texts: str | None) -> dict[str, int]:
        """Pattern name -> number of occurrences, only for patterns that occur at all."""
        haystack = self._normalize(" \n ".join(t for t in texts if t))
        if not haystack.strip():
            return {}
        counts = {p.name: len(p.regex.findall(haystack)) for p in self._patterns}
        return {name: n for name, n in counts.items() if n}


def accept_text_hits(counts: dict[str, int], *, min_distinct: int, min_occurrences: int) -> bool:
    """Whether keyword hits inside a full bill text justify an LLM analysis.

    One stray "cudzoziemiec" in a 200-page tax bill is noise; several distinct topics, or the same
    topic repeated, is signal.
    """
    if not counts:
        return False
    return len(counts) >= min_distinct or sum(counts.values()) >= min_occurrences
