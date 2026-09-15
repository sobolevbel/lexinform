"""Who the register means by `MSWiA`.

The register names the responsible organ by an abbreviation and nothing else, and «отправить в
MSWiA» tells a reader neither what the office is called nor where to find it. The names and
addresses are the government's own list (`gov.pl/web/gov/ministerstwa`, 15 Sept 2026).

An organ that is not a ministry the register already names in full and it passes through
unchanged; an abbreviation from a government reorganised since (`MP`, `MN`) keeps its name
without an address, a dead link being worse than none. There is no per-ministry page for the
art. 7 zgłoszenie — "Działalność lobbingowa" is about art. 18 reports — so the address is the
ministry's own site.
"""

from typing import NamedTuple

__all__ = ["MINISTRIES", "Ministry", "ministry_name", "ministry_url"]


class Ministry(NamedTuple):
    """`url` is None for an office that no longer has a site of its own."""

    name: str
    url: str | None


_GOV = "https://www.gov.pl/web/"

MINISTRIES: dict[str, Ministry] = {
    "MAP": Ministry("Ministerstwo Aktywów Państwowych", f"{_GOV}aktywa-panstwowe"),
    "MC": Ministry("Ministerstwo Cyfryzacji", f"{_GOV}cyfryzacja"),
    "ME": Ministry("Ministerstwo Energii", f"{_GOV}energia"),
    "MEN": Ministry("Ministerstwo Edukacji Narodowej", f"{_GOV}edukacja"),
    # The ministry was renamed when it took over gospodarka; both abbreviations are in the
    # register and both lead to the same site.
    "MF": Ministry("Ministerstwo Finansów", f"{_GOV}finanse"),
    "MFiG": Ministry("Ministerstwo Finansów i Gospodarki", f"{_GOV}finanse"),
    "MFiPR": Ministry("Ministerstwo Funduszy i Polityki Regionalnej", f"{_GOV}fundusze-regiony"),
    "MI": Ministry("Ministerstwo Infrastruktury", f"{_GOV}infrastruktura"),
    "MKiDN": Ministry("Ministerstwo Kultury i Dziedzictwa Narodowego", f"{_GOV}kultura"),
    "MKiŚ": Ministry("Ministerstwo Klimatu i Środowiska", f"{_GOV}klimat"),
    "MN": Ministry("Ministerstwo Nauki", None),
    "MNiSW": Ministry("Ministerstwo Nauki i Szkolnictwa Wyższego", f"{_GOV}nauka"),
    "MON": Ministry("Ministerstwo Obrony Narodowej", f"{_GOV}obrona-narodowa"),
    "MP": Ministry("Ministerstwo Przemysłu", None),
    "MRiRW": Ministry("Ministerstwo Rolnictwa i Rozwoju Wsi", f"{_GOV}rolnictwo"),
    "MRiT": Ministry("Ministerstwo Rozwoju i Technologii", f"{_GOV}rozwoj-technologia"),
    "MRPiPS": Ministry("Ministerstwo Rodziny, Pracy i Polityki Społecznej", f"{_GOV}rodzina"),
    "MS": Ministry("Ministerstwo Sprawiedliwości", f"{_GOV}sprawiedliwosc"),
    "MSiT": Ministry("Ministerstwo Sportu i Turystyki", f"{_GOV}sport"),
    "MSWiA": Ministry("Ministerstwo Spraw Wewnętrznych i Administracji", f"{_GOV}mswia"),
    "MSZ": Ministry("Ministerstwo Spraw Zagranicznych", f"{_GOV}dyplomacja"),
    "MZ": Ministry("Ministerstwo Zdrowia", f"{_GOV}zdrowie"),
}


def ministry_name(organ: str) -> str:
    """The full name of the organ the register abbreviates, or what the register itself says."""
    known = MINISTRIES.get(organ.strip())
    return known.name if known else organ.strip()


def ministry_url(organ: str) -> str | None:
    """The organ's own site, when it is a ministry the table knows and the site is alive."""
    known = MINISTRIES.get(organ.strip())
    return known.url if known else None
