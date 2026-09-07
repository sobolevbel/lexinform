# Roadmap

Planned on 2026-09-07 after the first production runs. Purpose of the project, restated by the
owner: catch bills that may affect foreigners **early**, follow their whole legislative life, and
give readers a chance to **act in time** (public consultations, hearings, opinions to committees).

## Status (2026-09-07, evening)

Done the same day, see the commit history:

- **Bills before they get a print number** (`RPW/…` entries from `/bills`): discovered, analysed
  from the official description, published with the public consultation dates; linked to the print
  when it is assigned (same Telegram thread), withdrawal announced. Not in the original plan; it
  surfaced when a consultation-stage bill (RPW/29075/2026) was missing from the channel.
- **Feature 3**: voting totals + per-club breakdown, Senate/President outcomes, committee names.
- **Feature 1**: keyword search inside the print PDF (`reprefilter` CLI, shared text cache).
- **Feature 2**: Dz.U. publication notice + entry-into-force reminder (schema v4).
- Action signals, cheap tier: consultation dates on cards, committee referral with name and hint,
  public hearing label.

Still open:

- Consultation deadline reminder (e.g. 3 days before `publicConsultationEndDate`).
- Medium tier of action signals: committee sitting agendas (`/committees/{code}/sittings`) and the
  next Sejm sitting agenda (`/proceedings`) mentioning the bill.
- Ukrainian-language channel; weekly digest; static site from the state dump.

The sections below are the original plan, kept for the rationale and the verified API facts.

---

Verified API facts used below (curl, 2026-09-07):

- `GET /sejm/term10/processes/{n}` carries `address` ("WDU20260001099"), `displayAddress`
  ("Dz.U. 2026 poz. 1099"), `ELI` ("DU/2026/1099") and `links[]` (rel `isap`, `eli`, `eli-api`).
  For druk 2699 `closureDate` = 2026-07-17 (3rd reading) and promulgation = 2026-08-18: 32 days
  later. The tracking grace period was therefore raised from 30 to 90 days.
- Post-Sejm stage types: `SenatePosition` (`position`: "nie wniósł poprawek" / "wniósł poprawki"),
  `SenatePositionConsideration` (`decision`), `ToPresident`, `PresidentSignature`, `Veto`,
  `PresidentToTribunal`, `End`. Vetoed bills still have `passed=true` and `ELI=null`, so `passed`
  means "adopted by parliament", not "in force"; `ELI`/`address` is the publication signal.
- `GET /eli/acts/DU/2026/1099`: `ELI`, `displayAddress`, `title`, `status` ("obowiązujący"),
  `inForce` (IN_FORCE|NOT_IN_FORCE), `entryIntoForce` (2026-11-19), `promulgation` (2026-08-18,
  the Dz.U. date), `announcementDate` (2026-07-17: the date of the act, despite the name),
  `texts[]`, `prints[]`. Unknown act: 404. `/text.pdf` works. Exactly one `entryIntoForce`.
- `Voting` stage (child of the 3rd-reading `SejmReading`) embeds `voting`: `yes`, `no`, `abstain`,
  `notParticipating`, `totalVoted`, `present`, `majorityType`, `majorityVotes`, `sitting`,
  `votingNumber`, `date`, `topic`, `links[rel=pdf]`. `GET /votings/{sitting}/{number}` adds
  `votes[]` (`MP`, `club`, `vote` YES/NO/ABSTAIN/ABSENT); no per-club aggregation server-side.
- Volume: 93 new bills between 2026-06-01 and 2026-09-07 (max 6 per day), so a text prefilter
  means 1–6 PDF downloads per day.
- `tests/fixtures/sejm/process_1962.json` already contains a `Voting` stage with results and a
  `SenatePosition` stage.

---

## 1. PDF-text prefilter (second stage)

**Behaviour.** Bills whose title/description miss the keywords are no longer dropped at once: the
main print PDF is scanned with the same `KEYWORD_PATTERNS`, and strong hits send the bill to
analysis. Hits are stored with a prefix (`title:cudzoziemcy`, `text:cudzoziemcy`); the run report
gets `text prefilter: checked N · hits M`. No new Telegram message.

**Status flow.** `DISCOVERED` → title hit → `ANALYSIS_PENDING`; title miss → new
`TEXT_PREFILTER_PENDING` → text hit → `ANALYSIS_PENDING`; miss / no PDF / oversize / unreadable →
new `SKIPPED_TEXT_PREFILTER` (weak hits still stored so the threshold can be tuned later from the
state branch). Legacy `SKIPPED_PREFILTER` keeps meaning "title-only skip, text never checked".

**Threshold** (pure function in `keywords.py`): `match_counts(text) -> dict[str, int]`; accept when
`distinct >= 2` **or** `sum(counts) >= 3`. One "cudzoziemiec" in a 200-page tax bill is noise.

