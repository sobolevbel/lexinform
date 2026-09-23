# Roadmap

Rewritten on 2026-09-15. Purpose of the project, restated by the owner: catch bills that may
affect foreigners **early**, follow their whole legislative life, and give readers a chance to
**act in time** — public consultations, hearings, opinions to committees, deadlines.

This file is what is *not* done. What is done is in `CLAUDE.md` (the invariants and the decisions
already taken) and in the commit history; the log of the first week and the original plan of the
three shipped features, which used to make up two thirds of this file, were removed on 2026-09-15
and are in git.

## Now

- **Reliability of process decisions — incident 2111, 16 Sept 2026.** The immediate fixes
  separate the observed closure from discovery metadata, select the current text on first
  analysis, and require explicit evidence of a veto outcome. Shared interpretation of source
  facts and immutable observation snapshots are implemented for the Sejm process slice. RCL,
  RPW, wykaz, linking, agendas and term rollover now commit their observation together with a
  delivery plan. Cards, status updates, agendas and reminders retry from saved facts and
  distinguish queued delivery from editorial holds. See
  [the architecture and compatibility boundaries](process-plans.md), now recorded as a CLAUDE.md
  invariant ("First sight seeds a baseline…"). The false reply incident 2111 itself sent was
  deleted by hand before this fix shipped. **Verified done, 21 Sept 2026**: reading and tribunal
  decisions are unified into the same evidence (`reading_evidence`/`tribunal_evidence`, wired into
  both `models/events.py` and `models/phases.py`); the RCL consultation-letter-read-late gap (was
  listed under "Later" here) is fixed too — `abecc6a` (16 Sept) and `e749a13` (21 Sept), now
  documented in CLAUDE.md's `read_consultations` invariant. Scan and triage memoization are in
  `analysis_memo` since the batch work of 23 Sept (`test_scan_and_triage_memos_survive_a_failed_write`);
  remaining work is to extend the independent expected-results corpus.

- **The weekly digest — built on 2026-09-15**, as designed here and with one change: the monthly
  figures count entries *taken in* and not a phase's `seen`, because the register is downloaded
  whole every run and a sum of what the runs looked at would count one entry sixty times. What is
  unobserved is the first real Sunday: whether a week's cards, updates and open consultations make
  a post worth reading, and whether the operator wants "sittings ahead" over fourteen days or
  seven (`SITTINGS_AHEAD_DAYS`).
- **Reader guides** (`docs/guides/`): written on 2026-09-15 — how to file a zgłoszenie
  zainteresowania, how to answer a public consultation, how to write to a committee, how a public
  hearing works, and what the bot and the channel are. They wait for a home: a URL, a design and
  short links from the messages («как подать?») in place of the paragraphs the cards carry today.
- **The seven projects the register had been pointing at — done, and it cost $3.39.** The first
  run after the deploy (run 61, 2026-09-15 01:37 UTC) took in all 12 projects the listing never
  showed as changed; 7 passed the text prefilter, exactly as predicted. Six were analysed and
  three carded — zawód pielęgniarki (4/5), praca na morzu and rejestry publiczne (3/5) — and the
  seventh, RCL/12409801 (Prawo o ruchu drogowym), was the combined-file project fixed the same
  day and carded by run 65. The queue is clean afterwards. What the observation
  also found is in the item on the consultation letter below: all four cards went out without a
  window and were repaired by hand.

## Next — decided, designed, not built

### The guides get a home

A domain, a static site, the guides and the page about the bot on it, and short links from the
cards («как подать?»). Until then the texts live in `docs/guides/`.

### RCL has no sweep of its own

The register now names the projects it knows of, which is how the twelve of 15 Sept 2026 came
in, and an entry rewritten into relevance is caught too: the whole register is judged every run.
What is left uncovered is a project the register never names — no wykaz number, or a number the
index does not carry — and untouched since this bot's first run: the listing is walked by
modification date and nothing else looks. One `run --since` far enough back would find them;
whether that is worth its Sejm re-scan is the open question.

