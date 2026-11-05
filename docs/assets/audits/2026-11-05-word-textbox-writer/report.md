# Count Word textbox nodes in the visited legacy walk

The native Word status observer counts exact WordprocessingML textbox nodes already visited
by the legacy parser. It includes visited body and note nodes and follows the existing choice
of fallback branches. The counter says how many nodes the walk observed. It carries no text,
layout coordinates or claim about complete coverage.

The extraction adds the optional count after existing metadata. If the count would exceed
the existing summary bound, the previous summary is returned. An absent field still means
unknown. The prior reader accepts known counts, including zero.

The paired run replayed 3,055 pinned Word files. Old text, selection and metadata matched
after the declared extractor implementation fingerprints were separated. Of 3,053 Word
statuses, 26 had positive counts and the walk observed 36 nodes. The field added 82,431
bytes across the serialized extraction results and at most 27 bytes per result.
[Corpus measurements](corpus.json).

Six pinned results were saved on separate copies of the same checkpoint. The writer
variant added 162 SQL bytes and 77 gzip bytes relative to the reader-only variant.
The SQLite allocation was identical. Both copies passed double dump/restore, integrity
and foreign-key checks. [Checkpoint comparison](storage.json).

[Exact CI and relay verification](release.json).
