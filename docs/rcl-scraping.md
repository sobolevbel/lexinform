# Scraping RCL and the wykaz prac legislacyjnych RM

Extracted from `CLAUDE.md` on 2026-09-14: markup, file formats, letters and probe results for
`adapters/rcl_html.py`, `adapters/doc_text.py` and `adapters/wykaz_csv.py`. Read it when touching
those; the invariants that govern *when* they run stay in `CLAUDE.md`.

## RCL lessons (verified live, Sept 2026)

- `legislacja.rcl.gov.pl` has no API/RSS; unknown query params (`pSize=all`, `modifiedDateFrom`)
  and blocked clients get HTTP 200 with `<title>Request Rejected</title>`. Plain `curl` works from
  Poland. **GitHub-hosted runners cannot reach it at all** (verified 2026-09-09 with
  `.github/workflows/rcl-probe.yml`): DNS resolves to 157.25.193.140, the TCP SYN to :443 is
  dropped (45 s connect timeout, no SYN-ACK), for every User-Agent and every path, while
  api.sejm.gov.pl answers in 1.7 s from the same runner (Azure northcentralus, US). A network-level
  block of the IP range or the country, not the WAF. The first list request is therefore a 20 s
  single-attempt probe (`RclClient(probe_timeout=…)`), so a blocked run loses seconds, not four
  minutes. Reaching RCL from CI needs an EU egress (proxy or self-hosted runner).
- List: `/lista?typeId=2&sKey=modifiedDate&sOrder=desc&pSize=100&pNumber=N` (2619 bills; `pSize`
  10/50/100); wykaz numbers come as `UC164`, `UD424`, `UD 247`, `UDER66`, `UPRO6`.
- Project page: `div.rcl-title`, `div.info` rows (Wnioskodawca, Data utworzenia, Działy, Hasła,
  Status `otwarty`, Numer z wykazu, EU note, Kadencja `X`), timeline `ul.cbp_tmtimeline li[id]`
  with icon classes `cbp_tmicon_notstart` / `cbp_tmicon` (reached) / `cbp_tmicon_active`, "Data
  ostatniej modyfikacji", optional "rozpoczęcie"/"zakończenie" (unreliable). Stage 14 links
  `sejm.gov.pl/…?symbol=RPL&Id=RM-0610-139-26`. A quarter of the projects skip "Konsultacje
  publiczne" (only uzgodnienia + opiniowanie); every stage republishes the text in its own
  "Projekt" folder. ~10 s per page.
- Stage catalog `/projekt/{id}/katalog/{stageId}`: `div.clearbox > ul > li.childdir` folders
  ("Projekt", "Pisma kierujące…", "Stanowiska zgłoszone…", "Odniesienie się wnioskodawcy…"),
  `li.doc > a[href=/docs//…/dokumentN.ext]`. Files in "Projekt" folders (40 projects, Sept 2026):
  PDF 40%, DOCX/DOCM 43%, ZIP 7% (the whole package in one archive), legacy DOC 6%
  (`adapters/doc_text.py`, an [MS-DOC] piece-table parser over `olefile` reading the document, its
  footnotes and its endnotes, and the one paragraph property that tells the end of a table row from
  the end of a cell — Word writes both as 0x07, so without it a tabela zgodności arrives as one
  line of tabs), ODT rare; XLSX/MSG/XADES/RTF are tables of comments, e-mails, signatures and
  reports, not bill texts. Display names often lack the extension; the URL carries it. Unknown or
  damaged files fall back to metadata-only analysis. RCL's OSR is a separate Word form starting
  with "Nazwa projektu"; point numbers are list formatting, so `sections._OSR_CUT_RE` accepts the
  heading without "6.".
