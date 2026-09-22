# Operator commands from the technical channel

Post a command in the technical (log) channel and the bot answers under it a few minutes
later. Any administrator of that channel can command; nobody else reaches the bot this way.

**Every `lexinform` command is here**, under its own name and with its own options. Three are
not, and could not be: `listen` is this relay, `commands` is the phase that answers what it
files, and `db init|dump|restore` is the workflow's handling of the state branch around every
run — none of them is a thing to ask a run for, and a `db restore` from a channel would replace
the database under a running channel. `--yes`, which `republish`, `forget` and `reset` take on a
terminal, has no twin either: posting the command *is* the confirmation.

### One bill

```
/analyze 3039                 fetch the bill, prefilter it (title, then text), analyse it, post
                              the card when it is relevant and important enough, follow it
/analyze 3039 force           analyse past the prefilter, a previous analysis and the cost guard
/analyze 3039 publish         post the card of a relevant bill even below the score threshold
/analyze 3039 json            add the stored analysis as raw JSON under the verdict
/show 3039                    what the database knows: status, hits, verdict, card, last stage
/preview 3039                 the card as the channel would get it, rendered here and posted
                              nowhere else
/preview 3039 to=-1001234567  …and sent to that chat instead (a test channel, or @name)
/refresh 3039                 check this bill now: stages, act, sittings, its card — what the
                              next scheduled run would have found
/skip 3039                    silence a false positive: no analysis, no card (the card stays)
/unskip 3039                  the way back: the bill queues for the next run's analysis
/reset 3039 to=skipped_cost   any status at all, with a clean budget of attempts
                              (`to=` defaults to `analysis_pending`, which is `/unskip`)
/republish 3039               post the card again (after a lost or failed post)
/forget 3039                  drop the card the channel remembers, post nothing (deleted by hand)
```

### The database, answered here and now

```
/find cudzoziemc              bills whose title or number carries the words
/status                       the queues, what is stuck, what the last 7 days of runs cost
/runs days=7                  what each recorded run found, posted and cost (default 30 days)
/cost days=7 top=3            the model spend: per model, the dearest run, the dearest bills
/digest                       draft the week that has just ended, here, with a publish button
/digest ref=2026-W38            some other week, by its ISO number
/digest publish ref=2026-W38    send it to the readers' channel — what the button does
/help                         this list
```

### A run, or one phase of it

```
/run                          the whole day: discover, prefilter, analyse, publish, track
/run since=2026-09-01           discover from this date instead of the watermark
/run dry                        print what would be posted, persist and publish nothing
/run reprefilter=50             first scan the texts of up to N bills the title prefilter skipped
/run reprefilter=200 text_skipped   …and the bills the text stage itself rejected (see below)
/run index_rcl_since=2023-11-01 first read the RCL listing back to this date for the wykaz join
/run no_publish no_track no_rcl full_track      the run's own switches
/run max_publish=5 max_analyze=3 min_score=4    and its caps
/scan since=2026-09-01        discover and keyword-filter only: no model, no posts, no tokens
/track                        the tracking phase over every followed bill (`dry` to see it only)
/collect-batches               write down a finished batch, then analyse/publish/track what that frees
/reprefilter limit=200 text_skipped   the backfill on its own, without the run behind it
/index-rcl-numbers since=2023-11-01   the RCL listing for the wykaz join (`/index` for short)
```

These six are the commands a run does not execute, because each **is** a run: the relay asks
GitHub to start `daily.yml` (`workflow_dispatch` on `main`) with the inputs named after it and
answers «▶️ scan started …» with a link, instead of filing anything into the inbox. The workflow
takes a `command` — which `lexinform` subcommand this run is — and an `options` string carrying
its flags; every value in it is checked by the relay first, because the workflow would take
`since=вчера` as free text and answer hours later with a job that did the wrong thing. `dry_run`
stays an input of its own rather than one more flag in `options`, since it is also what keeps
the state branch unpushed, and the two must never disagree about what a run persisted. Options
may be combined in any order (`/run dry since=2026-09-01 min_score=4`). Two things follow from
`workflow_dispatch`: the token needs **Actions: read and write** on the repository (filing a
command only needs Contents), and a dry run persists nothing at all — not even an index it was
asked to build, since the state branch is only pushed by a real run.

`/run reprefilter=N` and `/reprefilter limit=N` are not the same thing: the first is a backfill
*and then* a whole run, which is what the composition is for (two dispatches would be two runs,
and the concurrency group keeps only the newest pending one), the second is the backfill alone.

