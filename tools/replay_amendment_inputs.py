"""Replay pinned committee originals through public preparation without provider calls."""

import argparse
import gzip
import json
from contextlib import closing
from pathlib import Path
from typing import Any

from tests.fakes import FakeLlm, FixedClock, make_analysis
from tests.harness import summary

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.errors import DocumentSelectionError
from lexinform.keywords import KeywordPrefilter
from lexinform.models import (
    AmendmentRevisionTarget,
    AmendmentsQuestion,
    AmendmentsRecord,
    AnalysisRecord,
    BillStatus,
    DeliveryPlan,
    DigestItemMeta,
    Publication,
    PublicationKind,
    PublicationStatus,
    Stage,
    StatusChange,
    TextDocument,
)
from lexinform.sections import TextBudget
from lexinform.services.analysis import AnalysisOptions, AnalysisService, Waiting
from lexinform.services.documents import TextLoader
from lexinform.services.sources import MetadataOnlySource, TextSources
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runs import verify
from tools.corpus_runtime import CorpusReads, source_fingerprints
from tools.replay_supplement_inputs import NOW, PreparationOnly


def snapshot(data: bytes, report: dict[str, Any], *, correction: bool) -> dict[str, Any]:
    observation = report["stage_observations"][0]
    owner, source_url = observation["owner_print"], report["url"]
    stage = Stage.model_validate(observation["stage"])
    downloads: list[str] = []

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        if url != source_url or max_bytes is not None and len(data) > max_bytes:
            raise ValueError("Replay attempted an unexpected or oversized download")
        downloads.append(digest(url.encode()))
        return data

    reader = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())
    loader = TextLoader({"api.sejm.gov.pl": download}, reader, max_bytes=8 * 1024 * 1024)
    document = TextDocument(url=source_url, kind="committee_amendments")
    with closing(SqliteBillRepository(Path(":memory:"))) as repo:
        repo.migrate()
        repo.upsert_summary(summary(owner, "Offline frozen prior context"), now=NOW)
        repo.save_analysis(
            10,
            owner,
            AnalysisRecord(
                analysis=make_analysis(),
                model="prior",
                prompt_version="prior",
                input_chars=1,
                truncated=False,
                text_source="pdf",
                created_at=NOW,
            ),
        )
        repo.set_status(10, owner, BillStatus.ANALYZED)
        bill = repo.get(10, owner)
        if bill is None:
            raise ValueError("Replay did not enroll its owner")
        backend = PreparationOnly()
        service = AnalysisService(
            repo,
            TextSources(MetadataOnlySource()),
            loader,
            FakeLlm(),
            FixedClock(NOW),
            AnalysisOptions(
                require_batch=True,
                batch_provider="openai",
                batch_models={"amendments": "gpt-6.1-sol"},
            ),
            text_budget=TextBudget(120_000),
            batch=backend,
        )
        result: AmendmentsRecord | Waiting | Publication | None = None
        error = None
        waiting = False
        try:
            if correction:
                change = StatusChange(
                    term=10,
                    number=owner,
                    old_fingerprint=None,
                    new_fingerprint="offline-amendment",
                    new_stages=[stage],
                    detected_at=NOW,
                )
                change_id = repo.add_status_change(change)
                if change_id is None:
                    raise ValueError("Replay did not retain its controlled event")
                change = change.model_copy(update={"id": change_id})
                plan = DeliveryPlan(
                    bill_json=bill.model_dump_json(),
                    change_json=change.model_dump_json(),
                    today=NOW.date(),
                    edit_message_id=17,
                )
                publication_id = repo.create_publication(
                    Publication(
                        term=10,
                        number=owner,
                        kind=PublicationKind.STATUS_UPDATE,
                        status=PublicationStatus.SENT,
                        channel_id="offline",
                        message_id=17,
                        status_change_id=change_id,
                        created_at=NOW,
                        sent_at=NOW,
                        delivery=plan,
                    )
                )
                result = service.revise_amendments(
                    bill,
                    document,
                    AmendmentRevisionTarget(
                        publication_id=publication_id,
                        channel_id="offline",
                        source_url=source_url,
                        delivery=plan,
                    ),
                    proposal=stage.proposal,
                )
            else:
                result = service.summarize_amendments(
                    bill, document, proposal=stage.proposal, may_wait=True
                )
            waiting = isinstance(result, Waiting)
        except (ValueError, DocumentSelectionError) as exc:
            error = type(exc).__name__
        queued = repo.list_queued_batch_intents()
        core: dict[str, Any] = {
            "source_sha256": digest(data),
            "correction": correction,
            "prepare_count": len(backend.prepared),
            "queued_count": len(queued),
            "waiting": waiting,
            "exception_type": error,
            "downloads": downloads,
            "spent_usd": service.spent_usd,
            "calls": [call.model_dump(mode="json") for call in service.calls],
        }
        receipt = None
        if queued:
            if len(queued) != 1 or len(backend.prepared) != 1:
                raise ValueError("One amendment must prepare exactly one frozen request")
            intent = queued[0]
            if not isinstance(intent.meta, DigestItemMeta) or not isinstance(
                intent.request.question, AmendmentsQuestion
            ):
                raise ValueError("Amendment preparation changed its typed purpose")
            ctx = intent.request.question.ctx
            _, selected = loader.select_document(document)
            text = selected.text or ""
            expected = TextBudget(120_000).apply(text, KeywordPrefilter().spans(text))
            if ctx.text != ("" if ctx.scan is not None else expected.text):
                raise ValueError("Amendment text differs from the actual selected budgeted input")
            core.update(
                context_sha256=digest(ctx.model_dump_json().encode()),
                payload_sha256=intent.request.payload_sha256,
                request_sha256=digest(intent.request.model_dump_json().encode()),
                text_sha256=digest(ctx.text.encode()),
                text_chars=len(ctx.text),
                scan=ctx.scan is not None,
                custom_id=intent.request.custom_id,
                memo_key=intent.meta.memo_key,
                metadata_sha256=digest(
                    intent.meta.model_dump_json(exclude={"prepared_input_receipt"}).encode()
                ),
                selection_sha256=digest(
                    selected.selection.without_text().model_dump_json().encode()
                ),
            )
            receipt = intent.meta.prepared_input_receipt
            if receipt is not None:
                if (
                    receipt.request_sha256 != intent.request.payload_sha256
                    or receipt.text_sha256 != digest(ctx.text.encode())
                ):
                    raise ValueError("Receipt differs from the actual frozen request/text")
                if (
                    not receipt.source_selection_id
                    or not receipt.artifact_sha256
                    or not receipt.extraction_id
                ):
                    raise ValueError("Amendment receipt lacks its corroborated parents")
                for segment in receipt.segments:
                    if (
                        segment.origin == "source"
                        and ctx.text[segment.start : segment.end]
                        != text[segment.source_start : segment.source_end]
                    ):
                        raise ValueError("Receipt range differs from selected normative text")
            repo.restore(repo.dump())
        dump = repo.dump().encode()
        return {
            "core": core,
            "receipt": receipt.model_dump(mode="json") if receipt else None,
            "provenance": {
                "source_observation_sha256": [
                    digest(x.model_dump_json().encode())
                    for x in repo.list_source_observations(10, owner)
                ]
            },
            "storage": {"sql_bytes": len(dump), "gzip_bytes": len(gzip.compress(dump, mtime=0))},
        }


