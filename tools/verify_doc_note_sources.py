"""Independently check declared DOC links and semantic mutations against a pinned old reader."""

import argparse
import io
import json
import struct
import types
import zipfile
from pathlib import Path
from typing import Any

import olefile

from lexinform.adapters import doc_notes, doc_text
from lexinform.adapters.doc_text import DocTextExtractor
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runtime import CorpusReads
from tools.replay_doc_notes import physical_snapshot

BASELINE_SHA = "0216af4fddc5af723554137b27498862c2893dd1dbd7e5eeddd74f29280f7dee"
MUTATIONS = (
    "do 31 grudnia 2099 r.",
    "oplata 99999999 zl",
    "nie przysluguje",
    "tylko obywatele Ukrainy",
    "art. 999 ust. 8",
)


def position(word: bytes, table: bytes, cp: int) -> tuple[int, int]:
    start, size = struct.unpack_from("<II", word, 0x1A2)
    clx = table[start : start + size]
    at = 0
    while clx[at] == 1:
        at += 3 + struct.unpack_from("<h", clx, at + 1)[0]
    size = struct.unpack_from("<I", clx, at + 1)[0]
    count = (size - 4) // 12
    plc = clx[at + 5 :]
    cps = struct.unpack_from(f"<{count + 1}I", plc)
    matches = [index for index in range(count) if cps[index] <= cp < cps[index + 1]]
    if len(matches) != 1:
        raise ValueError("Independent CP lookup is not unique")
    index = matches[0]
    raw = struct.unpack_from("<I", plc, 4 * (count + 1) + 8 * index + 2)[0]
    width = 1 if raw & 0x40000000 else 2
    offset = raw & 0x3FFFFFFF
    offset = offset // 2 if width == 1 else offset
    address = offset + width * (cp - cps[index])
    if address + width > struct.unpack_from("<I", word, 0x40)[0]:
        raise ValueError("Independent CP exceeds meaningful WordDocument bytes")
    return address, width


def raw_links(word: bytes, table: bytes) -> list[tuple[str, int, int, int, int, int]]:
    counts = struct.unpack_from("<8i", word, 0x4C)
    result = []
    for kind, pair, base in (
        ("footnote", 2, counts[0]),
        ("endnote", 46, sum(counts[:5])),
    ):
        ref, size, text, _ = struct.unpack_from("<4I", word, 0x9A + pair * 8)
        count = (size - 4) // 6 if size else 0
        for index in range(count):
            call = struct.unpack_from("<I", table, ref + 4 * index)[0]
            flag = struct.unpack_from("<H", table, ref + 4 * (count + 1) + 2 * index)[0]
            start, end = struct.unpack_from("<2I", table, text + 4 * index)
            result.append((kind, call, start, end, base, flag))
    return result