An option a command does not know is answered rather than ignored, including one that belongs to
another command: `/show 3039 force` used to do nothing and say nothing, which reads exactly like
it did something. A `key=value` is only read as an option where the command knows the key, so a
Sejm link full of them (`…druk.xsp?nr=3039`) is still just a link.

`text_skipped` rides on `reprefilter` and is the only way back for a bill the **text** stage
rejected. That skip is revisited by nothing: not by a later run, only by a changed title
(`discovery._ingest`) or by `/unskip`, one bill at a time. So a row whose reason was never
recorded, and a print whose PDF the Sejm had simply not uploaded when the run asked
("no document to read"), stay closed until a run is asked for this. It costs downloads and
keyword matching, no tokens — but a bill that passes becomes an analysis candidate, so the run
after it pays for the readings and posts the cards; give it a `limit` you are willing to read.

`/preview` answers with the card itself, links and tags and all, so the wording can be read
before `/republish` sends it — and so that a bill held under the score threshold can be looked
at without posting anything. For a print that will reply under another's card it shows the reply,
and without the «чем отличается» block when no comparison is stored yet: that block is a model
call, a read-only command spends nothing, and the note says the comparison is made when the reply
goes out. `/refresh` runs the tracking phase for one bill: the reader waits
for the Sejm, not for 05:23 UTC, and an update the Sejm published an hour ago can be posted now.
It leaves the reminders to the scheduled run — those are due-date queries over the whole
channel, not about the bill that was named. `/unskip` clears the skip and the spent attempts and
puts the bill back in the queue; an RCL project keeps only its skeleton while it is skipped, so
its documents are read again first, and `/reset` does the same for whichever status it is given.
`to=` names a `BillStatus` and the reply repeats what the row was, attempts and all — the one
command that can put a bill anywhere, which is why it says where it came from.

`/preview BILL to=CHAT` sends the card to that chat and records nothing: no `publications` row,
so the bill is no more published afterwards than before and the channel's own card is untouched.
The bot has to be able to post there, and a failure is answered as one.