**Design.** No migration. New `services/text_prefilter.py::TextPrefilterService(gateway, repo,
extractor, prefilter, *, max_pdf_bytes, min_distinct, min_occurrences)` with
`run(term, *, limit)`; a `text_prefilter` phase between discovery and analysis;
`ServiceUnavailableError` aborts the phase leaving bills pending, any other per-bill error →
`SKIPPED_TEXT_PREFILTER` + warning. No text caching in the DB (the dump lives in git); the double
download (prefilter + analysis) is accepted for v1.

**Settings.** `text_prefilter_enabled=True`, `text_prefilter_min_distinct=2`,
`text_prefilter_min_occurrences=3`, `text_prefilter_max_per_run=20`.

**CLI.** `lexinform reprefilter [--since] [--limit] [--include-text-skipped] [--dry-run]` runs the
text stage over previously skipped bills; candidates are analysed by the next `run` (use
`run --no-publish` after a big backfill). `scan` also runs the text stage.

**Tests.** `match_counts` + threshold table; real `print_3039.pdf` → accepted; service on fakes
(hit, weak hit, no PDF, oversize, Sejm down leaves pending, extractor error → skipped); pipeline
test with a "VAT" title and foreigner-heavy text; `enabled=False` reproduces old behaviour.

**Steps (~0.5–1 day).** statuses + pure functions → service + tests → pipeline phase + report line
→ settings/container → CLI → docs.

---

## 2. Publication in Dziennik Ustaw and entry into force

**Behaviour.** Two new replies to the original card:

```
📢 Опубликован в Dziennik Ustaw — druk nr 2699

Ustawa z dnia 17 lipca 2026 r. o zmianie ustawy o nabywaniu nieruchomości przez cudzoziemców …

📰 Dz.U. 2026 poz. 1099 (опубликован 18.08.2026)
📅 Вступает в силу: 19.11.2026
ℹ️ Отдельные положения могут вступать в силу в другие сроки — см. текст закона.

🔗 Ход процесса в Сейме | ISAP | Текст закона (PDF)

#опубликован #druk2699 #Sejm10
```

```
⚖️ С сегодняшнего дня действует — druk nr 2699

Ustawa z dnia 17 lipca 2026 r. …

📰 Dz.U. 2026 poz. 1099 · вступил в силу 19.11.2026

📝 Суть закона
…current summary…

💡 Что это значит на практике: …

🔗 ISAP | Текст закона (PDF)

#вступилвсилу #druk2699 #Sejm10
```

If the act is discovered already in force, the first message says "уже действует с DD.MM.YYYY" and
the reminder is recorded as `skipped`.

**Data model.** `ProcessDetail` + `display_address`, `isap_url`. New frozen `ActInfo` (`eli`,
`display_address`, `title`, `act_date`, `promulgation_date`, `entry_into_force`, `in_force`,
`status`, `text_pdf_url`, `isap_url`, `fetched_at`); `Bill.act: ActInfo | None`.
`PublicationKind` + `ACT_PUBLISHED`, `IN_FORCE`. Migration v3:

```sql
ALTER TABLE bills ADD COLUMN act_json TEXT;
ALTER TABLE bills ADD COLUMN entry_into_force TEXT;
CREATE INDEX ix_bills_entry_into_force ON bills(entry_into_force);
CREATE UNIQUE INDEX ux_pub_once_per_kind ON publications(term, number, kind, channel_id)
    WHERE kind IN ('act_published', 'in_force');
```

**Ports/adapters.** New `EliGateway(Protocol)`: `get_act(eli) -> ActInfo | None` (None on 404,
`SejmApiUnavailableError` on transport/5xx), implemented on `SejmApiClient` (same host, same retry
loop). `Publisher` + `publish_act_published(bill, reply_to)`, `publish_in_force(bill, reply_to)`;
formatter + console publisher mirror them. Repo: `save_act`, `list_due_in_force(term, channel,
today)`, relaxed `list_tracked`.

**Service.** In `StatusTrackingService._detect`: when `detail.eli` is set and `bill.act` is None,
fetch the act, save it, post the publication notice through the pending-row protocol. New
`remind_in_force(term, *, publish)` after `check_updates`: bills with `entry_into_force <= today`
(today in Europe/Warsaw) and no `in_force` publication get the reminder. `list_tracked` keeps
passed bills without an act up to `track_passed_max_days` (180).

**Limitation.** ELI exposes one `entryIntoForce`; staged provisions are not modelled, hence the
fixed note in both messages.

**Steps (~1.5–2 days).** models + parsing + fixtures (`process_2699.json`,
`eli_act_DU_2026_1099.json`) → `EliGateway` + tests → migration + repo methods → labels +
formatter + publisher + fakes → tracking + pipeline wiring + report counters → settings/CLI
(`lexinform act NUMBER`) → README.