- **An archive is never sent as an archive, and what a file is is read from the file, not from its
  name.** A package is a "Projekt" folder in a file: it is unpacked, every member is read and asked
  what it is (`sections.document_kind`), and only the best bill, uzasadnienie and OSR go to the
  model (`document_text._pick_parts`). A nested archive is opened only when the bill is not outside
  it — the three seen were bundles of draft regulations, and the "letter.pdf + projekt.zip" shape
  is what the exception is for. Measured over the packages of seven followed projects and 45 prints
  of term 10 (13 Sept 2026), the name is the thing that lies: `projekt.docx`, `uzasadnienie.docx`
  and `OSR.doc` inside `akty_wykonawcze_ETIAS.ZIP` are draft **rozporządzenia**, `opiniaUE.pdf` and
  `Minister Zdrowia UD439 na SKRM.pdf` are letters, `Lista_kontrolna_na_KRMC_-_etias_.DOCX` is a
  checklist — and every one of them passed as the bill until its own opening was read. Archives
  written on Windows carry cp437 file names (`zaêÑcznik nr 2.docx`), one more reason no rule may
  rest on a name alone. The names still narrow the candidates before a download, and they stay
  measured, not guessed: an appendix is ruled out *before* the OSR is recognised, or "załącznik do
  OSR" stands in for it, and no pattern may be a word a bill can carry in its own subject —
  "protokół" of a ratification, "raportowanie" of a reporting duty, "opiniowanie" the stage a bill
  is published for (which is why "opinia" is word-bounded). UC104's package: 646k characters →
  338k. Run over the "Projekt" folders of 80 harvested projects (13 Sept 2026) the name rule finds
  the bill in all 80, and the three it got wrong before are what the last patterns are for: an
  **autopoprawka** is an amendment to the government's own bill and was winning over it (a PDF
  outranks the package the bill comes in), an "opinia RL" and a "materiał uzupełniający" likewise,
  and one ministry files each part as an attachment to its letter and says which is which in a tag
  — `załącznik do pismo 07.08.2026 uzgodnienia [projekt].pdf`. The tag beats the rest of the name,
  being the one part of it about the document rather than its envelope. A file that opens as an
  appendix is refused at the other end too (`AnalysisService._load_text`): describing a compliance
  table would describe the wrong document with every appearance of describing the right one. A
  *letter* is not refused there — every print opens with one.
- **A heading is matched on its whole line, and where the case is data it is respected.**
  `document_kind` reads the first 1,200 characters. `USTAWA` and `ROZPORZĄDZENIE` are matched in
  either case but **anchored at both ends**, and the anchor is what does the work: the
  justification of every act implementing an EU regulation wraps onto a line beginning
  "rozporządzenia 2018/1240", and an unanchored pattern read UC104's uzasadnienie as a draft
  regulation. Over the 168 openings of the fixture, ignoring case moves exactly one document, and
  it moves it right — a draft headed "Rozporządzenie" in title case that had passed for a bill's
  uzasadnienie on its file name alone. `Nazwa projektu dokumentu` is a tabela legislacyjna, one
  word from the OSR's `Nazwa projektu`. A kind we do not know is `unknown`, never an appendix: the
  file name decides then, as it did before, unless the file is longer than
  `LEXINFORM_MAX_PART_CHARS` (300k) — the longest real document measured is 161,678 characters and
  the longest nameless appendix 954,730.
- **A table of provisions is known by its columns, not by its title.** `Tytuł projektu` opens an
  OSR form and `TYTUŁ PROJEKTU` a tabela zgodności, but the case does not settle it and the title
  never did: UD439's OSR opens "Tytuł projektu" and druk 2670's "Tytuł projektu: ustawa o zmianie
  ustawy – Kodeks wyborczy", while druk 1430's derivation table heads itself "Tabelaryczne
  zestawienie przepisów rozporządzenia wykonawczego Komisji (UE) 2023/564" and only says "Tytuł
  projektu:" further down the same page. Matching case-blind threw both OSRs away — 78,281
  characters of UD439's, all of druk 2670's, and those are the documents that count who is
  affected. What separates them is that one is a table: `Tabelaryczne zestawienie przepisów`,
  `Jedn. red.` and `Treść przepisu` are its column headings and appear in no OSR form.
