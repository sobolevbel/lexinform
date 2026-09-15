# Roadmap

Rewritten on 2026-09-15. Purpose of the project, restated by the owner: catch bills that may
affect foreigners **early**, follow their whole legislative life, and give readers a chance to
**act in time** — public consultations, hearings, opinions to committees, deadlines.

This file is what is *not* done. What is done is in `CLAUDE.md` (the invariants and the decisions
already taken) and in the commit history; the log of the first week and the original plan of the
three shipped features, which used to make up two thirds of this file, were removed on 2026-09-15
and are in git.

## Now

- **Reader guides** (`docs/guides/`): written on 2026-09-15 — how to file a zgłoszenie
  zainteresowania, how to answer a public consultation, how to write to a committee, how a public
  hearing works, and what the bot and the channel are. They wait for a home: a URL, a design and
  short links from the messages («как подать?») in place of the paragraphs the cards carry today.
- **The RCL backlog: twelve live projects the bot has never walked.** Replaying wykaz discovery
  over the whole register with the watermark pushed back to 2015 (15 Sept 2026, against a
  restored production dump) ingests **nothing**: of the 56 entries the keywords accept, 34 are
  realised or withdrawn, 3 are the plans in the channel, 7 are followed as RCL projects and 12
  have a project on RCL this bot does not follow. So `report.wykaz_backlog` is a count and not a
  queue, and there is nothing to take in from the register.
  The twelve are the finding, and they are an RCL question: every one is **open**, pre-Sejm or
  just handed over, with fourteen stages reached — UD387 (zawód pielęgniarki, 12410308), UD384
  (zawody lekarza, 12411050), UD237 (mediatorzy sądowi, 12400301), UC82 (kredyt konsumencki,
  12399650), UDER87 and UD263 (Prawo o ruchu drogowym, 12409801 and 12413302), UD344 (Kodeks
  karny, 12405503), UD337 (rejestry, 12405805), UD329, UD334, UDER49, UPRO10. They were missed
  because RCL discovery walks the listing by modification date and the bot's watermark starts on
  2026-09-09: a project not touched since then is invisible, and there is no sweep. Two ways in,
  both existing: `/analyze RCL/<id>` one at a time (the prefilter still decides, so only what
  passes costs tokens), or one `run --since` far enough back, which walks the RCL listing to that
  date — and re-scans the Sejm term with it.

## Next — decided, designed, not built

### Weekly digest and the monthly report

A post every Sunday saying what happened in the channel that week — new cards, updates,
consultations that opened, sittings ahead — and, in the first digest of a month, what the month
cost and what it caught: entries seen, dropped by the keywords, read by the model, published, the
LLM spend in dollars, and a request for support (buymeacoffee, GitHub Sponsors).

The draft goes to the technical channel first, with an inline "publish" button; the press comes
back to the relay as a `callback_query`, is filed into the inbox like any operator command, and
the next run posts it to the public channel. Nothing reaches readers unread.

What it touches, in the order it has to be built:

- a `digest` phase in `services/pipeline.py`, after tracking so the week's own posts count, gated
  on the weekday **in Warsaw time** (`_full_day` compares UTC — do not copy that);
- the week's own content: there is no query for "publications between two dates" and no index on
  `publications.sent_at`; the monthly figures need neither, `repo.list_runs(since=…)` already
  returns every counter of `RunReport` and `llm_usage` (retention is 90 days);
- the first message that is not about one bill: `Outgoing.bill` is required, `publications.term`
  and `number` are NOT NULL and every unique index is keyed on them, so either the field relaxes
  or the row takes a sentinel number with a new partial unique index on `(channel_id, kind, ref)`,
  `ref` being the ISO week;
- `callback_query` handling in `lexinform listen`, and a command that publishes a digest by `ref`;
- `RunReport` has no "dropped by the keywords" counter — it is `discovered - prefilter_hits`
  unless one is added.

### The guides get a home

A domain, a static site, the guides and the page about the bot on it, and short links from the
cards («как подать?»). Until then the texts live in `docs/guides/`.

### Both government sources are read by date, and neither has a sweep

- **A register entry rewritten into relevance after publication is invisible**: `Data publikacji`
  does not move when an entry is edited. `wykaz_fingerprint` exists for exactly this and nothing
  reads it across the watermark.
- **An RCL project untouched since the bot's first run is invisible too** — the listing is walked
  by modification date. Twelve are known (see "Now"); a periodic sweep over a long window, or a
  one-off `run --since`, is the choice to make.

## Later — worth doing when there is a reason or a measurement

- **A conditional sitting is announced as a fact.** `notes` says otherwise on 18 sittings of term
  10 ("Posiedzenie aktualne w przypadku zgłoszenia poprawek…"); the same field carries the only
  application address for a przesłuchanie the API has (4 sittings). One regex, and the field is in
  the response already.
- **A scanned deputies' print shows no clubs.** `SejmAuthorsResolver.resolve` is handed `text`
  while the analysis already reads the pages; 11 prints of term 10 have an empty text layer.
- **22 prints carry the covering letter in the PDF and not in its text layer** — worth checking
  whether they are mixed PDFs, a scanned first page over a digital body.
- **An autopoprawka rewrites the bill and nothing points at it.** `{druk}-A`, 29 in term 10,
  visible only in `/bills` as `submissionType: BILL_AMENDMENT`. Detection is one query; what to do
  with it is the question, since re-analysing on its PDF would repeat the `carries_bill_text`
  mistake — the supplement path is probably right.
- **The plenary `schedule`** would say which day of a four-day sitting a bill is taken on.
- **`Analysis.confidence`** is asked for, stored, and read by nothing.
- **`PHASE_PATIENCE` has no entry for `rcl_opinions` or `rcl_consultation`**, so both fall back to
  the default patience.
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

- Chat and comments under the channel, with Rose moderating who joins.
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
| Tracking cap for passed bills without an act | 180 days |
| In-force reminder repeats the summary | yes |
| Re-fetch ELI metadata | only while `entry_into_force` is null |
| Probability that a bill passes | never estimated (the owner's decision) |
