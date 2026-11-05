# Continuation child availability

The retained-state doctor now inventories each local publication ID explicitly listed in a saved `continuation_ids` slot. A missing child yields an advisory finding with a bounded row/slot reference. Retained parent and child checks use observed local ID membership, so damaged integers outside SQLite signed64 do not crash diagnostics or appear in the report.

Cleaned/unreadable plans leave membership unavailable. Missing publication schema produces null counts. Presence proves only local availability; ownership, Telegram identity, delivery and resend eligibility remain outside this check. Runtime delivery, repository writes and schema are unchanged.

The real public `freeze_parts` producer creates the test root/children without sending. Deleting only the final child reproduces the original false negative while surviving parent edges remain valid. Five new unit cases cover complete, missing-last, cleaned, oversized-child and oversized-parent states; the existing missing-schema unit is extended. No scenarios were added.

Author63focused, strict scoped types, Ruff and format pass. Both immutable pinned states preserve seven established report objects and every prior retained-state field; only the child census and explicit limitation are added. [Qualification](qualification.json), [source pins](source-pins.json), [independent review](peer-review.json), [reproducer](reproducer-proof.json), [pinned states](pinned-proof.json).

Root full gate, final integration and complete requirement acceptance follow. Raw legislative originals remain separately deferred.

Root43focused/8927full tests/300.87s/92%coverage, mypy769/Ruff838 and all838source/config pins pass. [Integration](integration-proof.json): seven prior report profiles and all prior retained-state fields exact, source unchanged, new child census226publications/2present edges/0missing/59unavailable plans. Actual release follows publication.
