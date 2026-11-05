# Shared amendment text reader

Schema v68 adds a nullable text reference to amendment snapshots. The existing snapshot API reconstructs the canonical question from its manifest and verifies the text digest, question digest and snapshot ID. Inline questions retain their stored representation. The pipeline producer remains off.

Two pinned prepared questions share the same `ctx.text`. The isolated table measurement includes schema and index overhead and checks exact reconstruction after a SQL dump round trip. [Storage measurement](report.json).

A fresh pinned copy of the `state` branch migrated from v67 to v68. The repository read druk 2699 from the restored copy. Every existing table preserved its original row values, and a second dump and restore reproduced the same dump. [State qualification](state-proof.json).

[Focused and full checks](qualification.json). [Reproduction recipes](reproduce.md).

[Exact CI and relay verification](release.json).
