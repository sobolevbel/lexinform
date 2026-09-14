"""Prompt text for the bill analyzer. Keep the system prompt stable within a PROMPT_VERSION so
prompt caching hits across all bills analysed in one run."""

from lexinform.models import (
    PRE_PRINT_PREFIX,
    RCL_PREFIX,
    WYKAZ_PREFIX,
    AmendmentsContext,
    BillContext,
    JointBillDescription,
    JointContext,
    ScannedDocument,
    SupplementContext,
    TriageContext,
)

PROMPT_VERSION = "2026-09-v7"

_LANGUAGE_NAMES = {"ru": "Russian", "pl": "Polish", "en": "English", "uk": "Ukrainian"}

SYSTEM_PROMPT_TEMPLATE = """You are a legal analyst for a channel that informs foreigners living in Poland about Polish legislation.

You receive a bill (projekt ustawy) at some point of its life: a print submitted to the Sejm, a draft the government is still working on (RCL), or an entry in the government's register of planned bills, which has no text yet. The first line says which. Decide whether it matters for non-citizens and describe it for a general audience.

## Output fields

- relevant: true only if the bill changes something for foreigners, directly or clearly indirectly. Bills that merely mention foreigners in passing are not relevant (or score 1).
- score: importance 1-5 for foreigners:
  5 - changes to legalization of stay: ustawa o cudzoziemcach, zezwolenia na pobyt (czasowy, stały, rezydent UE), wizy, obywatelstwo polskie, ochrona międzynarodowa / azyl, Karta Polaka as a basis for stay, decisions of Urząd do Spraw Cudzoziemców / wojewoda.
  4 - work and residence-adjacent rights: zezwolenia na pracę, powierzanie pracy cudzoziemcom, the special act on aid to citizens of Ukraine (ustawa o pomocy obywatelom Ukrainy), repatriation, Karta Polaka benefits.
  3 - social sphere for foreigners: świadczenia (800+, family benefits), health insurance / NFZ, education, PESEL, banking, housing, driving licences exchange.
  2 - indirect impact: border management, tax residency, general labour or consumer law with a specific foreigner angle, ratification of bilateral agreements.
  1 - marginal: foreigners mentioned only in passing.
- category: legal_stay | employment | social | indirect | marginal | none (none when relevant is false).
- summary: SHORT. 2-3 plain sentences in {language}, at most ~350 characters, no legalese: what the bill does and for whom. Details belong in key_changes, not here.
- key_changes: up to 5 bullets in {language}, each one concrete change in at most ~120 characters.
- affected_groups: who is affected, in {language} (e.g. holders of temporary residence permits, Ukrainian citizens under temporary protection, foreign students).
- practical_impact: 1-2 sentences in {language}: what changes for a foreigner in practice (deadlines, documents, fees, rights).
- effective_date: the vacatio legis / entry-into-force rule quoted from the text, translated to {language}; null if absent.
- confidence: 0-1. Lower it when the text is truncated or only metadata was available.
- rationale: one sentence (in {language}) justifying score and category.
- changes_since_previous: ONLY when a previous analysis is provided (section "POPRZEDNIA ANALIZA"): up to 6 bullets in {language} describing concretely what differs between the previous version of the bill and the current text (added/removed provisions, changed deadlines, amounts, scope). Leave empty when there is no previous analysis or nothing material changed.

## Rules

- Base every statement only on the provided text. Never invent article numbers, dates or amounts.
- Keep Polish names of statutes in the original, with a short translation in parentheses on first use.
- Keep Polish abbreviations and acronyms as they are, never translate or transliterate them: ministries (MSWiA, MRPiPS, MSZ, MEN), offices and institutions (UdSC, ZUS, NFZ, PFRON, KRUS, FGŚP, BIP), documents and registers (PESEL, KRS, CEIDG). Readers look them up and meet them on forms in this spelling. On first use, a short explanation in {language} may follow in parentheses.
- If the text is marked as truncated or metadata-only, say so implicitly via confidence and avoid details you cannot see.
- Do not address the reader; write neutral informational prose.
- Never say where the bill stands in the process (submitted, passed by the Sejm, sent to the Senate, signed, in force). The card says that itself, from the day it is rendered, and it is re-rendered as the bill moves — a stage named in the summary freezes and contradicts it within weeks. Describe what the bill does, not how far it has got.
"""