## Later — worth doing when there is a reason or a measurement

- **A scanned deputies' print shows no clubs.** `SejmAuthorsResolver.resolve` is handed `text`
  while the analysis already reads the pages; 11 prints of term 10 have an empty text layer.
- **22 prints carry the covering letter in the PDF and not in its text layer** — worth checking
  whether they are mixed PDFs, a scanned first page over a digital body.
- **An autopoprawka rewrites the bill and nothing points at it.** `{druk}-A`, 29 in term 10,
  visible only in `/bills` as `submissionType: BILL_AMENDMENT`. Detection is one query; what to do
  with it is the question, since re-analysing on its PDF would repeat the `carries_bill_text`
  mistake — the supplement path is probably right.
- **The plenary `schedule`** would say which day of a four-day sitting a bill is taken on.
- **`Analysis.confidence`** is asked for, stored, and read by nothing but `lexinform show`.
- **Two counters mislead the operator**: `lexinform runs` prints only `discovered` in its `disc`
  column, so run 61 — the largest RCL haul so far, 12 projects — reads as a zero; and `/refresh`
  answers "card refreshed" while `RunReport.cards_refreshed` stays 0.
- **A card published before `PROMPT_VERSION 2026-09-v7`** keeps the analysis it was published with
  (decided 2026-09-08). Only `/republish` changes that, one bill at a time.
- **Druki 1929/1933 were carded separately** before the joint rule; merging them retroactively is
  possible and has not been done.
- Ukrainian-language channel; a static site built from the state dump.
- Committee e-mail addresses in "what you can do now" (the Sejm API has none; the committee page
  is linked instead) and the Senate committee that received the act (the Senate API is not used;
  the card links the Senate's listing of the laws the Sejm has passed).

## Measurements that would settle a question

- Does a `PublicHearing` node ever appear **before** the hearing? 47 committee agendas of term 10
  mention "wysłuchanie" against 9 such nodes, so a hearing is announced through the sittings
  listing; one observation in production would say what `HearingReminder` is worth.
- The **daily drift of the API**: two `/processes` snapshots a day apart differ in no field, but
  they straddle a weekend. Two working days would say whether `changeDate` moves on its own.
- What **trimming the uzasadnienie** would lose: it is the largest single line of the bill
  ($53.53 of $118.83 for term 10) and it is also where the card gets "what this is for".
- The **triage of a scan that is relevant**: 18 of 18 scans were correctly rejected, but the
  sample met no relevant scan, so the rate of false rejections is unmeasured.

## Not code — with the owner

- ~~Chat and comments under the channel, with Rose moderating who joins.~~ **Done 2026-09-15**:
  the chat is created, Rose moderates who joins, and it is linked to the channel as its
  discussion group, so every card and every reply now takes comments. Nothing in the code knows
  the chat exists, and it does not need to: a comment is a message in the chat, while every post
  the bot makes is a post in the channel, so no run reads or answers one. Whether the bot should
  ever see them is a product question, not a missing feature.
- Avatars for the channel, the group and the bot.
- A domain (see "The guides get a home").
- Telling people about the channel, once the bot is finished.

## Decisions taken with defaults (change if needed)

| Question | Default |
|---|---|
| Text threshold | distinct ≥ 2 or occurrences ≥ 3 |
| New statuses vs reuse | new `text_prefilter_pending` / `skipped_text_prefilter` |
| Cache PDF text between prefilter and analysis | run-scoped in `TextLoader`, not in the DB |
| `reprefilter` results published by next `run` | yes; document `run --no-publish` for backfills |
| Publication notice merged with a stage update | no, separate message |
| Club breakdown | `voting_club_breakdown`, on since 2026-09-07 |
| "Today" for reminders | Europe/Warsaw |
| Tracking cap for passed bills without an act | 1095 days, the same as a veto or the Tribunal |
| In-force reminder repeats the summary | yes |
| Re-fetch ELI metadata | only while `entry_into_force` is null |
| Probability that a bill passes | never estimated (the owner's decision) |