def packed_members(members: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as target:
        for name, data in members.items():
            target.writestr(zipfile.ZipInfo(name), data)
    return stream.getvalue()


def qualify(
    corpus: Path,
    manifest_path: Path,
    baseline_path: Path,
    output: Path,
    *,
    physical_binding: bool = False,
    default_note_writer: bool = False,
) -> dict[str, Any]:
    if default_note_writer and not physical_binding:
        raise ValueError("Default DOC writer checks require physical binding")
    source = read_artifact(baseline_path)
    if digest(source) != BASELINE_SHA:
        raise ValueError("Wrong baseline source")
    baseline = types.ModuleType("pinned_doc_text")
    exec(compile(source, str(baseline_path), "exec"), baseline.__dict__)
    manifest = json.loads(read_artifact(manifest_path))
    driver_sha = digest(read_artifact(Path(__file__)))
    producer_sources = {
        "src/lexinform/adapters/doc_notes.py": digest(read_artifact(Path(doc_notes.__file__))),
        "src/lexinform/adapters/doc_text.py": digest(read_artifact(Path(doc_text.__file__))),
    }
    reads = CorpusReads(corpus)
    reader = DocTextExtractor()
    observed = {}
    derived: list[dict[str, str]] = []
    verified = 0
    packages: list[dict[str, Any]] = []
    with reads.guard():
        if digest((corpus / "INDEX.json").read_bytes()) != manifest["index_sha256"]:
            raise ValueError("INDEX changed")
        for case in manifest["cases"]:
            if case["extension"] != ".doc":
                continue
            path = corpus / case["path"]
            reads.require(path)
            data = path.read_bytes()
            identity = digest(data)
            if identity != case["sha256"]:
                raise ValueError("Raw input changed")
            if identity in observed:
                continue
            original = baseline.DocTextExtractor().extract(data)
            result = reader.extract_with_notes(data)
            if result.input_sha256 != identity or original != result.result.legacy_text:
                raise ValueError("Opt-in capture changed baseline text/identity")
            observed[identity] = digest(original.encode())
            links = result.result.links
            if links is None or not links.links:
                continue
            with olefile.OleFileIO(io.BytesIO(data)) as ole:
                word = ole.openstream("WordDocument").read()
                table = ole.openstream(links.table_name).read()
            if physical_binding and not packages:
                packed = packed_members(
                    {
                        "first.doc": data,
                        "nested.zip": packed_members({"second.doc": data}),
                    }
                )
                snapshot = physical_snapshot(packed, default_note_writer=default_note_writer)
                if snapshot["captured_leaves"] != 2:
                    raise ValueError("Duplicate natural DOC leaves lost their physical bindings")
                packages.append(
                    {"source_sha256": identity, "package_sha256": digest(packed), **snapshot}
                )
            expected = raw_links(word, table)
            actual = [
                (
                    link.kind,
                    link.reference_cp,
                    link.note_start_cp,
                    link.note_end_cp,
                    link.story_start_cp,
                    link.numbering_flag,
                )
                for link in links.links
            ]
            if actual != expected:
                raise ValueError("Reader links differ from independent raw PLC pairs")
            for _, call, start, end, base, flag in expected:
                address, width = position(word, table, call)
                if flag and int.from_bytes(word[address : address + width], "little") != 2:
                    raise ValueError("Independent reference marker differs")
                address, width = position(word, table, base + end - 1)
                if int.from_bytes(word[address : address + width], "little") != 13:
                    raise ValueError("Independent terminal CR differs")
                verified += 1
                if derived or end - start < 40:
                    continue
                for mutation in MUTATIONS:
                    changed = bytearray(word)
                    for index, char in enumerate(mutation):
                        address, width = position(word, table, base + start + 1 + index)
                        changed[address : address + width] = ord(char).to_bytes(width, "little")
                    buffer = io.BytesIO(data)
                    with olefile.OleFileIO(buffer, write_mode=True) as ole:
                        ole.write_stream("WordDocument", bytes(changed))
                    variant = buffer.getvalue()
                    old = baseline.DocTextExtractor().extract(variant)
                    new = reader.extract_with_notes(variant)
                    if old == original or mutation not in old or old != new.result.legacy_text:
                        raise ValueError("Meaningful note mutation was lost or changed")
                    if new.result.links is None or new.input_sha256 != digest(variant):
                        raise ValueError("Derived source identity/qualification was lost")
                    derived.append(
                        {
                            "source_sha256": identity,
                            "variant_sha256": digest(variant),
                            "injected": mutation,
                            "text_sha256": digest(old.encode()),
                        }
                    )
    if reads.changed_inputs():
        raise ValueError("Corpus changed during qualification")
    if len(derived) != len(MUTATIONS):
        raise ValueError("No natural note could support every semantic control")
    report = {
        "scope": "Independent raw PLC/code-unit checks; CFSpec/legal labels unknown",
        "driver_sha256": driver_sha,
        "producer_sources": producer_sources,
        "baseline_source_sha256": BASELINE_SHA,
        "manifest_sha256": digest(read_artifact(manifest_path)),
        "unique_inputs": len(observed),
        "verified_raw_links": verified,
        "input_paths_verified": len(reads.paths),
        "changed_inputs": [],
        "baseline_texts": observed,
        "meaningful_derivatives": derived,
        "physical_packages": packages,
        "default_note_writer": default_note_writer,
    }
    write_json(output, report)
    return {
        "unique_inputs": len(observed),
        "verified_raw_links": verified,
        "derivatives": len(derived),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--physical-binding", action="store_true")
    parser.add_argument("--default-note-writer", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            qualify(
                args.corpus,
                args.manifest,
                args.baseline,
                args.output,
                physical_binding=args.physical_binding,
                default_note_writer=args.default_note_writer,
            )
        )
    )


if __name__ == "__main__":
    main()
