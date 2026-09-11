# Operator commands from the technical channel

Post a command in the technical (log) channel and the bot answers under it a few minutes
later. Any administrator of that channel can command; nobody else reaches the bot this way.

```
/analyze 3039                 fetch the bill, prefilter it (title, then text), analyse it, post
                              the card when it is relevant and important enough, follow it
/analyze 3039 force           analyse past the prefilter, a previous analysis and the cost guard
/analyze 3039 publish         post the card of a relevant bill even below the score threshold
/show 3039                    what the database knows: status, hits, verdict, card, last stage
/skip 3039                    silence a false positive: no analysis, no card (the card stays)
/republish 3039               post the card again (after a lost or failed post)
/help                         this list
```

A bill is named by any number the bot knows or by a link to it:

| Reference | Example |
|---|---|
| Print number of the current term | `3039`, `druk 3039` |
| Bill without a print number | `RPW/29075/2026` |
| RCL project id | `RCL/12414100` |
| Wykaz number of an RCL project already followed | `UC164`, `UD 247` |
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

`/analyze` of an RCL project that already reached the Sejm answers about its druk instead: the
rows are linked and the print carries the thread, so a project whose act is in force cannot get
a card that promises a druk number any day now. No card is posted for a bill the Sejm has
finished with (adopted, rejected, withdrawn) — the reply gives the verdict and says so.

Every reply ends with what the command took: when the run that answered it started, how long
the command itself ran, and the model tokens and dollars it spent — the triage included, since
it is part of the same bill. A command that never calls the model (`/show`, a bill whose
verdict is already stored) shows the time alone.

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

Needs Python 3.12, git, ~50 MB of RAM and outbound HTTPS; nothing inbound. On Ubuntu 24.04:

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

Updates are automatic: `.github/workflows/deploy-relay.yml` runs after every green CI on
`main` (and on demand from the Actions tab), connects with the deploy key in the repository
secret `MIKRUS_SSH_KEY` and runs `deploy/update.sh` on the server (fetch, reset to
`origin/main`, `uv sync`, restart). The key is bound to that script in `authorized_keys`
(`command="/opt/lexinform/deploy/update.sh",no-pty,…`), so it can do nothing else; the server's
host key is pinned in the workflow. By hand: `ssh mikrus /opt/lexinform/deploy/update.sh`.
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
