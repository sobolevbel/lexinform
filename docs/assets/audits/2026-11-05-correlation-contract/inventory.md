# DA10.1 finite correlation contract inventory

Exact base is f050d68c + J233 two-file patch. The inventory recipe scans the actual repository AST and retains every dynamic SQL call for explicit review; raw SQL strings are not executed. SQL readers/validators in the other adapter modules do not own new writes. Migrations/restore and the three delegated public wrappers are accounted separately.

98 direct write/schema/restore methods: 15 required-identity boundaries, nine supporting metadata/edge methods, seven maintenance/rehydration methods, 67 operational projection/work/cache/control methods. 37 nonliteral SQL callsites are individually classified. All305 execute/executemany/executescript call ASTs remain exact.

| Required identity | Native producer/event |
| --- | --- |
| run/command | Existing pipeline/drain actual runs row and command-handler context; fan_out copies context and restores scopes. |
| bill key | Explicit source/result/application/intent/item/binding fields; status updates use the already loaded physical owner. Publication upsert masks a supplied owner because conflicts can retain another. |
| observation/selection | source observation save; applicability observation save; native selection-artifact/extraction edge saves. |
| artifact/read/extraction | Complete-body metadata, artifact-read receipt, extraction save, selection-extraction binding. |
| input | Exact frozen queued request SHA; submitted-item saved request SHA. Only actual receipt artifact/extraction/selection parents; missing/opaque receipt parents are absent and do not inherit outer scope. |
| result/application | Existing retained computation and actual application composite identity; no numeric ID lookup or invented input ID. |
| content/publication | Native content-version save, publication-content binding, resolved publication-upsert ID/status and existing physical status update. |

Every native ON CONFLICT/OR IGNORE path was checked. Immutable identity events follow saved-row equality validation. Plain submitted-item insert fails before events on a collision. Intent events require cursor.rowcount==1; ignored conflicting input logs no new parents. Publication upsert reports its resolved ID/status and withholds supplied owner. Missing physical status-update rows emit no success-shaped trace. Rollback can follow any local event: none attests commit, checkpoint, provider acceptance or Telegram delivery.

Supporting catalog/omission/physical/edition/snapshot-specific IDs remain in their dedicated typed SQL rows, with core endpoints traced at their native boundaries. They are known SQL-only IDs, not reclassified as unknown. Operational projection/cache/cleanup methods do not become a universal SQL mutation journal. Restore/import never fabricate historical events. Existing SQL doctors/reports retain their actual IDs; this adds no stored report/history payload.

The exact non-DEBUG repository AST is invariant after explicitly normalizing one cursor-result capture and two log-only guards (inserted.rowcount==1; original is not None). The recipe asserts those exact forms/counts before comparison. No other business AST or SQL call is normalized away.

Acceptance is the current finite DA10.1 correlation contract, not complete DA3/DA5 maps or DA10.7 natural cycles. Unknown legacy request/input/source parents remain absent; source block/physical maps are not inferred from text.
