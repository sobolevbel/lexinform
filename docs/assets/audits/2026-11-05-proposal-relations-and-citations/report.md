# Read a confirmed Sejm proposal relation and check quotes across two editions

The candidate-trace reader accepts a bounded Sejm original-proposal relation only
when the selected documents, edition and physical artifacts agree with the saved
observation. Existing traces keep their identifiers and encoding. This stage adds
the reader, with no native writer.

The pinned 2320-A and 2320 inputs reproduce the official singleton relation and the
actual version-choice path. In the isolated database the 1,140-byte trace added
1,516 bytes of SQL and 324 bytes of gzip. Two restores reproduced the dump exactly.
Ten legacy traces retained their identifiers and bytes.
[Reader replay](sejm-reader.json).

The offline check for changes since the previous edition binds one saved claim to
two unique literal quotes. Each side has its own document identity, artifact hash,
text hash and role. The pinned UD325 pair used drafts from public consultations and
later review. Both quotes matched. Altering a duration invalidated only the later
quote. The fixture checks technical evidence binding and leaves legal meaning
and adoption unassessed.
[Paired edition replay](paired-editions.json).
