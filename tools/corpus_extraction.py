"""Build bounded maintenance extractors and append-only caches with explicit input identities."""

import gzip
import json
import os
import platform
import tempfile
import zlib
from contextlib import nullcontext
from importlib.metadata import version
from pathlib import Path
from typing import Any

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.models.documents import ExtractedDocument
from tools.corpus_artifacts import digest
from tools.corpus_runtime import CorpusReads, source_fingerprints

MAX_BYTES = 32_000_000
OPTIONS = {"max_member_bytes": MAX_BYTES, "max_part_chars": 400_000}
CACHE_LIMIT = 128_000_000


def bounded_reader() -> DocumentTextExtractor:
    return DocumentTextExtractor(
        PypdfTextExtractor(),
        DocxTextExtractor(),
        DocTextExtractor(),
        max_member_bytes=OPTIONS["max_member_bytes"],
        max_part_chars=OPTIONS["max_part_chars"],
    )


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def extraction_identity() -> dict[str, Any]:
    return {
        "version": "bounded-document-v1",
        "options": dict(OPTIONS),
        "python": platform.python_version(),
        "libraries": {
            name: version(name) for name in ("pypdf", "inflate64", "olefile", "pydantic")
        },
        "source_sha256": {
            name: sha
            for name, sha in source_fingerprints().items()
            if name.startswith("src/") or name in {"uv.lock", "tools/corpus_extraction.py"}
        },
    }


class ExtractionCache:
    """Reuse exact bounded extraction results without replacing prior cache entries."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory.absolute()
        if self.directory.resolve() != self.directory:
            raise ValueError("Extraction cache must not contain symlinks")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.identity = extraction_identity()
        self.identity_sha256 = digest(canonical(self.identity))
        self.reader = bounded_reader()
        self.hits = 0
        self.misses = 0

    def extract_document(
        self, data: bytes, *, reads: CorpusReads | None = None
    ) -> ExtractedDocument:
        if len(data) > MAX_BYTES:
            raise ValueError("Raw source exceeds the 32 MB replay limit")
        raw_sha = digest(data)
        key = digest(canonical({"raw_sha256": raw_sha, "identity_sha256": self.identity_sha256}))
        path = self.directory / f"{key}.json.gz"
        if path.is_symlink():
            raise ValueError("Extraction cache entry must not be a symlink")
        if path.exists():
            cached = self._read(path, raw_sha)
            self.hits += 1
            return cached
        with reads.guard() if reads is not None else nullcontext():
            extracted = self.reader.extract_document(data)
        result = extracted.model_dump(mode="json")
        payload = canonical(
            {
                "version": 1,
                "identity": self.identity,
                "identity_sha256": self.identity_sha256,
                "raw_sha256": raw_sha,
                "result_sha256": digest(canonical(result)),
                "result": result,
            }
        )
        if len(payload) > CACHE_LIMIT:
            raise ValueError("Extraction cache result exceeds its 128 MB limit")
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.directory, prefix=".entry-", delete=False
            ) as out:
                temporary = Path(out.name)
                out.write(gzip.compress(payload, mtime=0))
            try:
                os.link(temporary, path)
            except FileExistsError:
                if self._read(path, raw_sha) != extracted:
                    raise ValueError("Concurrent extraction cache entries disagree") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.misses += 1
        return extracted

    def _read(self, path: Path, raw_sha: str) -> ExtractedDocument:
        if path.is_symlink() or path.resolve() != path:
            raise ValueError("Extraction cache entry must not be a symlink")
        try:
            with gzip.open(path, "rb") as stream:
                payload = stream.read(CACHE_LIMIT + 1)
            if len(payload) > CACHE_LIMIT:
                raise ValueError("Extraction cache result exceeds its 128 MB limit")
            entry = json.loads(payload)
            result = entry["result"]
            if (
                entry["version"] != 1
                or entry["identity"] != self.identity
                or entry["identity_sha256"] != self.identity_sha256
                or entry["raw_sha256"] != raw_sha
                or entry["result_sha256"] != digest(canonical(result))
            ):
                raise ValueError("Extraction cache identity or result hash mismatch")
            return ExtractedDocument.model_validate(result)
        except (
            OSError,
            EOFError,
            UnicodeError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
            zlib.error,
        ) as exc:
            raise ValueError(f"Invalid extraction cache entry: {path.name}") from exc
