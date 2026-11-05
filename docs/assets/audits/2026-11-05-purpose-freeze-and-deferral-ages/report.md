# Narrow the context saved for status updates and group document wait ages

New status updates omit the consultation-review record when freezing the Bill for
delivery. The dedicated consultation-review publication still freezes its review.
Existing plans and live Bill records keep their original contents.

On the pinned state, publication 260 had a consultation review with three context
documents. Fresh preparation with and without the field gave the same route, render
and digest model. The stored plan was 502,289 bytes. Replacing only that plan on
an isolated copy with the new projection gave 71,690 bytes and reduced the full gzip
dump by 109,168 bytes. This is a counterfactual for one row.
[Replay and sizes](consultation-freeze.json).

Document retries now record a finite reason at the branch that actually deferred work.
The existing retry policy, problem text and handling of outages remain in effect.
The work doctor groups waiting documents by the last saved reason when an observation
clock is supplied and at least one reason is known. The age starts at saved work
creation. The saved reason can precede a pending batch and does not establish a
current blocker. Legacy rows without a reason are grouped as unknown alongside
coded rows.

Three immutable snapshots with 4, 4 and 6 waiting rows respectively produced identical
default, age and timing reports before and after the change. A lifecycle test covers a
missing source, later batch preparation and completion.
[Legacy report parity](legacy-age-parity.json).

The exact stage passed hosted CI and the relay transition, restart and timer checks.
[Release receipt](release.json).
