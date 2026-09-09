"""Keyword prefilter: cheap, deliberately over-inclusive first pass before the LLM.

Patterns are Polish word stems anchored with word boundaries so that e.g. `wiz` does not match
`wizja` and `granic` does not match `ograniczeniu`. The LLM makes the final relevance call, so
false positives here only cost an LLM request, while false negatives lose a bill forever.
"""

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
    _p("wizy",               r"\bwiz(a|y|ie|ę|ą|om|ach|ami|ow\w*)?\b"),  # incl. bare "wiz"
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
    # Non-Polish citizens named by their citizenship rather than as "cudzoziemcy": EU citizens
    # (free movement, local elections), third-country nationals (entry bans).
    _p("obywatele_innych_panstw",
       r"\bobywatel\w*\s+(unii\s+europejskiej|ue|pa[ńn]stw\w*\s+(cz[łl]onkowsk|trzeci)\w*)\b"),
    # Work of foreigners without the word: only foreigners need a work permit, a seasonal work
    # permit or an employer's declaration (oświadczenie o powierzeniu wykonywania pracy).
    _p("zezwolenie_praca",   r"\bzezwoleni\w*\s+na\s+prac[ęe]\b"),
    _p("praca_sezonowa",     r"\bprac\w*\s+sezonow\w*"),
    _p("oswiadczenie_powierzenie", r"\bo[śs]wiadczeni\w*\s+o\s+powierzeniu\b"),
    _p("uznawanie_kwalifikacji", r"\buznawani\w*\s+kwalifikacji\b"),
    _p("studenci_zagraniczni", r"\bstudent\w*\s+zagraniczn\w*"),
    _p("nierezydenci",
       r"\bnierezydent\w*|\brezydencj\w*\s+podatkow\w*|\bcertyfikat\w*\s+rezydencji\b"),
    _p("przekraczanie_granicy", r"\bprzekracza\w*\s+granic\w*"),
)
# fmt: on

# Authorities and places that turn up in bills about customs, policing or inspections without the
# bill touching foreigners at all (a food-quality bill lists Straż Graniczna among inspectors),
# and "legalizacja", which in Polish law mostly means excise stamps, metrology or unpermitted
# buildings (an excise bill said it 140 times); a bill legalising someone's stay says
# "cudzoziemiec" too. "Nierezydent" is mostly a company in tax law; "przekraczanie granicy" turns
# up in customs and transport bills. Inside a full text they count only next to a strong
# pattern, and in a title they do not send the bill to the model on their own.
#
# Not patterns at all, on purpose: PESEL, mObywatel/ePUAP, NFZ, świadczenia (800+), prawo jazdy,
# szkolnictwo wyższe, Kodeks wyborczy. A bill changing these *for foreigners* says "cudzoziemiec"
# or "obywatel Ukrainy" and the text stage catches it; as title patterns they hit 4–14 unrelated
# bills each per term (measured on the 1500 processes of term 10), every one a full analysis.
WEAK_PATTERNS: frozenset[str] = frozenset(
    {
        "straz_graniczna",
        "granica_panstwowa",
        "schengen",
        "legalizacja",
        "nierezydenci",
        "przekraczanie_granicy",
    }
)


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

    def spans(self, text: str) -> list[tuple[int, int]]:
        """Character ranges of every hit in `text` (offsets are valid for the original text)."""
        haystack = self._normalize(text)
        return sorted(
            (m.start(), m.end()) for p in self._patterns for m in p.regex.finditer(haystack)
        )


def accept_title_hits(hits: list[str]) -> bool:
    """Whether keyword hits in a title (and description) send the bill straight to the model.

    A title hit costs a full analysis, so a weak pattern alone ("o Straży Granicznej") is not
    enough: such a bill goes to the text stage instead, where the weak hit counts next to a
    strong one.
    """
    return any(name not in WEAK_PATTERNS for name in hits)


def accept_text_hits(counts: dict[str, int], *, min_distinct: int, min_occurrences: int) -> bool:
    """Whether keyword hits inside a full bill text justify an LLM analysis.

    One stray "cudzoziemiec" in a 200-page tax bill is noise; several distinct topics, or the same
    topic repeated, is signal. Weak patterns (border authorities) never carry the decision alone.
    """
    if not counts or all(name in WEAK_PATTERNS for name in counts):
        return False
    return len(counts) >= min_distinct or sum(counts.values()) >= min_occurrences
