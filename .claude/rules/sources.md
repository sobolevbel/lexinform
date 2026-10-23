---
paths:
  - "src/lexinform/rcl_letters.py"
  - "src/lexinform/models/rcl.py"
  - "src/lexinform/models/wykaz.py"
  - "src/lexinform/models/ministries.py"
  - "src/lexinform/models/source_checks.py"
  - "src/lexinform/adapters/rcl_html.py"
  - "src/lexinform/adapters/wykaz_csv.py"
  - "src/lexinform/adapters/consultation_documents.py"
  - "src/lexinform/services/discovery.py"
  - "src/lexinform/services/rcl_discovery.py"
  - "src/lexinform/services/rcl_projects.py"
  - "src/lexinform/services/wykaz_discovery.py"
  - "src/lexinform/services/lookup.py"
  - "src/lexinform/services/predecessors.py"
  - "src/lexinform/services/publishing.py"
  - "src/lexinform/services/sources.py"
  - "src/lexinform/services/tracking/pre_print.py"
  - "src/lexinform/services/tracking/rcl.py"
  - "src/lexinform/services/tracking/wykaz.py"
  - "src/lexinform/services/tracking/linking.py"
  - "src/lexinform/services/tracking/consultations.py"
  - "src/lexinform/services/tracking/service.py"
  - "tests/scenario/test_rcl*.py"
  - "tests/scenario/test_wykaz*.py"
  - "tests/scenario/test_tracking_pre_print.py"
  - "tests/scenario/test_tracking_rcl.py"
  - "tests/scenario/test_tracking_wykaz.py"
  - "tests/scenario/test_predecessors.py"
  - "tests/scenario/test_consultations.py"
  - "tests/scenario/test_unthreaded_observation.py"
---

# Sources and linking (RPW, RCL, wykaz)

How RCL and the register are read (markup, catalogs, file formats, consultation letters, CSV
columns, probes): `docs/rcl-scraping.md`.

