---
paths:
  - "src/lexinform/sections.py"
  - "src/lexinform/keywords.py"
  - "src/lexinform/pricing.py"
  - "src/lexinform/law_*.py"
  - "src/lexinform/document_*.py"
  - "src/lexinform/analysis_records.py"
  - "src/lexinform/adapters/pdf_text.py"
  - "src/lexinform/adapters/doc_text.py"
  - "src/lexinform/adapters/document_text.py"
  - "src/lexinform/adapters/llm_*.py"
  - "src/lexinform/services/documents.py"
  - "src/lexinform/services/sources.py"
  - "src/lexinform/services/text_prefilter.py"
  - "src/lexinform/services/analysis.py"
  - "src/lexinform/services/cost.py"
  - "src/lexinform/services/rcl_projects.py"
  - "src/lexinform/models/documents.py"
  - "src/lexinform/models/word_notes.py"
  - "src/lexinform/models/analysis.py"
  - "tests/scenario/test_text_prefilter.py"
  - "tests/scenario/test_analysis*.py"
  - "tests/scenario/test_law_*.py"
  - "tests/unit/test_sections*.py"
---

# Documents, text and cost

What of a document reaches the model, with the prints each rule was derived from:
`docs/text-selection.md`. Prices, guard rails and term cost: `docs/llm-cost.md`.

- **A missing file is not a verdict.** A print with no document yet leaves the row
  `text_prefilter_pending`, counted `unanswered` (like an orka WAF refusal or an initial orka 404); a
  text skip is only revisited on a title change (`discovery._ingest`) or by hand (`reprefilter
  --include-text-skipped`, `/unskip`). Only a source that cannot get a file later (an RCL project with
  nothing readable in "Projekt") is skipped.
- **Scans and covering letters.** `sections.carries_the_document` is asked by both prefilter and
  analysis: it cuts the transmittal letter (`without_cover_letter`) and judges density — under 300
  characters a page (`MIN_CHARS_PER_PAGE`) is a photograph. A textless file with pages goes to the
  model as pages (`ScannedDocument`, `text_source="scan"`, ~1,600 tokens and $0.008 a page, priced by
  `pricing.estimate_scan_cost`), keeping the file `sha256` where a text digest would stand; a file
  with neither keeps its text. Only the letter's page is dropped (`sections.scan_page_window` when
  `sections.has_cover_letter` finds the formula; not truncation — `ScannedDocument.truncated` counts
  only `cover_letter_pages` aside); nothing else of a scan is trimmed. The gate is
  `AnalysisService._load_text`, the one place every document passes. The prefilter sends an
  unsearchable scan to `analysis_pending` (`TextPrefilterResult.scans`, distinct from `unreadable` =
  no pages); skip reasons name the missed threshold. `TextLoader` alone calls a file textless only
  under `MIN_TEXT_CHARS`, which is why both stages ask `carries_the_document` instead. Before this, 91 of 938 term-10 prints (a
  700–1,200-char transmittal letter as their text layer) were skipped. Scans of term 10: 317
  documents, 7,763 pages. **A scan is always triaged** (`_worth_triaging`, whatever its text) on its
  first `triage_scan_pages` (8) pages cut from the downloaded bytes (`TextLoader.first_pages`, no
  second fetch): ~$0.044 a scan on Sonnet 5 (18/18 correct rejections on Haiku). The prompt states how
  many pages of how many are attached and asks for lower confidence instead of a guess; an unsure
  verdict passes the bill on. The per-bill guard stops a scan at ~250 pages.
- **A committee report is the bill only when it attaches one** — `proposal` "załączony projekt"
  (`Stage.carries_bill_text`); "odrzucić projekt ustawy" and "uchwalić projekt ustawy bez poprawek"
  attach none (else a bill would be re-analysed on the motion to reject it). What follows the
  committee is read from the print number (`Stage.is_additional_report`: every "Praca w komisjach po
  II czytaniu" carries an "-A" report, none after a first reading). `…-A` reports proposing "przyjąć
  poprawki" are amendment tables, not text.
- **RCL: the final stage does not carry the text.** "Skierowanie projektu ustawy do Sejmu" has only
  the covering letter. `with_text` walks back up to `TEXT_STAGE_ATTEMPTS` = **3** catalogs, stopping
  at the first with the text (newest stage has it in 26%, two reach 99.25%, three 99.75%).
- **RCL: the bill may be combined with its uzasadnienie or be a letter's appendix.** `text_role`
  tests "uzasad" first and drops `załącznik`, so such projects had no bill (9 of 824).
  `_bill_of_last_resort` runs only when the answer would be nothing, still refuses what `text_role`
  refuses (tabela, pismo, autopoprawka); a combined file is stored as the bill and dropped from
  extras, or the analysis pays for it twice.
- Package selection belongs to `document_candidates.select_document_parts`; `extract_parts` stays physical and must never call that policy or the legal classifier. `extract_document` is the compatible facade, with its explicit `max_part_chars` selection limit.
- **Memoized results** (`analysis_memo`) are keyed by bill identity, purpose, normalised text and
  comparison context (a scan by file digest and page window; triage has its own key), so a URL change
  never triggers a paid re-analysis; reused results charge no new tokens. Worker threads never write
  the repository.
- **Cost.** Opus 5.5 is $4/M input (older measurements: Opus 5 at $5); output ~1% of the bill. Texts ≥
  `triage_min_chars` are triaged on `llm_triage_model` first; a term of candidates costs ≈$44 because
  the triage rejects 62% (vs $118.83 untriaged: 268 keyword-passing prints, 132.9M chars → 47.5M after
  `trim_print`; bill 38.8%, uzasadnienie 45.2%, OSR points 1–4 15.9%). **A text over the per-bill
  limit is cut down by the keywords, not refused**; **a re-analysis is never refused** (refusals turn
  on `first`). Every call is recorded (`RunReport.llm_calls`, `AnalysisService._charge`). Rejected,
  not to revisit without new numbers: diffing RCL redactions (63–116% of lines differ),
  article-level selection (body is a median 27% of what is kept), changing `CHARS_PER_TOKEN` (2.0,
  verified 1.96–2.08), thinning the uzasadnienie (where the card gets "what this is for"), narrowing
  `azyl` (decides only druki 1861 and 1268; both narrowings are worse).
