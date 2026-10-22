---
paths:
  - "src/lexinform/adapters/sejm_api.py"
  - "src/lexinform/adapters/orka.py"
  - "src/lexinform/adapters/browser_identity.py"
  - "src/lexinform/adapters/senat_html.py"
  - "src/lexinform/adapters/retries.py"
  - "src/lexinform/adapters/streams.py"
  - "src/lexinform/models/sejm.py"
  - "src/lexinform/models/senate.py"
  - "src/lexinform/agenda.py"
  - "src/lexinform/authors.py"
  - "src/lexinform/senate_documents.py"
  - "src/lexinform/services/signatories.py"
  - "src/lexinform/services/documents.py"
  - "src/lexinform/services/tracking/senate.py"
  - "src/lexinform/services/tracking/acts.py"
  - "src/lexinform/services/tracking/hearings.py"
  - "tests/integration/**"
  - "tests/unit/test_sejm*.py"
  - "tests/unit/test_orka*.py"
---

# Sejm API, orka, ELI and the Senate site (verified live)

- `/sejm/term`: `num`, `from`, `to` (absent while running), `current` (exactly one),
  `prints.count/lastChanged`. Term 10 began 2023-11-13; print numbers restart each term.
- `/processes` lists only printed bills. Pre-print bills are in `/bills` (`RPW/…`:
  `publicConsultationStart/EndDate`, `applicantType`, `status`, `print`, `consultationResults`).
  **`dateOfReceiptFrom` is exclusive** (B83): `iter_bills` passes the day before, so its `received_from` stays inclusive like the fake's. Consultations
  exist only for deputies', President's, Senate, committee and citizens' bills; government bills (541
  of 1,279 in term 10) were consulted on RCL. The opinion form is a survey at
  `opiniowanie.sejm.gov.pl/RPW-29075-2026` (RPW number with dashes; `models.consultation_survey_url`,
  linked directly); the Sejm page linking it (`www.sejm.gov.pl/Sejm10.nsf/agent.xsp?symbol=
  KONSULTOWANY_PROJEKT&NrProjektu=…`) is browser-only (F5 captcha). Sending needs a sign-in at
  `logowanie.sejm.gov.pl` (Profil Zaufany works); messages say nothing about it (2026-09-13).
- **RPW text is a PDF on orka.sejm.gov.pl** (`models.submission_pdf_url`, `LEXINFORM_ORKA_BASE_URL`),
  downloadable only through `adapters/orka.py`: Imperva refuses bot-like clients, so the client
  follows the 302 and keeps `visid_incap_*`/`incap_ses_*` cookies, and **header order is part of the
  shape** — `BROWSER_HEADERS` is ordered as Chrome sends them (`Connection`,
  `Upgrade-Insecure-Requests`, `User-Agent`, `Accept`, `Accept-Encoding`, `Accept-Language`);
  `browser_headers(accept=…)` replaces in place; a test guards the order. The first client served
  vouches for an address, so probes must start cold. Every client shares
  `adapters/browser_identity.py` (Chrome 140 on Windows; WAFs score client kind, not version),
  `Accept` aside. A refusal may arrive as HTTP 200 text/html (the body decides). The client retries
  refusals (only a 404 is about the bill — the address is built by convention) and errors carry the
  WAF ids (`_waf_marks`: Imperva incident id, F5 support id, `x-iinfo`). Failures are per-bill
  (`OrkaUnreachableError`), and **no phase turns a refusal or an initial 404 into a verdict**: the
  prefilter leaves `text_prefilter_pending`, the analysis leaves `analysis_pending` without spending
  an attempt (`AnalysisResult.unanswered`); a bill with a text must never end analysed on metadata.
  The VPS EU proxy does not help (refused the same). Probe: `.github/workflows/orka-probe.yml`.
- Sittings: `/committees/{code}/sittings` items have `num`, `date`, `startDateTime`, `room`,
  `status` (only PLANNED|FINISHED — a cancelled sitting vanishes, hence "gone" = retraction),
  `agenda` (HTML `<div class="agenda-indent-N">`, prints as "druk nr 3035"/"druki nr 3010 i 3055"),
  `video[].playerLink`, `jointWith`, `comments` (the change made), `notes`, `closed`. `/proceedings`
  lists sittings (`number` 0 = planned without agenda); `/proceedings/{n}` adds `agenda` (`<li>`,
  `PrzebiegProc.xsp?nr=` links unreliable: match the text, strip `-A`) and `schedule` (approximate
  hour per agenda point per day; unused). Of 1,150 plenary items naming a projekt, 981 give a number.
  The Sejm names the print last, so `items_mentioning` keeps a window around the reference beside the
  head when an item exceeds `ITEM_MAX_CHARS` (111 of 4,040 items).
- `modifiedSince`/`changeDate` are naive **Europe/Warsaw**; `Z` is rejected. `sort_by` and `passed`
  filters are ignored; paginate by `offset` until empty. `documentType` needs the Polish display
  string ("projekt ustawy"); the enum `BILL` does not filter.
- `passed=true` = adopted by parliament, not in force (vetoed too). Publication: `ELI`/
  `displayAddress`; `/eli/acts/DU/{year}/{pos}` gives `promulgation` (Dz.U. date), `entryIntoForce`,
  `announcementDate`; ~30–40 days after the vote. `closureDate` is the **Sejm's** closure (third
  reading, rejection, withdrawal; term 10 on 2026-09-12: 938 bills, 498 closed with an act, 84 closed
  and passed without one, 47 `passed=false`); an open process never has it; `End` says nothing about
  how far the bill got.