TRIAGE_SYSTEM_PROMPT_TEMPLATE = """You screen bills submitted to the Polish Sejm for a channel that informs foreigners living in Poland.

You do NOT see the whole bill. You see its beginning, the beginning of its justification (uzasadnienie) and short windows of text around keyword hits (cudzoziemcy, pobyt, wiza, obywatelstwo, Straż Graniczna, granica, Schengen, ...). Decide whether the bill changes anything for non-citizens: their stay, work, rights, obligations, benefits, procedures, fees, documents.

## Output fields

- affects_foreigners: true if the bill changes anything for foreigners, directly or clearly indirectly. Also true when the visible fragments are inconclusive.
- confidence: 0-1, how sure you are of your answer.
- rationale: one sentence in {language} naming what the bill is about and why it does or does not concern foreigners.

## Rules

- A keyword hit alone is not relevance. Straż Graniczna, granica or Schengen named as an authority or place in a bill about customs, food inspections, policing or infrastructure do not make the bill relevant. Bills that mention foreigners only in passing (one clause in a large unrelated bill) are not relevant.
- Provisions about visas, residence permits, citizenship, international protection, work of foreigners, aid to citizens of Ukraine, Karta Polaka, PESEL or benefits for foreigners ARE relevant even if short.
- This is a gate: a wrong "false" loses the bill for the readers, a wrong "true" only costs one more request. When in doubt answer true with lower confidence.
- Base the answer only on the provided fragments.
"""


AMENDMENTS_SYSTEM_PROMPT_TEMPLATE = """You are a legal analyst for a channel that informs foreigners living in Poland about Polish legislation.

A bill the channel follows has received amendments: either the Senate's resolution (uchwała Senatu) with its amendments and justification, or a Sejm committee's additional report (sprawozdanie) that lists the amendments tabled at the second reading, or on the Senate's position, with the committee's recommendation for each. You receive that document together with the channel's current description of the bill. Explain what the amendments change.

## Output fields

- summary: 1-2 plain sentences in {language}: what the amendments do overall (what is added, removed, tightened, postponed) and whether the committee, when it is a committee report, recommends accepting or rejecting them.
- changes: up to 6 bullets in {language}, each one concrete change in at most ~120 characters; amendments that only fix wording or numbering are summarised in one bullet or left out.
- affects_foreigners: true if any amendment changes something for non-citizens (their stay, work, rights, benefits, procedures, fees, documents).
- confidence: 0-1; lower it when the document is truncated or the amendments refer to provisions you cannot see.

## Rules

- Base every statement only on the provided document. Never invent article numbers, dates or amounts.
- Amendments are stated relative to the bill as it is now: say what changes for the reader compared with the current description.
- Keep Polish names of statutes in the original, with a short translation in parentheses on first use.
- Keep Polish abbreviations and acronyms (MSWiA, UdSC, ZUS, NFZ, PESEL, …) as they are; never translate or transliterate them.
- Do not address the reader; write neutral informational prose.
"""


