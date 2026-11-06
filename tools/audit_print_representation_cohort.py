"""Measure the exact original Sejm PDF/DOCX representation profile over a pinned listing."""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

from lexinform.adapters.document_text import DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints
from tools.replay_print_representation import (
    _MAX_BYTES,
    _PRINTS,
    RepresentationCase,
    official_original,
    prove,
)
from tools.replay_proposal_edition import PinnedInput


def audit(
    corpus: Path,
    official_prints: PinnedInput,
    term: int,
    *,
    requested_prints: tuple[str, ...] = (),
    progress_every: int = 0,
) -> dict[str, object]:
    root = corpus.resolve(strict=True)
    reads, before = CorpusReads(root), source_fingerprints()
    outcomes: list[dict[str, object]] = []
    pdf_reader, word_reader = PypdfTextExtractor(), DocxTextExtractor()
    with reads.guard():
        listing = _PRINTS.validate_json(official_prints.read(root))
        numbers = sorted(
            {
                number
                for item in listing
                if re.fullmatch(r"[1-9][0-9]{0,8}", number := str(item.get("number", "")))
            },
            key=int,
        )
        requested = set(requested_prints)
        if requested - set(numbers):
            raise ValueError("Requested print does not exist in the pinned listing")
        eligible = []
        for number in numbers:
            if requested and number not in requested:
                continue
            try:
                official_original(listing, term=term, number=number)
            except ValueError:
                continue
            eligible.append(number)
        if requested - set(eligible):
            raise ValueError("Requested print is outside the original PDF/DOCX profile")
        folder = root / "sejm" / f"term{term}" / "files"
        for index, number in enumerate(eligible, 1):
            if progress_every and (
                index == 1 or index % progress_every == 0 or index == len(eligible)
            ):
                print(
                    f"Sejm term {term}: starting {index}/{len(eligible)} (print {number})",
                    file=sys.stderr,
                    flush=True,
                )
            pdf_path = folder / f"{number}__{number}.pdf"
            word_path = folder / f"{number}__{number}-ustawa.docx"
            paths = (pdf_path, word_path)
            missing = [str(path.relative_to(root)) for path in paths if not path.is_file()]
            if missing:
                for path in missing:
                    reads.require(root / path)
                outcomes.append(
                    {"number": number, "status": "missing_local_pair", "missing": missing}
                )
                continue
            if any(path.stat().st_size > _MAX_BYTES for path in paths):
                outcomes.append({"number": number, "status": "outside_byte_bound"})
                continue
            pdf, word = (read_artifact(path) for path in paths)
            if not pdf.startswith(b"%PDF-") or not word.startswith(b"PK"):
                outcomes.append({"number": number, "status": "unsupported_local_format"})
                continue
            case = RepresentationCase(
                term=term,
                number=number,
                official_prints=official_prints,
                pdf=PinnedInput(path=str(pdf_path.relative_to(root)), sha256=digest(pdf)),
                docx=PinnedInput(path=str(word_path.relative_to(root)), sha256=digest(word)),
                pdf_text_sha256=digest(pdf_reader.extract_pages(pdf).legacy_text.encode()),
                docx_text_sha256=digest(word_reader.extract_blocks(word).legacy_text.encode()),
            )
            try:
                proof = prove(case, listing, pdf, word)
            except ValueError as exc:
                outcomes.append({"number": number, "status": "unproven", "reason": str(exc)})
            else:
                outcomes.append(
                    {
                        "number": number,
                        "status": "proven",
                        "case": case.model_dump(mode="json"),
                        "proof": proof,
                    }
                )
    if reads.changed_inputs() or before != source_fingerprints():
        raise ValueError("Replay inputs or code changed")
    return {
        "version": 1,
        "term": term,
        "official_prints": official_prints.model_dump(mode="json"),
        "scope": "Exact original single-member normative core; refusals assert no relationship.",
        "numbered_prints": len(numbers),
        "requested_prints": sorted(requested, key=int),
        "eligible_prints": len(outcomes),
        "counts": dict(sorted(Counter(str(row["status"]) for row in outcomes).items())),
        "outcomes": outcomes,
        "read_inputs": reads.initial_sha256,
        "code_version": code_version(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--term", type=int, required=True, choices=range(1, 100), metavar="TERM")
    parser.add_argument("--listing", required=True)
    parser.add_argument("--listing-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--print",
        action="append",
        default=[],
        dest="prints",
        help="limit the report to an explicit print",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=25,
        metavar="N",
        help="log progress to stderr every N prints; 0 disables it",
    )
    args = parser.parse_args()
    try:
        if args.output.resolve().is_relative_to(args.corpus.resolve()):
            raise ValueError("Replay output must be outside the corpus")
        pins = PinnedInput(path=args.listing, sha256=args.listing_sha256)
        if args.progress_every < 0:
            raise ValueError("Progress interval must be nonnegative")
        write_json(
            args.output,
            audit(
                args.corpus,
                pins,
                args.term,
                requested_prints=tuple(args.prints),
                progress_every=args.progress_every,
            ),
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