- **What the rule was measured on is checked in.** `tests/fixtures/rcl/openings.json` holds the
  opening of 147 real documents with the kind each must be recognised as, and one table test runs
  `document_kind` over the lot. A new case is one row. It is a regression set and not the corpus:
  `../lexinform-corpus/candidates/openings-candidate.json` holds all 21,071 openings collected,
  labelled by the current code — which is why they are copied in by hand and read first, never
  generated into the repo.
- Consultation letters give a relative deadline ("w terminie 7/14 dni od dnia otrzymania
  niniejszego pisma", 30 for social partners), often no date (electronic time stamp) and the e-mail
  for comments ("na adres: …"). `rcl_letters.parse_letter` reads them; the deadline counts from the
  letter date or the day the letter appeared on RCL. Every project also has a comment form
  `/projekt/{id}/komentarz` (captcha).
- Join: `/processes` `rclNum="RM-0610-81-26"`, `rclLink=…/getIdFromLegislacja?number=…` → 302 to
  `/projekt/{id}`.


## Wykaz prac RM lessons (verified live, 12 Sept 2026)

- The register page `gov.pl/web/premier/wplip-rm` renders client-side; the whole register is one
  file, `gov.pl/register-file/Rejestr_{id}.csv`, and the id is in the page as `registerVue-{id}`
  (20874195 then). 10.5 MB, 2.8 MB gzipped, 1454 rows, `;`-separated with quoted multi-line
  paragraphs. `ETag` and `If-None-Match` work (304, 0 bytes), but there is nowhere to keep the tag,
  so every run downloads it.
- 19 columns; `Rodzaj dokumentu` splits 775 Projekty ustaw / 357 rozporządzeń / 322 inne. Statuses:
  403 empty (in progress), 1018 Zrealizowany, 31 Wycofany, 2 Niezrealizowany. Of the bill entries
  ~19 match the project's keywords in the whole register — 2–5 a year.
- `Data publikacji` is the **first** publication and does not move when an entry is edited; the
  entry's own gov.pl page keeps a version history instead (UD408 is at 2.0, edited 18.08.2026).
- Numbers are all but unique: UC168 is the same project entered twice a day apart, two `Podgląd`
  URLs. The later publication wins.
- Header wording drifts ("o przyczynach i potrzebie **wprowadzenia** rozwiązań" in one export,
  without it in another) and two headers start with "Organ odpowiedzialny", so columns are matched
  by the longest prefix.
- Lead time over RCL, measured: UD408 (o zmianie ustawy o cudzoziemcach, MSWiA) entered the
  register 2026-05-12 and appeared on RCL 2026-07-06 (`/projekt/12412103`) — 55 days. RCL shows its
  number as "UD 408", with a space.
- `www.gov.pl` resolves to one Polish address (185.32.48.49), not a CDN — the shape that turned out
  to be blocked for RCL — but **GitHub-hosted runners reach it fine** (verified 2026-09-12 with
  `.github/workflows/wykaz-probe.yml`, Azure eastus2): the page answers in 0.57 s, the CSV in 10.1
  s uncompressed and 3.1 s / 2.8 MB with `Accept-Encoding: gzip`, which httpx sends by default. So
  no proxy is needed; `LEXINFORM_WYKAZ_PROXY_URL` stays as the escape hatch if that ever changes.
- The stage is genuinely actionable: art. 7 ust. 1 of the ustawa o działalności lobbingowej — "z
  chwilą udostępnienia w BIP programów prac legislacyjnych … **każdy** może zgłosić zainteresowanie
  pracami nad projektem", with the organ that prepares it; art. 8 ust. 2 makes the filing the
  ticket to the Sejm's wysłuchanie publiczne. Caveat to check before the card names a form: the RM
  regulation with the official form (Dz.U. 2011/1080) was repealed on 2026-08-28 by Dz.U. 2026/160,
  so the mechanics must be read from the consolidated text (Dz.U. 2026/936).