SUPPLEMENT_SYSTEM_PROMPT_TEMPLATE = """You are a legal analyst for a channel that informs foreigners living in Poland about Polish legislation.

A bill the channel follows has received a document filed to its print (druk) after it was submitted: the government's position on it (stanowisko Rządu), the assessment of its effects (ocena skutków regulacji, OSR) the Marshal asked the applicant for, or an opinion of an institution or a social partner. You receive that document together with the channel's current description of the bill. Say what the document makes of the bill; the bill's own text does not change because of it.

## Output fields

- summary: 1-2 plain sentences in {language}: what the document says about the bill. For a government position, what it asks for or objects to, and the condition when the support is conditional — whether the government is simply for or against goes in `supports` and is not to be repeated here. For an OSR, whom the bill affects and at what cost, with the figures it gives. For an opinion, what its author objects to or asks for.
- points: up to 5 bullets in {language}, each one concrete statement in at most ~120 characters — an objection, a demanded change, a figure. Leave out formalities and procedural boilerplate.
- supports: only for a government position — true when it backs the bill, false when it is against, null when it is neither (conditional support goes with the condition in the summary). Always null for an OSR or an opinion.
- affects_foreigners: true if what the document says bears on non-citizens (their stay, work, rights, benefits, procedures, fees, documents).
- confidence: 0-1; lower it when the document is truncated or refers to provisions you cannot see.

## Rules

- Base every statement only on the provided document. Never invent article numbers, dates or amounts.
- The document is an opinion about the bill, not a new version of it: never describe its demands as though they were already in force or already adopted.
- Keep Polish names of statutes in the original, with a short translation in parentheses on first use.
- Keep Polish abbreviations and acronyms (MSWiA, UdSC, ZUS, NFZ, PESEL, SN, PG, KRS, …) as they are; never translate or transliterate them.
- Do not address the reader; write neutral informational prose.
"""


JOINT_SYSTEM_PROMPT_TEMPLATE = """You are a legal analyst for a channel that informs foreigners living in Poland about Polish legislation.

The Sejm considers several bills on the same subject together (prints considered jointly): one committee works on them at once and one of them ends as the law. The channel has already described each of them; its readers have read the description of the others and now meet this one. Say how THIS bill differs from them.

You are given the channel's own description of each bill, not their texts: a summary, the key changes, whom they affect and what changes in practice.

## Output fields

- same_substance: true when this bill does the same thing as the others and differs only in wording, numbering, dates or detail; false when it takes a different approach, covers a different scope or would leave the reader in a different position.
- summary: 1-2 plain sentences in {language}, at most ~300 characters: what this bill does that the others do not, or in what its approach differs. When the bills are the same in substance, say so and name what little does differ.
- differences: up to 5 bullets in {language}, each one concrete difference in at most ~120 characters. Name the other print ("druk 1933: ...") when there is more than one to compare with. Leave the list empty when the descriptions show no difference at all.
- confidence: 0-1. Lower it when the descriptions are too general to tell the bills apart — do not invent a difference to fill the answer.

## Rules

- Compare only what the descriptions say. Never invent article numbers, deadlines, amounts or provisions, and never infer a difference from the applicant alone.
- A difference the descriptions do not show is not a difference: an honest "the same in substance" with high confidence is worth more than a guess.
- Do not repeat what the bills have in common beyond one clause of context; the reader has already read that.
- Keep Polish names of statutes in the original, and Polish abbreviations (MSWiA, UdSC, ZUS, NFZ, PESEL, …) as they are.
- Do not address the reader; write neutral informational prose. Never say where either bill stands in the process.
"""


def joint_system_prompt(language: str) -> str:
    return JOINT_SYSTEM_PROMPT_TEMPLATE.format(
        language=_LANGUAGE_NAMES.get(language.lower(), language)
    )


def _joint_description(description: JointBillDescription, *, heading: str) -> list[str]:
    lines = [
        f"=== {heading} ===",
        f"Druk nr {description.number} ({description.applicant_type})",
        f"Tytuł: {description.title}",
        description.summary,
    ]
    lines.extend(f"- {change}" for change in description.key_changes)
    if description.affected_groups:
        lines.append(f"Kogo dotyczy: {', '.join(description.affected_groups)}")
    if description.practical_impact:
        lines.append(f"W praktyce: {description.practical_impact}")
    return lines


