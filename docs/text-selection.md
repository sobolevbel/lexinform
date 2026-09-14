# Text selection: what of a document reaches the model

Extracted from `CLAUDE.md` on 2026-09-14 so that the rules of `sections.py`, `document_text.py` and
`AnalysisService._load_text` are read when those modules are touched and not in every session.
Nothing here is new; every number was measured on `../lexinform-corpus` (`CLAUDE.md` says what the
corpus holds and how to re-run a measurement).

## Scanned paper, and a covering letter is not the document it transmits

Measured with the project's own extractor (term 10, 12 Sept 2026): of 66 documents filed to prints
**none** carries readable text — 55 have no text layer, 11 hold the Prime Minister's letter and
nothing else (700–820 characters naming the bill and who will present the position, never what it
is); of 39 prints, 11 are scans and druk 604 is its letter and the signatures under it. "None" held
for those 66 and not for the population: over 570 filings (14 Sept 2026) 521 have no text layer and
39 more are a covering letter, but **ten carry a document** — `1319-001`, `1528-004`, `3033-001`,
`2883-005` are the OSR asked of a deputies' bill (56k–80k characters over 19–25 pages) and `439-s`,
`494-s`, `1676-s` the government's position, the two kinds the channel is told about, so
`digest_supplement` sometimes gets real text instead of paying per page. Those 800 characters
passed `MIN_TEXT_CHARS` and read like a document, so `sections.carries_the_document` cuts the
letter (at the page break or the heading after it, `without_cover_letter`), asks whether anything
is left, then asks the same of the paper it came from: a text thinner than 300 characters a page is
a photograph of pages, not their text (30 prints drawn at random from term 10, 2026-09-12: the scan
among them runs 139 characters a page, the thinnest real document 477, the median ~2,200). **And it
is judged at any length** — a 4,000-character ceiling used to disable the density check, on the
reasoning that an appendix-heavy print extracts to little and is still text; term 10 says otherwise
(what runs long and thin is a scan's OCR layer), and the ceiling let **30 prints** through as
`text`: druk 703 is 155 pages with text on three (6,865 characters, diacritics gone: "norki
amerykanskiej"), druk 204 is 268 pages with ten, druk 348 is 362 with sixteen, each read by the
model as that fragment with nothing on the card to say so. They are scans now
(`SCANNED_TEXT_CEILING` bounds nothing and is kept for the record); 204 and 348 are more pages than
`LEXINFORM_MAX_ANALYSIS_COST_USD` buys and are refused rather than half-read, the price of the rule
and decided with that in view. **`MIN_CHARS_PER_PAGE` is a proxy and the prints crowd right against
it** — 306.2 characters a page for the thinnest kept (druk 1625), 299.9 for the densest rejected
(druk 205) — so the seam was checked by a signal that does not depend on it: walking the page's
`/XObject` tree recursively, 205 carries a full-page raster on 6 of its first 10 pages and 1625 on
none, and the same check confirms druk 386 (278.6, its OCR reading like clean Polish) is really a
scan, which reading the text cannot tell you. It does **not** catch druk 348 (362 pages, text on
16, no raster — vector, or a font pypdf will not decode), so density stays the broader net and
structure is the confirmation, not the replacement (`pdf_scan_classification_en.md` in the corpus
sets out how to do it properly; PyMuPDF is AGPL, its own decision). The gate sits in
`AnalysisService._load_text`, the one place every document the model reads passes through, so the
rule is one and not four. A file with no text but with pages goes to the model as pages
(`ScannedDocument`, `text_source="scan"`, the API's document block — ~1,600 tokens and $0.008 a
page, which `pricing.estimate_scan_cost` guards by and prices before the call: asking the tokenizer
would mean uploading the file to learn a number we can multiply out), and what is kept of it is the
file's `sha256`, standing where a text's digest would. A file with neither text nor pages (a Word
file whose letter we could not cut off, an archive) keeps its text after all: there is nothing to
fall back to, and a document of that length is not a covering letter. The extracted text comes back
either way, because the letter is the one page of a scanned print with a text layer and the card's
club breakdown is parsed from it. Of the pages, only the letter's is dropped
(`sections.scan_page_window`: exactly one page in all 15 government prints measured, and only when
`sections.has_cover_letter` finds the transmittal formula — *any* text is not that finding, and a
page dropped on a running head would be a page of the bill). That is not truncation:
`ScannedDocument.truncated` counts only pages of the document itself the model was not shown
(`cover_letter_pages`), or the card's «неполный текст» and the prompt's instruction to lower
confidence would be on every scanned print there is. **Nothing else of a scan is trimmed, and the
reason is measured.** A filed OSR is not the government's 13-point form — druk 1273's is the Sejm's
own expertise (BEOS), sections I–XI, substantive from the first page, with the count of affected
foreigners on page 10 of 30 — so a page budget cuts into the substance. A page map by a cheap model
was built and removed on 2026-09-12: it labels the pages well (Haiku over the PDF, 30 pages,
$0.048) but keeps 25 of the 30, so the document costs $0.245 mapped against $0.236 whole. Mapping
pays only where the appendices dominate, and those prints — the government's — carry a text layer
and are trimmed by `trim_print` already; what reaches us as a scan is an old deputies' bill of a
few dozen pages that is substance throughout.

## What the model is sent, and why

- **The text layer is a trap** (the rule is in the scanned-paper invariant; the sample, 12 Sept
  2026: 66 filed documents — 0 with readable text, 55 empty, 11 cover-only — and 39 prints — 27
  text, 11 scans, 1 cover-only). The model reads such a file as a PDF document block: 32 MB of
  request (so ≤ 24 MB of file, base64 being a third larger; druk 2865 is 40 MB and does not fit)
  and 600 pages, ~1,600 tokens a page measured with `count_tokens` on druk 1273 (10 pages 16,157
  tokens, 30 pages 47,268, one page 1,622).
- **What `trim_print` meets in a print, measured over 45 of term 10 with a text layer** (13 Sept
  2026). Reading the sections by `document_kind` rather than by a handful of headings takes another
  **15%** off what the model is sent across the sample (7.65M characters kept → 6.48M), and the
  bill, its uzasadnienie and "Art. 1." survive in every one: druk 1479 keeps 35% of what it did,
  druk 1963 48%, druk 810 63%. The one print that keeps *more* is druk 2670, whose OSR was being
  thrown away whole. The OSR is cut at point 6 in 16 of the first 25 measured. Three of the 45
  carry **"DEKLAROWANE SKUTKI REGULACJI (DSR)"** instead of the OSR form — the deputies' version,
  15 pages of 60 in druk 2673 — and one heads its OSR "Tytuł projektu". Three was the sample: over
  all 938 bill prints of term 10 (14 Sept 2026, `page_index.json.gz` in the corpus) **180** open a
  page with the DSR, a form every fifth print carries. **And the form was being dropped on 14 of
  them**: it is itself an attachment to a resolution of the Presidium of the Sejm, so it opens
  "Załącznik / do uchwały nr 51 / Prezydium Sejmu" and names itself only on the line after, while
  `_ANNEX_RE` matches the first two lines (`\s+` spans the break). `page_kind` therefore looks for
  the DSR heading past the two-line window — the one heading it does, because widening the window
  is what druk 810 page 40 forbids — and the fix moved exactly 21 pages of 84,422 (14 `annex`, 7
  `unknown`), costs +0.25% of the text sent over the whole term, and kept 40,758 characters of druk
  1963 that used to go. In the whole corpus every "Załącznik do uchwały" is this masthead and
  nothing else. The DSR is recognised as the OSR section but **not cut**, on purpose: it has no
  fixed thirteen points and so no "point 6" to cut at, and its own headings are where its substance
  is ("Podmioty, na które wpływa projekt", "Wpływ projektu na wskazane podmioty" in druk 3035) —
  the part of an OSR this channel reads it for; cutting at a guessed heading would take that and
  leave the rest. One print (druk 1764) puts its club's name and site as the first line of all
  thirty pages, which stood in front of every section heading the trimmer looks for, so a page's
  running head and its number are stripped before it is classified
  (`sections.strip_page_furniture`). A bare "Załącznik" is deliberately *not* a section start: druk
  2673 carries one on page 25 of 60 as a schedule of its own bill, and cutting there would take the
  rest of the bill with it; only "Załącznik do uchwały/rozporządzenia/raportu" is one.
- **A section heading opens a page; one found inside a page is prose that wrapped that way.**
  `_section_start` reads `document_kind` over the first **two non-empty lines** of the
  furniture-stripped page (`_page_opening`), not over its first 600 characters — `document_kind`
  searches rather than anchors, so anywhere in 600 characters means anywhere at all. Druk 810 page
  40 is the bill ("Art. 156q. 1. Prezes Urzędu … w części A") and wraps so that its third line
  begins "załącznika do rozporządzenia nr 2019/947/UE": **119,420 characters of the bill were
  dropped as an appendix** and the model never saw them. Druk 545 page 10 lost its OSR to "Zgodnie
  z art. 5 ustawy … o działalności lobbingowej" at character 298, druk 1638 the same way. One line
  is too few (the corpus then keeps 620k characters of appendices that name themselves on the
  second line, "Projekt" over "R O Z P O R Z Ą D Z E N I E"), three already reaches druk 810's
  wrap. **What stands above the heading is furniture, and widening the window is the wrong way to
  reach past it.** A ministry stamps a draft with its date and the committee it is going to —
  "Projekt z dnia 9 lipca 2026 r." over "Etap: materiał informacyjny na SKRM" — and two such lines
  put the heading on the third, out of the window. Measured over `openings.json` (13 Sept 2026) the
  narrowing moved 12 of 147 documents to `unknown`, nine of them by the stamp and five of those the
  draft regulations of the ETIAS package — and for a regulation the cost is not a mislabelling:
  `_section_start` latches on `REGULATIONS` and drops everything after it, so a block read as
  `unknown` never latches and each draft's own OSR form (`Nazwa projektu`, a kind that is kept)
  re-opens the run and goes to the model. `strip_page_furniture` takes the stamp off the top the
  way it takes a running head, and the window lands on the heading; `page_kind` is the whole
  reading in one place. The two openings that stay `unknown` are a letterhead and a signature
  block, which say what they are in their body and not at their top — which is what the window is
  for.
- **A page break is a line break, and Python's anchors do not know it.** `^` and `$` in MULTILINE
  turn on `\n` alone; the pages are joined with `\f` and the extractor emits a page from its first
  glyph, so a print whose justification opens a page reads `…\fUZASADNIENIE\n` with no newline in
  front of the heading. Every line-anchored pattern in `sections` was blind to it. Measured over
  term 10 (14 Sept 2026): **683 of the 819 prints with a text layer hide at least one heading
  behind the form feed, and in 279 `_JUSTIFICATION_RE` finds no justification anywhere**. That is
  the one search `excerpts` makes over the joined text, so those 279 went to the triage — and to
  the cut a text over the per-bill limit is reduced to — as the head of the bill and not one line
  of the reasons for it. `_BOL`/`_EOL` are the rule in one place; teaching both anchors about the
  page break recovered 744,693 characters, and closed the same hole for 525 documents of RCL, where
  DOCX is worse still because `document_text` collapses `\n\f\n` to `\f` on purpose.
- **The opening of a document is a page, and the first page that speaks wins.** `HEAD_CHARS` is a
  budget spent page by page, not a window over the joined text: a bill's first page on RCL runs
  550–1,150 characters, so a flat 1,200 read on into the second page where the uzasadnienie begins,
  and `_KINDS` tries `justification` before `bill`. Over the 9,208 documents of the corpus that
  cost **66 bills**, every one headed USTAWA on its own first page — and in `_pick_parts` such a
  member fills the justification role and the archive is left with no bill at all. A page that says
  nothing hands the budget on: 26 documents open on a ministry's stamp or a title sheet and name
  themselves on the page after.
- **After the OSR, no page is the bill or its uzasadnienie again.** A print runs letter, bill,
  uzasadnienie, OSR, appendices, in that order and once each. Every table of submitted comments
  labels each row "Uzasadnienie", so a page of one read as the bill's own justification and
  re-opened the kept run: druk 1424 sent **270,989** characters of a consultation table to Opus
  that way and druk 1677 **317,546**. The rule is tied to the OSR and not to the first dropped
  section because druk 810's uzasadnienie stands *before* its OSR, behind an appendix wrongly
  detected in front of it, and a blunter rule would lose it. Druk 1677 page 98 is why both rules
  are needed: it genuinely opens "Uzasadnienie", so no window saves it.
- **The OSR is cut at point 5, not point 6.** Point 5 ("Informacje na temat zakresu, czasu trwania
  i podsumowanie wyników konsultacji") is the roll of organisations the draft was sent to — 5,136
  characters in druk 1677, 10,187 in druk 1479 — and names nobody the bill affects; point 4
  ("Podmioty, na które oddziałuje projekt"), the one count of the affected a print gives, survives
  in all 26 prints of the corpus that have a point 5. Point 6 stays as the fallback. ~5k a print,
  ~2–3k an RCL package.
- **Net over the 45 prints (13 Sept 2026): 6,437,943 characters kept → 5,929,867.** The sum is not
  the point: it is ~588k of appendices out and ~157k of real bill text back in. The pages these
  rules were measured on are checked in as `tests/fixtures/sejm/page_starts.json` — **21 rows**,
  the individual pages each rule was derived from, not a page corpus. The page corpus is
  `../lexinform-corpus/sejm/term10/page_index.json.gz`: 84,422 pages of 914 prints, one row each
  with the kind `page_kind` gives it, built by the same call the fixture is (druk 810's pages 40,
  113, 182 and 388 come out identical). Asked of it (14 Sept 2026), **no print of term 10 now has
  an appendix detected before its uzasadnienie** — the druk 810 failure is closed across the term
  and not only on the print it was found on.