`/status`, `/runs` and `/cost` are what no single run report says. `/status` is the state
between the runs: the queues as they stand, how many bills are followed, and by bill number and
kind the posts stuck `pending`/`unknown` — nothing retries those on its own (BUGS.md #4), so this
is what tells the operator one is there; `/republish` clears it for a card, and for any other
kind of reply there is no command yet, only a look at the row. `/runs` is the row per run that
`lexinform runs` prints — what each found, posted and cost — and `/cost` breaks the window's
spend down by model, names the dearest run and the dearest analyses. All three read the database
and nothing else: no request, no token, whatever the window.

`/forget` is `/republish` without the post, for a card deleted from the channel by hand. The row
goes on saying `sent` until something clears it, and everything downstream believes it: the bill
stays in `list_tracked`, the refresher edits a message that is not there once a run (Telegram
answers `message to edit not found`, which is a warning and not an error, so it repeats for
ever), and an update would reply under nothing. `/republish` clears the same two rows by sending
the card again — the right answer when the post was lost, the wrong one when it was deleted on
purpose. What the bill gets afterwards is the publishing rule's decision, and the reply says
which way it went: still analysed, relevant and above the threshold means the next run posts a
fresh card; silenced or below it means the bill simply stops being followed. Running it twice
changes nothing the first run did not.

A bill is named by any number the bot knows or by a link to it:

| Reference | Example |
|---|---|
| Print number of the current term | `3039`, `druk 3039` |
| Bill without a print number | `RPW/29075/2026` |
| RCL project id | `RCL/12414100` |
| Wykaz number | `UC164`, `UD 247`, `WPL/UD408` — the RCL project when it is out, the register entry before that |
| RM number of a government print | `RM-0610-139-26` (resolved through RCL) |
| Sejm links | `…/Sejm10.nsf/PrzebiegProc.xsp?nr=3039`, `…/druk.xsp?nr=3039`, `api.sejm.gov.pl/sejm/term10/processes/3039`, the consultation page with `NrProjektu=RPW/…` |
| RCL links | `legislacja.rcl.gov.pl/projekt/12414100` (any subpage), `getIdFromLegislacja?number=RM-…` |

A Sejm link carries the term; a bare print number means the current term (older terms are
searched for a bill already in the database).

## How a command travels

```
technical channel ──getUpdates──► lexinform listen (VPS) ──Contents API──► branch `inbox`
                                          │ repository_dispatch                         │
                  ◄──reply under the command── daily.yml: lexinform commands ◄─────────┘
```

1. `lexinform listen` runs on a small always-on server and long-polls Telegram for posts in the
   log channel. A post that starts with `/` becomes `inbox/{update_id}.json` on the git branch
   `inbox`, written through the GitHub Contents API with a personal access token, and the relay
   sends a `repository_dispatch` event (same token) that starts the workflow within seconds
   (only cron runs are delayed; a push of the inbox branch itself would start nothing, because
   GitHub reads a push event's workflow from the pushed branch, which has none). The relay
   replies "⏳ queued" under the post and only then confirms the update to Telegram, so a GitHub
   outage leaves the command with Telegram (kept for 24 hours).
2. `.github/workflows/daily.yml` checks out `state` and `inbox`, restores the database and runs
   `lexinform commands`: the commands phase alone. Every scheduled run does the same phase first,
   so a command whose event was lost is answered by the next scheduled run.
3. The run records the command in the `commands` table before executing it, executes it, marks
   it executed, answers under the command's message, marks it handled and deletes the file; the
   workflow commits the deletions. A file that comes back (the deletion was not pushed, the job
   died after the post) is measured against those two marks: answered means it is only deleted,
   executed but unanswered means the answer is repeated and the command is *not* run a second
   time — a repeated `/republish` would post a second card. Runs never race: one concurrency
   group, and a pending run replaced by a newer one is harmless because every run drains the
   whole inbox.

`/analyze` of an RCL project or an `RPW/…` entry that already reached the Sejm answers about its
druk instead: the rows are linked and the print carries the thread, so an entry whose act is in
force cannot get a card that promises a druk number any day now. Named the other way round — the
druk of an entry already in the channel — the print joins that card instead of getting a second
one, exactly as the next run's tracking would have linked them. No card is posted for a bill
whose road has ended (the act is in Dziennik Ustaw, the bill was rejected or withdrawn, the
project or the plan was dropped) — the reply gives the verdict and says so. A bill the Sejm has
only passed still gets its card: the Senate and the President are ahead.

Every reply ends with what the command took: when the run that answered it started, how long
the command itself ran, and the model tokens and dollars it spent — the triage included, since
it is part of the same bill. A command that never calls the model (`/show`, `/find`, `/status`,
a bill whose verdict is already stored) shows the time alone; `/refresh` counts what a
re-analysis of a new text cost, because that is the run's spending done under the operator's
hand.

### The weekly digest and its button

On the digest's weekday (Sunday in Warsaw, `LEXINFORM_DIGEST_WEEKDAY`) the run drafts the week
into the technical channel and posts nothing to readers. The draft carries one inline button;
pressing it is not a second way into the bot but the command `/digest publish ref=…`, filed into
the inbox like any typed one and executed by the run the relay starts. Telegram delivers a press
as a `callback_query`, so the relay answers it through `answerCallbackQuery` — the button stops
spinning — rather than with a "queued" post, a press having no message of its own to reply
under. The week is built again from the database at the moment it is published, so a draft left
standing for a day cannot go stale, and a second press changes nothing: the command row is
answered rather than executed again, and the digest's publication row is unique per week and
channel. `/digest` on any other day drafts that week all the same, and spends nothing either way
— the digest never asks the model.

An outage of the Sejm API, RCL, the model or Telegram ends the phase and leaves the command for
the next run; a mistake in the command (unknown bill, bad link) is answered as such. A text
whose first analysis would cost more than the per-bill guard allows is answered with the
estimate and the hint to add `force`, and the bill is left in `skipped_cost` exactly as the
daily run's analysis phase leaves it.

## Setup

### GitHub

1. Create the branch once (empty, orphan):
   ```bash
   git checkout --orphan inbox && git rm -rf --quiet . && mkdir inbox && touch inbox/.gitkeep
   git add inbox/.gitkeep && git commit -m "inbox: operator commands" && git push origin inbox
   git checkout main
   ```
2. Create a fine-grained personal access token (Settings → Developer settings → Fine-grained
   tokens): this repository only, permission **Contents: read and write**, nothing else. It is
   the only credential the relay holds for GitHub; a leak lets someone write files to the
   repository's branches, not read secrets or run workflows with them.

### The server (the relay)

Needs git, ~50 MB of RAM and outbound HTTPS; nothing inbound. Python 3.14 is `uv sync`'s own
business — it downloads a standalone build (~70 MB of disk), so the distribution's Python does
not matter. On Ubuntu 24.04:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh          # uv into ~/.local/bin
git clone https://github.com/sobolevbel/lexinform /opt/lexinform
cd /opt/lexinform && ~/.local/bin/uv sync --frozen --no-dev
cat > .env <<'EOF'
LEXINFORM_TELEGRAM_BOT_TOKEN=...
LEXINFORM_TELEGRAM_LOG_CHANNEL_ID=-100...
LEXINFORM_GITHUB_REPO=sobolevbel/lexinform
LEXINFORM_GITHUB_TOKEN=github_pat_...
EOF
chmod 600 .env
~/.local/bin/uv run --frozen --no-dev lexinform listen --once --dry-run   # reads, files nothing
cp deploy/lexinform-listen.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now lexinform-listen
journalctl -u lexinform-listen -f
```

### The batch poller (optional, LEXINFORM_LLM_BATCH_ENABLED only)

Same server, its own unit pair: a timer fires `lexinform poll-batches` every 20 minutes, which
asks GitHub for a `collect-batches` run when the active provider (`LEXINFORM_LLM_BATCH_PROVIDER`)
reports a finished batch — sooner than the next scheduled `daily.yml`, which would find it anyway.
No database, no state of its own: `deploy/update.sh` installs and enables it automatically once
its unit files are in `deploy/`, the same way it manages the relay's. Add to `.env`:

```bash
ANTHROPIC_API_KEY=...   # or OPENAI_API_KEY=..., matching LEXINFORM_LLM_BATCH_PROVIDER
LEXINFORM_LLM_BATCH_ENABLED=true
```

`LEXINFORM_GITHUB_REPO`/`_TOKEN` are already there for the relay; the same Contents-scoped token
is enough for `poll-batches`' `repository_dispatch` too (see the GitHub section above). Manual
check: `~/.local/bin/uv run --frozen --no-dev lexinform poll-batches`.

Updates are automatic: `.github/workflows/deploy-relay.yml` runs after every green CI on
`main` (and on demand from the Actions tab), connects with the deploy key in the repository
secret `MIKRUS_SSH_KEY` and runs `deploy/update.sh` on the server (fetch, reset to
`origin/main`, `uv sync`, restart). The key is bound to that script in `authorized_keys`
(`command="/opt/lexinform/deploy/update.sh",no-pty,…`), so it can do nothing else; the server's
host key is pinned in the workflow. By hand, against the host the workflow names (`HOST` in
`deploy-relay.yml`): `ssh root@… /opt/lexinform/deploy/update.sh`.
The bot itself needs no deploy: every run of `daily.yml` checks out `main`.

To rotate the key: `ssh-keygen -t ed25519 -N "" -f deploy_key -C lexinform-deploy`, replace the
`lexinform-deploy` line in the server's `~/.ssh/authorized_keys` (keep the `command=` prefix),
`gh secret set MIKRUS_SSH_KEY < deploy_key`, delete the local files.

Rules of the road:

- Telegram allows **one `getUpdates` consumer per bot**. A second `lexinform listen` (a local
  one, say) steals updates from the server's: use `--dry-run`, which confirms nothing, and
  never set a webhook for the bot (`deleteWebhook` if one exists).
- The relay must be the bot whose token the runs use, and an administrator of the log channel
  (it already is: it posts the run reports there).
- A command that cannot be filed holds the relay where it is: the offset stays below it, so
  Telegram delivers it again, and the loop waits before the next poll — 15 s, doubling to ten
  minutes while the failures continue, back to nothing on the first poll that gets through.
  A refusal no retry fixes (a revoked PAT, a repository the token may no longer write) therefore
  shows in `journalctl -u lexinform-listen` as the same warning every few minutes with no
  "⏳ queued" in the channel; the queued commands are all still with Telegram, which keeps them
  for 24 hours, so a token replaced within the day loses nothing.
- `LEXINFORM_INBOX_DIR` is set by the workflow; locally, point it at any directory of
  `{update_id}.json` files and run `lexinform commands --dry-run` to see the replies without
  posting (the model is still called for `/analyze`).

## Trying it

- `lexinform commands --dry-run` against the production dump (see `CONTRIBUTING.md`) with a
  hand-written `inbox/1.json`:
  ```json
  {"update_id": 1, "chat_id": "-100", "message_id": 1, "text": "/analyze 3039",
   "received_at": "2026-09-11T08:00:00+00:00"}
  ```
- `lexinform listen --once --dry-run` with the real token: prints the pending posts of the
  channel, files and confirms nothing.
- End to end: post `/help` in the log channel, watch the run start on the event and the reply
  appear; then `/analyze` of a bill known to be irrelevant (no card) and of a relevant one.