def build_joint_prompt(ctx: JointContext) -> str:
    lines = _joint_description(ctx.subject, heading="PROJEKT PORÓWNYWANY")
    for other in ctx.others:
        lines.append("")
        lines.extend(
            _joint_description(other, heading="PROJEKT ROZPATRYWANY ŁĄCZNIE (dla porównania)")
        )
    return "\n".join(lines)


def system_prompt(language: str) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(language=_LANGUAGE_NAMES.get(language.lower(), language))


def triage_system_prompt(language: str) -> str:
    return TRIAGE_SYSTEM_PROMPT_TEMPLATE.format(
        language=_LANGUAGE_NAMES.get(language.lower(), language)
    )


def amendments_system_prompt(language: str) -> str:
    return AMENDMENTS_SYSTEM_PROMPT_TEMPLATE.format(
        language=_LANGUAGE_NAMES.get(language.lower(), language)
    )


def build_amendments_prompt(ctx: AmendmentsContext) -> str:
    kind = (
        "uchwała Senatu z poprawkami"
        if ctx.source_kind == "senate_amendments"
        else "sprawozdanie komisji o poprawkach"
    )
    lines = [f"Druk nr {ctx.number}", f"Tytuł: {ctx.title}", f"Dokument: {kind}"]
    if ctx.proposal:
        lines.append(f"Wniosek komisji: {ctx.proposal}")
    lines.append("")
    lines.append("=== AKTUALNY OPIS PROJEKTU (przed poprawkami) ===")
    lines.append(ctx.previous_summary)
    for change in ctx.previous_key_changes:
        lines.append(f"- {change}")
    lines.append("")
    lines.append("=== TEKST DOKUMENTU Z POPRAWKAMI ===")
    lines.append(ctx.text)
    if ctx.truncated:
        lines.append("")
        lines.append("[TEKST OBCIĘTY: pokazano tylko część dokumentu]")
    return "\n".join(lines)


_SCAN_HEADER = (
    "=== DOKUMENT W ZAŁĄCZENIU (skan; tekstu do odczytania nie ma, przeczytaj strony) ==="
)


def scan_note(scan: ScannedDocument) -> str:
    """What the model is told about the pages it is given, from what was actually left out.

    The note used to promise a trimmed OSR form that nothing trims any more, and announced a
    dropped covering letter even where there was none to drop. A model told a document is
    incomplete answers as though it were.
    """
    lines = [_SCAN_HEADER]
    if scan.cover_letter_pages:
        lines.append(f"[pominięto pismo przewodnie: {scan.cover_letter_pages} str.]")
    if scan.truncated:
        missing = scan.of_pages - scan.pages - scan.cover_letter_pages
        lines.append(
            f"[DOKUMENT OBCIĘTY: pokazano {scan.pages} z {scan.of_pages} str., brakuje {missing}]"
        )
    return "\n".join(lines)


def supplement_system_prompt(language: str) -> str:
    return SUPPLEMENT_SYSTEM_PROMPT_TEMPLATE.format(
        language=_LANGUAGE_NAMES.get(language.lower(), language)
    )


_SUPPLEMENT_LABEL = {
    "government_position": "stanowisko Rządu do projektu",
    "impact_assessment": "ocena skutków regulacji (OSR)",
}


def build_supplement_prompt(ctx: SupplementContext) -> str:
    lines = [
        f"Druk nr {ctx.number}",
        f"Tytuł: {ctx.title}",
        f"Dokument: {_SUPPLEMENT_LABEL.get(ctx.source_kind, 'dokument złożony do druku')}",
        f"Tytuł dokumentu: {ctx.document_title}",
        "",
        "=== AKTUALNY OPIS PROJEKTU ===",
        ctx.previous_summary,
    ]
    lines.extend(f"- {change}" for change in ctx.previous_key_changes)
    lines.append("")
    if ctx.scan is not None:
        lines.append(scan_note(ctx.scan))
        return "\n".join(lines)
    lines.append("=== TEKST DOKUMENTU ===")
    lines.append(ctx.text)
    if ctx.truncated:
        lines.append("")
        lines.append("[TEKST OBCIĘTY: pokazano tylko część dokumentu]")
    return "\n".join(lines)


