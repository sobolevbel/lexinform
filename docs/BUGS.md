# Bug registry

What has been wrong with this bot, what is wrong with it now, and — the part that earns the file —
**which kinds of mistake it keeps making**. Read the last section before hunting: almost every
defect found so far was the second or third instance of a shape already in the history.

A row is `open` until a test fails on the old code and passes on the new one. `candidate` means
the code reads wrong but nothing has yet been replayed over the corpus to say how often it bites:
a candidate is a lead, not a finding, and the first suspicion goes to the probe, not to the code.

**Rank** is what it costs the reader, never how hard it is to fix:

| rank | meaning |
|---|---|
| P0 | the channel states something false, or a bill is lost with no way back |
| P1 | a piece of news is lost or told twice; recoverable by hand |
| P2 | the reader is told the truth in a way that misleads or says nothing |
| P3 | the operator, the report or the log is wrong; no reader sees it |

## Open

| # | rank | state | module | what is wrong | found |
|---|---|---|---|---|---|
| 1 | P1 | candidate | `services/tracking/linking.py:138` | the card alias is created three ways and only this one leaves `sent_at` NULL (`publishing.py:325` and `wykaz.py:128` copy the original's), so the digest's week query `sqlite_repo.py:1132` sees the three paths differently | 2026-09-16 |
| 2 | P1 | candidate | `services/digest.py:174` | the week is assembled per publication with no dedupe by thread, and an inherited card is a second `new_bill` row for one message — a project that gets its druk in the same week is listed twice under two names | 2026-09-16 |
| 3 | P1 | candidate | `services/tracking/posting.py:157` | `record_change()` answers `None` on a `change_key` collision and all seven callers read that as "nothing to do", while the fingerprint and `save_rcl`/`save_wykaz` have already been written (`rcl.py:168` before `rcl.py:220`, `wykaz.py:274` before `wykaz.py:293`) — the change is never detected again | 2026-09-16 |
| 4 | P1 | candidate | `services/tracking/posting.py:71` | `posted()` counts `PENDING` and `SKIPPED` as "already posted", so a crashed run or a `--no-publish` run permanently silences `act_published`, `in_force`, `consultation_*`, `decision_deadline`, `hearing_deadline` and `agenda` | 2026-09-16 |
| 5 | P1 | candidate | `services/tracking/acts.py:77` | `create_publication` upserts (`sqlite_repo.py:1030`) and this call writes `IN_FORCE`/`SKIPPED` without the `posted()` guard its neighbours have — an existing `sent` row can be downgraded | 2026-09-16 |
| 6 | P1 | candidate | `models/sejm.py:736` | `_stage_key` excludes `position`, `proposal` and `report_file`, so a `SenatePosition` whose position text arrives later moves no fingerprint and is never told | 2026-09-16 |
| 7 | P0 | candidate | `models/phases.py:369` | `_phase_after` falls through to `return None` for `Referral`, `Voting`, `CommitteeReport` and a non-trailing `End`; `next_phase is None` means "road over", so the bill gets no card and a followed one freezes | 2026-09-16 |
| 8 | P0 | candidate | `services/tracking/wykaz.py:255` | `_removed` reads absence from the register as a withdrawal, guarded only against a wholly empty CSV — a partially parsed register marks live plans `Wycofany` for good, and the fabricated entry is then persisted | 2026-09-16 |
| 9 | P1 | candidate | `services/tracking/rollover.py:58` | every unfinished row is discontinued whether or not its announcement succeeded; a bill whose `_announce` threw is discontinued, unannounced and out of `CardRefresher`'s reach (`list_tracked` filters on `discontinued_at`) | 2026-09-16 |
| 10 | P1 | candidate | `services/tracking/linking.py:86` | the print is adopted and then `return`s with no alias and no announcement when the predecessor's card is missing or not `sent` — the row ends `analyzed` with no card and is not a publish candidate either | 2026-09-16 |
| 11 | P2 | candidate | `models/events.py:189` | `Voting` is substantive but has no entry in `_EVENT_BY_STAGE_TYPE` and no `_stage_event` arm, so a change whose only new node is a vote is posted under the bare «Обновление» | 2026-09-16 |
| 12 | P2 | candidate | `services/tracking/service.py:422` | `has_news` and `fills_in_the_past` are applied on the Sejm path alone; RCL, wykaz, pre-print, rollover and linking post every change they record | 2026-09-16 |
| 13 | P2 | candidate | `models/phases.py:556` | `rcl_sejm` is a derivable phase key with no entry in `next_step_labels`, `no_action_labels`, `typical_durations` or `PHASE_STEP` — the step line silently disappears if `sent_to_sejm` ever stops covering it | 2026-09-16 |
| 14 | P2 | candidate | `models/phases.py:306` | a numbered druk whose stage tree is empty goes to `_pre_print_phase`, which promises «присвоение номера druku» to a bill that has one | 2026-09-16 |
| 15 | P2 | candidate | `models/sejm.py:817` | `_DERIVED_PRINT_STAGES` omits `PresidentMotionConsideration`, so an agenda item naming only the President's motion print is not matched to its bill | 2026-09-16 |
| 16 | P2 | candidate | `services/tracking/posting.py:336` | a reply falls back to a top-level post when the bill has no card, so a thread reply can appear in the channel as a rootless message | 2026-09-16 |
| 17 | P3 | candidate | `services/publishing.py:418` | `_safe_print` swallows `ServiceUnavailableError` while every other `except Exception` in the chain re-raises it first — an outage is recorded as a per-bill failure | 2026-09-16 |
| 18 | P3 | candidate | `models/events.py:334` | `impact_assessment` gets a header and an icon but no searchable event tag, while `government_position` gets one | 2026-09-16 |
| 19 | P3 | candidate | `adapters/telegram_format.py:183` | `tribunal_ruled` has an `update_headers` entry and no `EVENT_ICON`, so the Tribunal's ruling is posted under the generic 🔄 while `tribunal` gets ⚖️ | 2026-09-16 |

## Fixed

*(nothing yet in this round)*

## Classes we have already had

Each line is a shape, not an incident. When a probe finds one instance, look for the others: every
entry below was found at least twice.

- **A fallback picked the wrong file.** `main_pdf` fell back to the first PDF when `{number}.pdf`
  was absent, so druk 2821 was judged on a one-page "no remarks" scan from the NBP (`9439b06`).
  `consultation()` looked only in "Pisma kierujące", so a project filing its letter elsewhere was
  carded with no deadline and no address (`a87386d`).
- **A constant table missed a member.** `rcl_opinions` was the one of four RCL groups with no
  `PHASE_PATIENCE` entry — and it is the fallback, so the commonest government step used the
  "nobody measured this" default (`1d6f1ac`); `rcl_consultation` was missing too (`4e1f17d`).
- **A key collided, or an index was never taken.** The digest row carried the current kadencja
  while its unique index carries no term, so a term moving between two drafts of one week hit a
  bare `assert`; and `ix_pub_sent_at` was never used because SQLite preferred `ix_pub_status`
  (`5e94db9`).
- **A guard asked a question the data could not answer.** `_has_drafts_channel` guarded on a value
  that falls back to a placeholder and is never empty, so with no technical channel the first
  Sunday would have sent every reader the unapproved draft and its publish button (`5e94db9`).
- **A watermark used as a relevance gate.** `Data publikacji` never moves on an edit, so an entry
  rewritten into relevance stayed behind the watermark for ever and 12 live projects were
  invisible to both sources (`ef77725`, `3caac74`).
- **The last item taken for the representative one.** `with_text` read only the newest reached
  stage's catalog, and the stage a project ends on carries the covering letter and no bill — nine
  projects were closed with "no document to read" (`dfd08b3`).
- **A "not yet" recorded as a verdict.** Druk 3094 was judged five minutes before its PDF
  appeared, and a text skip is reopened only by a title change (`c7e4f6d`).
- **A whole record dropped by a naming assumption.** A folder naming bill and uzasadnienie in one
  file, or filing the bill as "Załącznik nr 1", yielded no bill text at all (`c7e4f6d`).
- **An emptiness standing in for "unread".** `refresh` kept any stage whose modification date had
  not moved, so a project read through one catalog left every other catalog closed for the row's
  life; 1,098 of 4,307 reached stages legitimately have no folders (`0bb1c0b`).
- **The same thing announced twice.** A plenary sitting named twice in one line (`5e94db9`); druk
  1929's second post of the day announced the cause of the first, seven of its eleven lines a
  repeat (`db8bd85`).
- **A conditional stated as a fact.** A committee's `notes` was read by nothing, and 21 sittings
  of term 10 happen only if the Sejm refers something first (`b306266`).
- **A success value indistinguishable from a failure.** `DigestResult.note` is empty on success,
  so `note or "posted"` reported a send that never happened (`5e94db9`).
- **A promise broken by a second path.** `/skip` promises no re-analysis; `reprefilter` re-scanned
  the silenced rows anyway and put two bills back in the paid queue (`5cc639d`).
- **A flag nothing in production could ask for.** `reprefilter --include-text-skipped` was never
  passed by `daily.yml`, so the skip that most needs revisiting never was (`ebd44a4`).
- **An exception raised above the guard meant to protect it.** The guard was around
  `notify_backfill`; the throw was one line earlier, and 36 minutes of scanning were lost
  (`1420f50`).
- **A parallel phase reimplemented sequentially.** The backfill had its own loop where the phase it
  mirrors fans out — 110 bills in 1791 s, five downloads overlapping (`7fd6686`).
- **A cap that silently dropped data.** Week 37 carried 14 cards; the digest showed ten and said
  nothing about the four (`5e94db9`).
- **An off-by-one week.** `current_ref` assumed the digest day ends the ISO week, so a Monday
  digest would have drafted the week that had just begun (`5e94db9`).
- **A cost measured on the wrong model or sample.** $0.013 was Haiku on eight pages; production
  triages on Sonnet and a 197-page print costs $0.044 (`b4c94e2`).
- **A page break that is not a line break.** `^`/`$` are blind to `\f`: 279 prints reached the
  model with no uzasadnienie (audit 1, defect 1), and the same blindness cut 18 signatures to a
  bare first name (audit 2, defect 2).
- **A doc invariant stated backwards.** CLAUDE.md said the print's detail is authoritative about
  attachments; the observation was right and the conclusion inverted (`9439b06`).

## Where the probes live

The replays that find these are in the corpus, not here:
`~/projects/lexinform-corpus/checks/`, run from this checkout as
`uv run python ../lexinform-corpus/checks/NN_name.py`. `checks/README.md` holds the method,
`FINDINGS.md` and `COLD.md` the two audits of 14 Sept 2026. The registry is the repository's copy
of what they concluded, because the corpus is not a git repository and does not travel with the
code.
