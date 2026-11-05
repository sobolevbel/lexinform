# Capture of prepared amendment text

Fresh text-only amendment requests save the exact question returned by the batch builder. A
manifest and content-addressed UTF-8 text are committed with the queued intent. The first
validated paid result keeps the reference after collection. Historical intents and results
keep their existing references or their recorded absence. Scan inputs keep their existing
scan manifests. Schema v68 already reads both encodings and was
[deployed before capture](../2026-11-05-amendment-shared-text-reader/release.json).

Cleanup removes unreferenced manifests and then unreferenced text. It may clear an operational
item reference after an exact paid failure has been consumed and accounted, the provider batch
has been forgotten, the bill has settled and it has no unfinished document, review or delivery
work. Pending and unknown delivery keep the text pinned. A successful paid result keeps its
reference even before publication. Existing corrections still follow the source fetch path.

## Offline qualification

The frozen J253 V2 replay preserved the request and decision core across three actual text
preparations using pinned corpus inputs. Nineteen controls cover the returned question,
rollback, shared storage, active pins, collection, legacy absence and original correction
targets. The original correction target controls ran with source reuse off. Corpus preparation
and controls used local fixtures and fake providers.

For lifecycle measurements, run the same collect and pin controls against the source from
`5de1bc60` and the candidate. Sample the repository dump after queueing, each collection and
cleanup. Measure UTF-8 SQL bytes, gzip bytes with mtime zero, and SQLite allocated bytes as
page count times page size. For row comparison, omit the two body tables and remove only
`amendment_input_ref` from parsed carrier JSON. The remaining row values matched at all
31 observations. [Driver pins, case counts and measurements](qualification.json).

For the successful fixture, the post-cleanup dump grew from 48,838 to 93,807 bytes of SQL
and from 11,202 to 23,632 bytes compressed. SQLite allocation grew by 36,864 bytes. A settled
failed request or cancelled queue returned to the baseline SQL and gzip size after cleanup.
SQLite retains freed pages for reuse. Pending and unknown delivery retained the failed input,
as intended. These are whole-dump measurements for the fixture. Successful unique text adds
retained data over time and this replay establishes no universal size cap.