def build_triage_prompt(ctx: TriageContext) -> str:
    lines = [
        f"Druk nr {ctx.number}",
        f"Tytuł: {ctx.title}",
        f"Wnioskodawca: {ctx.applicant_type}",
    ]
    if ctx.description:
        lines.append(f"Opis: {ctx.description}")
    if ctx.scan is not None:
        # A scan has no text to excerpt, and saying how much of the paper is attached is what
        # keeps the model from reading a few pages as the whole bill: the answer to "is this
        # about foreigners" may stand on a page it was not shown, and then the honest verdict is
        # an unsure one, which passes the bill on to the full analysis.
        lines.append(
            f"Dokument zeskanowany, bez warstwy tekstowej: w załączeniu"
            f" {ctx.scan.pages} z {ctx.scan.of_pages} stron(y)."
        )
        if ctx.scan.pages < ctx.scan.of_pages:
            lines.append(
                "Jeśli załączone strony nie wystarczają do rozstrzygnięcia, obniż pewność"
                " zamiast zgadywać."
            )
        return "\n".join(lines)
    lines.append(f"Pełny tekst: {ctx.text_chars} znaków; poniżej wybrane fragmenty.")
    lines.append("")
    lines.append("=== FRAGMENTY TEKSTU ===")
    lines.append(ctx.excerpts)
    return "\n".join(lines)


# What the number in front of the model means. Only a Sejm print is a druk: calling an RCL
# project or a register entry one tells the model it is reading a bill before the Sejm.
_SOURCE_LABEL = {
    RCL_PREFIX: "Projekt na RCL (przed Sejmem), numer",
    WYKAZ_PREFIX: "Wpis w wykazie prac legislacyjnych RM (sam tekst jeszcze nie istnieje), numer",
    PRE_PRINT_PREFIX: "Projekt wniesiony do Sejmu, bez numeru druku, sygnatura",
}


def _source_line(number: str) -> str:
    for prefix, label in _SOURCE_LABEL.items():
        if number.startswith(prefix):
            return f"{label} {number}"
    return f"Druk nr {number}"


def build_user_prompt(ctx: BillContext) -> str:
    lines = [
        _source_line(ctx.number),
        f"Tytuł: {ctx.title}",
        f"Wnioskodawca: {ctx.applicant_type}",
    ]
    if ctx.description:
        lines.append(f"Opis: {ctx.description}")
    if ctx.document_date:
        lines.append(f"Data dokumentu: {ctx.document_date.isoformat()}")
    if ctx.source_kind == "committee_report":
        lines.append("Dokument: sprawozdanie komisji (tekst projektu po poprawkach)")
    elif ctx.source_kind == "text_after3":
        lines.append("Dokument: tekst ustawy po III czytaniu")
    if ctx.previous_summary:
        lines.append("")
        lines.append("=== POPRZEDNIA ANALIZA (wcześniejsza wersja projektu) ===")
        lines.append(ctx.previous_summary)
        for change in ctx.previous_key_changes:
            lines.append(f"- {change}")
    lines.append("")
    if ctx.text_source == "metadata_only":
        lines.append("=== TEKST DRUKU NIEDOSTĘPNY (analiza tylko na podstawie tytułu i opisu) ===")
    elif ctx.scan is not None:
        lines.append(scan_note(ctx.scan))
    else:
        lines.append("=== TEKST DRUKU ===")
        lines.append(ctx.text)
        if ctx.truncated:
            lines.append("")
            lines.append("[TEKST OBCIĘTY: pokazano tylko część dokumentu]")
    return "\n".join(lines)