- **`/bills` rows are refreshed by tracking only.** Discovery saves new submissions and never
  overwrites an existing row; the reconciler (`tracking/pre_print.py`) re-reads `/bills` for pending
  RPW entries and those awaiting consultation results and compares (print assigned, withdrawn,
  `consultationResults` flipped). An entry the listing *stopped* showing
  (`events._listing_says_withdrawn`, `_long_gone` = a year; entries can wait months in the
  Marszałek's "freezer") is headed «Проект больше не отслеживается» saying the Sejm published no
  decision — never «отозван».
- **RPW (`RPW/…`), RCL (`RCL/{id}`) and wykaz (`WPL/UD408`) rows have no Sejm process**
  (`has_process` tests `NON_SEJM_PREFIXES`; a missing prefix sends rows to `/processes`), but are not
  textless: `TextSources` routes RPW to `SubmissionTextSource`, RCL to its documents, only wykaz to
  metadata. The print inherits the card (`tracking/linking.py::Linker`: `new_bill` row aliased with
  the same `message_id`). The print copies the entry's status except a prefilter skip
  (`skipped_prefilter`/`skipped_text_prefilter`) → `text_prefilter_pending` (the entry's file may
  have been a scan or a WAF refusal; the print's PDF is the text the Sejm works from); an operator's
  `/skip` (`SILENCED_BY_OPERATOR`) is copied with its reason. RPW → print
  via `/bills`; RCL → Sejm discovery notices a druk whose `rclNum` names a followed project (stored
  RM number, else `getIdFromLegislacja?number=…`), stores it on the RCL row, the RCL watcher links.
  A druk whose `/bills` entry already has a row is stamped on the entry's project too (by
  `rclNum` or `sendersNumber`), and `PublishingService.became_druk` gives no card to a druk whose
  RCL project holds a sent card: it stamps the number and the RCL watcher links it that run (B116).
  `rclNum` is detail-only, so while a threaded RCL project (`Bill.has_thread`) awaits its druk,
  discovery reads details of new prints not known non-government (`_with_rcl_num`); a quiet
  project's druk is judged on its own text. **A government `/bills` entry of a followed project is
  that project's** (UDER106): `predecessors.absorb_submission` stores it on the RCL row by
  `sendersNumber`, no RPW row; the card and hand-over reply name and link its file.
  `PublishingService.became_druk` refuses a card to an entry read while RCL could not name the
  number, and to the project past that gate. The linker keeps a druk's own card, analysis and — when
  read on its own text or in a batch (`_read_on_its_own`, B60) — status; a listing row never erases a
  detail's `rcl_num`. The RCL watcher asks the DB (`find_print_by_rcl_num`) before the API (B63).
- **On-request lookup links in whichever direction is named; the druk comes back.**
  `BillLookup._fetch`: RCL → `find_process_by_rcl_num` for its `rm_number`; RPW → `print` in its
  `/bills` row; druk → `/bills?print=N` and its own `rclNum` (`discovery.NOT_FOLLOWED`: a skipped or
  linked row has no thread; RCL unreachable leaves the print alone). A print linked outside tracking
  inherits the entry's card (`_inherited_card`, re-rendered with both tags). An entry whose act is in
  force never gets a card promising a druk. A command posts no card for a bill `models.is_over` calls
  finished, answering with the verdict.
- **RCL rows are refreshed by the RCL watcher only.** RCL has no API; pages take ~10 s (no request
  per project without reason); GitHub runners may be network-blocked, so the initial list probe stays
  short and single-attempt. Discovery reads a project once (timeline + catalogs for a candidate, one
  catalog for a title miss), then only bumps `change_date`. `RclWatcher` re-reads the timeline and
  catalogs whose "Data ostatniej modyfikacji" moved **or that were never read**
  (`RclStage.catalog_read`, set only in `parse_stage_catalog`: 1,098 of 4,307 reached stages have no
  folders after a full read); `rcl_fingerprint` (stages reached, folders' first files, newest text,
  status, hand-over) decides. Folder uploads alone are not news; published opinions are a
  `consultation_results` reply. A skipped project keeps its skeleton (`RclProject.without_documents()`
  via `set_status`); `reset … --to analysis_pending` re-reads. Runs older than
  `LEXINFORM_RUNS_RETENTION_DAYS` (90) are deleted at run start.
- **RCL markup is parsed, not matched.** `adapters/rcl_html.py` uses CSS selectors; a missing detail
  is tolerated, a missing structural element (timeline, announced table rows, every stage label)
  raises `RclPageError` (shown in the report). The WAF's HTTP-200 "Request Rejected" page is
  `RclUnavailableError` (an outage).
- **The consultation letter has its own phase.** A title miss gets only `with_text()`, a hit
  `complete()`, and the watcher only refreshes listed changes, so `RclDiscoveryService.
  read_consultations` (phase "rcl consultations", between text prefilter and analysis) reads the
  letter's catalog (`RclProjectReader.with_consultation`) for analysed or pending RCL rows missing a
  window (`list_rcl_missing_consultation`, `ORDER BY change_date ASC` so old rows are not starved;
  `CONSULTATION_READ_ATTEMPTS` = 2 via `consultation_attempts`). **Reading the letter late is not an
  event**: `consultation_opened` is news only while `models.consultation_open`; `_results_due` is
  silent when the stored window was None. `_rcl_actions` reads `window is None` as "never consulted"
  (`action_rcl_window_closed` is unreachable then), so the window must be read before carding.
- **The wykaz is an intention, not a bill.** One CSV per run (`adapters/wykaz_csv.py`; URL id read
  from the page, columns matched by prefix); **every entry is judged every run** (keyword match + two
  indexed lookups); the watermark only decides what the report calls news. A plan whose project is out
  hands it to RCL discovery (`WykazDiscoveryResult.on_rcl` →
  `RclDiscoveryService.discover(from_register=…)` → `_take`) — the only way in for a project the
  listing does not show as changed (the watermark starts 2026-09-09). Rejected entries are not stored
  (~2.3 MB vs an 822 KB dump). Only `Projekty ustaw`; no card for a plan realised/withdrawn on first
  sight or whose project is already on RCL. The card says there is no text yet and offers the art. 7
  zgłoszenie zainteresowania (ticket to a wysłuchanie publiczne, art. 8 ust. 2) and says **how**: the
  ministry's BIP form under "Działalność lobbingowa", name, address, interest, solution sought (art. 7
  ust. 4), to the ministry presenting the bill (ust. 2), published in BIP minus a private address
  (ust. 3). **Dz.U. 2026 poz. 160 repealed art. 3–4 on 2026-08-28** (the wykaz now rests on art. 8a–8b
  ustawy o Radzie Ministrów; form regulation Dz.U. 2011/1080 lapsed, no replacement), so the card
  links the ministry's `/kontakt` (`ministry_contact_url`), not a Dz.U. number. The register is the
  only source of a **dropped** project (`Status realizacji`, `Informacja o rezygnacji`): posted; a
  slipped quarter or rewritten "istota" is only stored. `Planowane przyjęcie przez RM` is free text:
  only the quarter is rendered.
- **A plan's project is stamped, not ingested.** RCL discovery seeing a followed `WPL/` row's number
  writes `rcl_project_id` and skips; `tracking/wykaz.py::WykazLinker` creates the `RCL/` row, hands
  over the card and **re-analyses from the documents** (the plan was judged on an announcement).
  Every listing row's number is stored (v22 `rcl_wykaz_numbers`, `RclDiscoveryService._remember_number`;
  `lexinform index-rcl-numbers --since` for older years, workflow input `index_rcl_since`). A plan
  whose project is already out gets no card; a followed plan claims a project the listing no longer
  shows (`WykazWatcher._stamp_projects_already_listed`). **The register reuses numbers** (UD368: CPK
  in 2018, Karta Polaka in 2026; 61 of 659 since 2025), so a plan takes only a project created on or
  after its announcement, and a row with no creation date matches nothing.
- **A bill with no card is observed, never told.** `observation_for`: sent card →
  `full`/`telegram_thread`; relevant and score ≥ `min_score − 1` → `full`/`relevant_analysis`;
  relevant below → `metadata`/`below_bar` (2026-10-01: no new text lifts it to a card); not relevant
  → `metadata`/`not_relevant`; an `operator` basis (`/observe`) is never overwritten.
  `list_tracked(unthreaded=True)` lists them (joint prints excluded); their process is read after the
  carded loop, stages/closure/act/change rows stored, no publication row, so nothing is delivered or
  replayed later. `metadata` asks the model nothing. Each read records a `process` check
  (`record_check`; an outage is no failure; `source_checks`, v35). RCL, wykaz and agenda watchers take
  them as `quiet`; the Senate page is read for `full`. A `full` bill's new text is re-analysed and
  reaching `min_score` gives a card next publishing phase (2026-09-28); stored changes are not
  replayed.
