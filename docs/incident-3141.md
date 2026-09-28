# Incident 3141: two cards for one bill and a reply that restated the card

Статус: исторический разбор инцидента 28.09.2026.
Дефекты — [B56–B58 в реестре](BUGS.md).

Runs 163–165, 28 September 2026, 15:32–16:20 UTC: Actions
[36444271386](https://github.com/sobolevbel/lexinform/actions/runs/36444271386) (run 163),
[36447339919](https://github.com/sobolevbel/lexinform/actions/runs/36447339919) (run 164, batch
collection) and [36449587246](https://github.com/sobolevbel/lexinform/actions/runs/36449587246)
(run 165); running code `cb2fd0c`; state commits `a914d4b` (before run 165) and `08eff8c` (after).
Investigated on restored copies of state and offline fakes; production state was not edited and
no model calls were made.

## What readers received

RCL project 12412454 and druk 3141 are one government bill — «o zmianie ustawy o świadczeniach
opieki zdrowotnej finansowanych ze środków publicznych oraz niektórych innych ustaw», RM number
RM-0610-155-26, wykaz UD428. The channel got:

| Message | Sent (UTC) | Run | What it was |
|---|---|---|---|
| 65 | 15:56:56 | 164 | card «Новый законопроект — druk nr 3141», analysed on the print |
| 67 | 16:15:17 | 165 | card of RCL/12412454, re-rendered at 16:18:05 as the card of druk 3141 |
| 68 | 16:18:05 | 165 | reply under 67: «🔢 Присвоен номер druku — druk nr 3141» |

So two cards of druk 3141 a message apart, and a reply announcing a number the first card had
already carried for twenty minutes. After run 165 no row pointed at message 65: the linker had
moved the druk's card row to message 67, so the refresher would never have edited 65 again. The
operator deleted 65 and 68 by hand on 28 September; the database needed no repair, its one card
row already named 67.

## Why it happened

1. **Discovery could not join the druk to its project.** `/processes` carries no `rclNum` — it is
   in a process's detail only — and until run 163 the RCL row had no `rm_number` either, so
   `rcl_predecessor` had nothing to match and druk 3141 was ingested as a bill of its own. It was
   a text-prefilter hit (cudzoziemcy, obywatele Ukrainy, pobyt kwalifikowany), passed the triage
   at 0.75 and its analysis went to the batch.
2. **The RCL watcher did not find the druk in time.** `find_process_by_rcl_num` reads at most
   `MAX_RCL_LOOKUPS` process details, oldest first from the hand-over, and run 164 did not reach
   3141. Meanwhile the project, followed quietly (relevant, score 2, no card), was re-analysed
   on its new text.
3. **Run 164 carded the druk** (score 3, message 65).
4. **Run 165 carded the project as well** (B56). The re-analysis collected at 16:15 lifted
   RCL/12412454 to 3, and a re-analysis that clears the bar gives a bill without a card its card
   on the next publishing phase. Nothing asked whether the project's druk was already in the
   channel, although the database knew: the druk's row carried `rcl_num` RM-0610-155-26 and the
   project's row carried the same `rm_number`.
5. **The linker overwrote the druk's card** (B57). At 16:17:25 the RCL watcher found the druk and
   `Linker.link` handed the project's card over as if the druk had none: `inherit_card` upserted
   the druk's `new_bill` row onto message 67, `_adopt` wrote the project's analysis over the
   druk's own (its `source_url` became the RCL zip), and the "print assigned" update went out.
6. **A listing row erases what only a detail knows** (B58). `upsert_summary` stored every summary
   whole, so each discovery pass over the listing reset the druk's `rcl_num` to None until a
   detail was read again. It did not change this incident's outcome, but it is why a check keyed
   on the druk's `rcl_num` alone could not be relied on: the fix of B56 failed its own test until
   B58 was fixed.

## Changes made

- `PublishingService.became_druk`: an RCL project or RPW entry gets no card when its druk is
  already in the database — by the stamped print number, by the RM number its druk's process
  names (`BillRepository.find_print_by_rcl_num`, spacing and case aside), or by the `/bills`
  entry's print. Tracking links the two and the druk carries the thread.
- `Linker._keep_prints_card`: a print that already has its own sent card keeps it, with its
  analysis and stages; the entry is only linked, and no "print assigned" reply is sent. When the
  entry had a card too, its message id is logged as left without a thread.
- `SqliteBillRepository.upsert_summary` keeps a stored `rcl_num` and `rcl_link` when the new
  summary has none.

Regressions in `tests/scenario/test_tracking_rcl.py`:
`test_a_project_whose_druk_already_has_a_card_gets_no_second_one` replays the sequence above run
by run, and `test_a_druk_carded_beside_its_projects_card_keeps_its_own_thread` starts from both
cards already sent. Without the linker change both fail — the druk loses its analysis and its
card row moves to the project's message.

## What is still possible

A project carded first can still see its druk carded beside it when the listing gives the druk
no `rclNum` and the watcher has not found it yet: the druk is a Sejm bill and passes every gate.
After this fix the linker keeps the druk's card as the thread and logs the project's orphaned
message, which has to be deleted by hand. Closing it would need the druk's candidate to look up
its project before publishing, which costs a detail read per new druk.
