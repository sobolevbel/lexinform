# Повторяющиеся ошибки

Статус: памятка по историческим расследованиям, перенесена 24 сентября 2026.
[Открытые дефекты](../BUGS.md) · [Архив исправлений](../archive/bugs.md)

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