- **`/prints/{n}` may answer with another record.** For druki 599, 1768, 2821 of term 10 the detail
  is a filing ("opinia NBP", "Stanowisko Rządu") and the bill's PDF is gone. `PrintInfo.main_pdf`
  falls back to the first PDF when `{number}.pdf` is absent (six prints publish under their own names,
  e.g. "2872 (z autopoprawką).pdf"), but refuses a file *named after a filing* — `{n}-<digits>.pdf` or
  `{n}-s.pdf` (`models.sejm._names_a_filing`); such a print reports no text and stays
  `text_prefilter_pending`.
- `additionalPrints` (`1273-001`, `1273-s`): filings after submission, each with `title`,
  `documentDate`, `deliveryDate`, own PDF on api.sejm.gov.pl (no WAF). Term 10: 2,339 over 3,282
  prints (289 OSR, 82 Stanowisko Rządu, 1,732 opinions — 563 saying "nie zgłoszono uwag" — 42
  second-reading amendments, rest housekeeping); the listing carries them, so the catalogue is one
  request. An autopoprawka is never one; only the government's position is also a stage
  (`GovermentPosition`).
- **An autopoprawka is a print of the bill's own number** (`72-A`, `128-B`, `128-BA`; 29 in term 10;
  `{druk}-A` is never anything else) that nothing points at — not `additionalPrints`, not the base
  detail (`processPrint` points the other way), not the base PDF — so
  `newer`, `_remember_supplements` and the tree miss it. Only `/bills` shows it
  (`submissionType: BILL_AMENDMENT`, 49 rows, `print` = base druk). Unhandled: discovery rightly skips
  these rows (`not sub.is_bill`), but `find_submission` may pick one instead of the bill.
- `rclNum`/`rclLink` are detail-only; a government `/bills` entry carries the RM number as
  `sendersNumber` (all 561 of term 10, unique). Print from RCL id: `find_process_by_rcl_num` narrows
  by hand-over date and caps lookups. The reverse (`getIdFromLegislacja`) throttles after ~90 calls
  (403, 292-byte body): sweeps pause 1.5 s and record the status beside `Location` (a 200 means not on
  RCL).
- `SenatePosition` reports via `position`, not `decision`. `UE` enum: NO|ADAPTATION|ENFORCEMENT.
- Stage types: `PublicHearing` is **always a child of `CommitteeWork`** (so `events.open_hearing`
  and `hearings_due` walk `flatten_stages`; in `_phase_after` it is unreachable). 995 top-level nodes
  lack `stageType` ("Rozpatrywanie na forum Sejmu"), never on a projekt ustawy. Whether a
  `PublicHearing` node precedes its hearing is unmeasured (hearings are mostly announced via
  sittings: 47 agendas vs 9 nodes).
- `Voting` embeds totals; per-club needs `/votings/{sitting}/{n}`. Signatories are not in the API:
  parse the cover letter and match `/MP`. The representative is named in the accusative after
  «upoważniamy pana posła …» (sometimes several — the card names the first — a committee's
  nominative, or after a colon); `MpDirectory` retries a name with spaces removed (pypdf splits
  surnames; no collisions over 499 members) and page-wrapped signatures (`\f`) are rejoined. Term 10:
  3 of 11,259 signatures unresolved, a representative on all 334 letters naming one; scans (11) and
  letters outside the text layer (22) are out of reach (corpus `checks/10_authors.py`).
- **Every call to action names an address.** Committee: "Listy do Sejmu" form
  `contact.xsp?type={code}` (`committee_letter_url`; 35 of 40 committees — not investigative,
  dissolved, NON). Public hearing: the Marshal's form to `wysluchanie.publiczne@sejm.gov.pl` with an
  electronic signature (`page.xsp/wysluchanie_publiczne`). RCL names the applicant by minister title,
  the wykaz by abbreviation; `ministry_url` reads both.
- **The Senate has no API; every act it receives has a page** ("Ustawy uchwalone przez Sejm", ten a
  page, newest first, "Ustawa" + the Sejm's `titleFinal` with retyped dashes/quotes/commas, dropped
  words, a cut last word). `senate_title_distance` compares words, allows two such edits, never a
  changed number or a longer tail: 574 of 575 term-10 acts found (B82). Titles repeat, so `find_act`
  prefers the Sejm's exact title, then the earliest act received on/after the third reading. The page
  names committees (`p[data-komisja]`, `komisja,{id}.html`) and their sittings; a committee page hides
  its e-mail in `data-cfemail` or `SendTo(...)`. The window is days; `SenateWatcher` reads it every run
  in phase `senate` (`bills.senate_json`, v31); the card says "committees, e-mail, before the sitting
  on …", then only the vote is left.
- Never `HEAD` an attachment (no `Content-Length`, 5–15 s on an unrendered file): stream the `GET`
  with `download(url, max_bytes=…)`. pypdf needs `pypdf[fonts]` (CFF fonts); its logger is capped at
  ERROR; ~1 s per 100 pages. A scan goes to the model as a PDF block: ≤ 32 MB request (≤ 24 MB file;
  druk 2865 at 40 MB does not fit), 600 pages, ~1,600 tokens a page (`count_tokens` on druk 1273).
  Polish is ~2 characters a token; the 1M context takes any print whole.