def run(generation: Path, pin: str, output: Path, *, baseline: Path | None = None) -> None:
    if digest(read_artifact(generation / "manifest.json")) != pin:
        raise ValueError("Collection manifest SHA mismatch")
    manifest = verify(generation)
    if (
        manifest["status"] != "recorded"
        or manifest["started"]["kind"] != "committee-report-collection"
    ):
        raise ValueError("Replay requires the recorded committee collection")
    reads = CorpusReads(generation)
    sources = source_fingerprints()
    rows: dict[str, Any] = {}
    with reads.guard():
        plan = json.loads(read_artifact(generation / "plan.json"))
        collected = json.loads(read_artifact(generation / "result.json"))
        for report in plan["reports"]:
            found = collected["observations"][report["id"]]
            if found["state"] != "downloaded":
                rows[report["id"]] = {"exclusion": found["state"]}
                continue
            data = read_artifact(generation / found["path"])
            if digest(data) != found["sha256"] or len(data) != found["bytes"]:
                raise ValueError("Original differs from its collection receipt")
            rows[report["id"]] = {
                mode: snapshot(data, report, correction=mode == "correction")
                for mode in ("ordinary", "correction")
            }
    if reads.changed_inputs() or sources != source_fingerprints():
        raise ValueError("Replay source/code changed")
    comparison = None
    if baseline is not None:
        previous = json.loads(read_artifact(baseline))
        if (
            previous["collection_manifest_sha256"] != pin
            or previous["results"].keys() != rows.keys()
        ):
            raise ValueError("Paired replay has different source cases")

        def cores(row: dict[str, Any]) -> dict[str, Any]:
            return (
                row if "exclusion" in row else {mode: value["core"] for mode, value in row.items()}
            )

        changed = [key for key in rows if cores(rows[key]) != cores(previous["results"][key])]
        comparison = {
            "baseline_sha256": digest(read_artifact(baseline)),
            "changed_core_cases": changed,
        }
    write_json(
        output,
        {
            "collection_manifest_sha256": pin,
            "source_sha256": sources,
            "read_inputs": reads.initial_sha256,
            "results": rows,
            "comparison": comparison,
            "scope": "Controlled prior context/target; natural bytes, no gold labels/model calls",
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    run(args.generation, args.manifest_sha256, args.output, baseline=args.baseline)


if __name__ == "__main__":
    main()