---

## 3. Voting results and Senate/President outcomes in updates

**Behaviour.** Stage lines in the existing update become richer:

```
🧭 Новые стадии
• 2026-07-17: III czytanie na posiedzeniu Sejmu — uchwalono
  🗳 Голосование: 239 за, 1 против, 199 воздержались · не голосовали: 21 · протокол (PDF)
• 2026-08-06: Stanowisko Senatu — Сенат внёс поправки (druk 2994)
• 2026-08-13: ✍️ Президент подписал закон
• 2026-07-17: ⛔ Президент наложил вето (druk 2863)
```

Per-club breakdown ("за: KO 152, PSL-TD 31 · против: PiS 178") behind a flag, off by default.

**Data model.** `Stage.voting: VotingSummary | None` (`yes`, `no`, `abstain`, `not_participating`,
`total_voted`, `majority_type`, `sitting`, `voting_number`, `date`, `pdf_url`, `topic`). `Stage.position`
already exists (2026-09-07). `_stage_key` is **not** changed, so stored fingerprints stay valid
and no spurious updates fire. No migration.

**Formatter.** `_stage_line` dispatches by `stage_type`: `Voting` → totals + PDF link;
`SenatePosition` → label from `position`; `PresidentSignature`, `Veto`, `ToPresident`,
`PresidentToTribunal` → `stage_type_labels` in `i18n`.

**Steps (~0.5 day; +0.5 for clubs).** models + parse + fingerprint test → labels + formatter →
(optional) `get_voting`, `aggregate_clubs`, enrichment in `_detect` behind `voting_club_breakdown`.

---

## Action signals (add-on, same rendering path)

Verified endpoints (curl, 2026-09-07):

- `GET /sejm/term10/bills?print=3039` → `publicConsultation: true`,
  `publicConsultationStartDate: 2026-08-05`, `publicConsultationEndDate: 2026-09-04`,
  `consultationResults`, plus `applicantType`, `status`, `euRelated`.
- `GET /sejm/term10/committees/{code}` → `name`, `nameGenitive`, `scope`;
  `/committees/{code}/sittings` → `date`, `startDateTime`, `room`, `status`, `agenda` (HTML that
  literally contains "druk nr 2846").
- `GET /sejm/term10/proceedings` → sittings with `dates[]`, `number` (0 for planned ones), `title`;
  future planned sittings are included.
- `PublicHearing` stage carries only `stageName`, `date`, `children`.

Cheap tier (~1 day on top of the stage rendering in feature 3):

1. **Public consultation on the card** from `/bills?print=N` (one request per analysed bill):
   `🗣 Общественные консультации: до 04.09.2026 — мнение можно направить через страницу druku`,
   plus a reminder 3 days before the deadline through the same idempotent status-change mechanism.
2. **Committee referral with the committee name** (`Referral` + `committee_code`, names cached per
   run): `📮 Направлен в комиссию: Komisja Administracji i Spraw Wewnętrznych (ASW) — мнения
   организаций можно направить в комиссию`, with a link to the committee page.
3. **Public hearing** (`PublicHearing` stage): `📢 Публичные слушания (wysłuchanie publiczne):
   12.10.2026 — можно подать заявку на участие`. Whether the stage appears ahead of the date must
   be observed live.

Medium tier (later): scan `/committees/{code}/sittings` for future items whose `agenda` mentions
`druk nr N` → `🗓 Комиссия ASW рассмотрит проект 15.09.2026, 10:00 (sala 412)`; scan the agenda of
the next `/proceedings` entry → `🗓 Проект в повестке заседания Сейма 7–9 октября`. One request per
committee/proceeding per run; needs HTML-to-text and a dedupe key per (bill, sitting).

## Order

Feature 3 first (small, no schema change, introduces `stage_type_labels` reused by 2), then 1
(independent, raises recall), then 2 (largest, the only migration), then the cheap tier of action
signals. Never edit earlier entries of `MIGRATIONS`; `restore()` migrates old dumps.

## Decisions taken with defaults (change if needed)

| Question | Default |
|---|---|
| Text threshold | distinct ≥ 2 or occurrences ≥ 3 |
| New statuses vs reuse | new `text_prefilter_pending` / `skipped_text_prefilter` |
| Cache PDF text between prefilter and analysis | not in v1 |
| `reprefilter` results published by next `run` | yes; document `run --no-publish` for backfills |
| `EliGateway` implementation | same `SejmApiClient` class, separate Protocol |
| Publication notice merged with a stage update | no, separate message |
| Club breakdown in v1 | no, flag off |
| "Today" for reminders | Europe/Warsaw |
| Tracking cap for passed bills without an act | 180 days |
| In-force reminder repeats summary | yes |
| Re-fetch ELI metadata | only while `entry_into_force` is null |
