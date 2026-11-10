"""Freeze offline public preparation behavior before prospective local-clock instrumentation."""

import argparse
import io
import json
from contextlib import ExitStack, closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Never, cast

from pypdf import PdfWriter
from tests.computations import KINDS
from tests.document_review_cases import context as technical_review_context
from tests.fakes import FakePublisher, FakeSejmGateway, make_analysis
from tests.harness import summary

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.llm_openai import OpenAiAnalyzer
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    AmendmentRevisionTarget,
    AnalysisRecord,
    BatchIntent,
    BatchRequest,
    Bill,
    BillStatus,
    ChangeContext,
    DeliveryPlan,
    LocalTimingWitness,
    LocatedText,
    Publication,
    PublicationKind,
    PublicationStatus,
    ReviewContext,
    ReviewSource,
    Stage,
    StatusChange,
    TextDocument,
    TriageContext,
)
from lexinform.models.batch import BatchKind
from lexinform.models.reviews import ReviewPurpose
from lexinform.sections import TextBudget
from lexinform.services.analysis import AnalysisOptions, AnalysisService
from lexinform.services.document_reviews import DocumentReviews
from lexinform.services.documents import TextLoader
from lexinform.services.posting import Poster
from lexinform.services.sources import MetadataOnlySource
from lexinform.services.tracking_result import TrackingResult
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runtime import CorpusReads, source_fingerprints
from tools.replay_supplement_inputs import PreparationOnly

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)


class EventClock:
    def __init__(self, sequenced: bool) -> None:
        self.current = NOW
        self.sequenced = sequenced
        self.events: list[dict[str, str]] = []

    def now(self) -> datetime:
        return self.current

    def completed(self, event: str) -> None:
        if self.sequenced:
            self.current += timedelta(seconds=1)
        self.events.append({"event": event, "at": self.current.isoformat()})


class RefusingAnalyzer(OpenAiAnalyzer):
    synchronous_attempts = 0

    def triage(self, ctx: TriageContext) -> Never:
        self.synchronous_attempts += 1
        raise AssertionError("Offline batch preparation refuses synchronous triage")

    def review_change(self, ctx: ChangeContext) -> Never:
        self.synchronous_attempts += 1
        raise AssertionError("Offline batch preparation refuses synchronous change review")


class RefusingPreparation(PreparationOnly):
    def __init__(self, clock: EventClock, model: str) -> None:
        super().__init__()
        self.client_attempts = 0
        self.submit_attempts = 0
        self.returned: list[BatchRequest] = []
        self.analyzer = RefusingAnalyzer(self.refuse, model=model, clock=clock.now)
        self.backend = self.analyzer

    def refuse(self) -> Never:
        self.client_attempts += 1
        raise AssertionError("Offline preparation attempted a provider/model client")

    def submit(self, requests: object) -> Never:
        self.submit_attempts += 1
        super().submit(requests)

    def prepare_request(self, request: BatchRequest) -> BatchRequest:
        result = super().prepare_request(request)
        self.returned.append(result)
        return result


class PinnedSource(MetadataOnlySource):
    def __init__(self, document: TextDocument, sources: tuple[ReviewSource, ...]) -> None:
        self.document = document
        self.sources = sources

    def locate(self, bill: Bill) -> LocatedText:
        return LocatedText(document=self.document)

    def supporting_documents(self, bill: Bill, purpose: ReviewPurpose) -> tuple[ReviewSource, ...]:
        return self.sources


def blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def enroll(repo: SqliteBillRepository, owner: str, title: str, *, first: bool) -> Bill:
    repo.upsert_summary(summary(owner, title), now=NOW)
    if not first:
        repo.save_analysis(
            10,
            owner,
            AnalysisRecord(
                analysis=make_analysis(),
                model="controlled-prior",
                prompt_version="technical-fixture",
                input_chars=0,
                truncated=False,
                text_source="metadata_only",
                created_at=NOW,
            ),
        )
    repo.set_status(10, owner, BillStatus.ANALYSIS_PENDING if first else BillStatus.ANALYZED)
    bill = repo.get(10, owner)
    if bill is None:
        raise ValueError("Controlled preparation owner was not enrolled")
    return bill


def packet(intent: BatchIntent, *, fresh_origin: bool = True) -> dict[str, Any]:
    actual = intent.request.payload_sha256
    if intent.meta.request_sha256 not in (None, actual):
        raise ValueError("Stored request binding differs from its frozen payload")
    meta = (
        intent.meta.model_copy(update={"request_sha256": actual}) if fresh_origin else intent.meta
    )
    return {
        "custom_id": intent.request.custom_id,
        "purpose": intent.request.call_kind,
        "request_sha256": digest(intent.request.model_dump_json().encode()),
        "payload_sha256": intent.request.payload_sha256,
        "context_sha256": digest(intent.request.ctx.model_dump_json().encode()),
        "meta_sha256": digest(meta.model_dump_json(exclude={"local_timing_witness"}).encode()),
        "memo_key": intent.meta.memo_key,
        "input_scope": intent.meta.input_binding.model_dump(mode="json")
        if intent.meta.input_binding
        else None,
        "state": intent.state,
        "created_at": intent.created_at.isoformat(),
        "estimated_cost_usd": intent.request.estimated_cost_usd,
    }


def snapshot(
    plan: dict[str, Any], bodies: dict[str, bytes], case: dict[str, str]
) -> dict[str, Any]:
    kind, mode = cast(BatchKind, case["purpose"]), case["mode"]
    if kind not in KINDS:
        raise ValueError("Unknown planned purpose")
    key = (
        "supplement"
        if kind == "supplement"
        else "amendments"
        if kind in ("amendments", "commission_review")
        else "primary"
    )
    asset = plan["assets"][key]
    clock = EventClock(case["clock"] == "sequenced")
    backend = RefusingPreparation(clock, plan["options"]["model"])
    document = TextDocument(url=asset["url"], kind=asset["kind"])
    available = {value["url"]: bodies[name] for name, value in plan["assets"].items()}
    if mode == "scan":
        document = TextDocument(url="https://api.sejm.gov.pl/technical-blank.pdf", kind="print")
        available[document.url] = blank_pdf()
    downloads: list[dict[str, Any]] = []

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        downloads.append({"url_sha256": digest(url.encode()), "bound": max_bytes})
        if mode == "failed_read":
            raise OSError("Synthetic planned read failure")
        raw = available[url]
        if max_bytes is not None and len(raw) > max_bytes:
            raise ValueError("Pinned preparation body exceeds its bound")
        clock.completed("download_complete")
        downloads[-1].update(sha256=digest(raw), bytes=len(raw))
        return raw

    reader = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())
    loader = TextLoader({"api.sejm.gov.pl": download}, reader, max_bytes=8 * 1024 * 1024)
    source = ReviewSource(
        identity="pinned-document",
        document=document,
        role="commission_report" if kind == "commission_review" else "project_text",
    )
    sources = (
        (source, source.model_copy(update={"identity": "technical-second-reference"}))
        if mode == "multipart"
        else (source,)
    )
    text_source = PinnedSource(document, sources)
    publisher = FakePublisher()
    with ExitStack() as stack:
        repo = stack.enter_context(closing(SqliteBillRepository(Path(":memory:"))))
        repo.migrate()
        bill = enroll(repo, asset["owner"], asset["title"], first=kind == "analysis")
        other = enroll(repo, "technical-joint-partner", "Controlled retained partner", first=False)

        def service_for(store: SqliteBillRepository) -> AnalysisService:
            service = AnalysisService(
                store,
                text_source,
                loader,
                backend.analyzer,
                clock,
                AnalysisOptions(
                    require_batch=True,
                    batch_provider="openai",
                    batch_kinds=frozenset(KINDS),
                    batch_models={purpose: plan["options"]["model"] for purpose in KINDS},
                    triage_min_chars=plan["options"]["triage_min_chars"],
                    consultation_reviews_enabled=True,
                    commission_reviews_enabled=True,
                ),
                text_budget=TextBudget(plan["options"]["budget_chars"]),
                batch=backend,
            )
            service.start_run(dry_run=kind == "explanation")
            return service

        def reviews_for(analysis: AnalysisService) -> DocumentReviews:
            return DocumentReviews(
                repo,
                text_source,
                analysis,
                Poster(
                    repo,
                    publisher,
                    clock,
                    gateway=FakeSejmGateway(),
                    channel_id="technical",
                    max_attempts=3,
                ),
                clock,
                channel_id="technical",
                max_attempts=3,
            )

        service = service_for(repo)
        context = None
        if mode == "cache":
            loader.read_document(document)
        if mode == "passed_context":
            built = service.amendments_context(bill, document)
            context = (
                type(built).model_validate_json(built.model_dump_json())
                if built is not None
                else None
            )
        review_context = None
        if mode == "resume":
            reviews_for(service).request(bill, cast(ReviewPurpose, kind), TrackingResult())
            built_review = service.review_context(bill, cast(ReviewPurpose, kind), sources)
            saved_owner = repo.get(10, bill.number)
            if built_review is None or saved_owner is None or not saved_owner.review_work:
                raise ValueError("The planned persisted review context was not constructed")
            review_context = ReviewContext.model_validate_json(built_review.model_dump_json())
            work = saved_owner.review_work[0].model_copy(
                update={
                    "context": review_context,
                    "memo_key": review_context.memo_key(10),
                    "input_bill_json": "",
                }
            )
            repo.save_review_work(10, bill.number, (work,))
        target = None
        if mode == "correction":
            stage = Stage.model_validate(
                asset.get(
                    "stage",
                    {"stage_type": "CommitteeReport", "stage_name": "Technical committee control"},
                )
            )
            change = StatusChange(
                term=10,
                number=bill.number,
                old_fingerprint=None,
                new_fingerprint="technical-correction-target",
                new_stages=[stage],
                detected_at=NOW,
            )
            change_id = repo.add_status_change(change)
            if change_id is None:
                raise ValueError("Controlled correction event was not retained")
            delivery = DeliveryPlan(
                bill_json=bill.model_dump_json(),
                change_json=change.model_copy(update={"id": change_id}).model_dump_json(),
                today=NOW.date(),
                edit_message_id=17,
            )
            identity = repo.create_publication(
                Publication(
                    term=10,
                    number=bill.number,
                    kind=PublicationKind.STATUS_UPDATE,
                    status=PublicationStatus.SENT,
                    channel_id="technical",
                    message_id=17,
                    status_change_id=change_id,
                    created_at=NOW,
                    sent_at=NOW,
                    delivery=delivery,
                )
            )
            target = AmendmentRevisionTarget(
                publication_id=identity,
                channel_id="technical",
                source_url=document.url,
                delivery=delivery,
            )
        loader.reset_read_metrics()
        initial_downloads = len(downloads)
        stages: list[dict[str, Any]] = []
        timing: list[dict[str, Any]] = []
        initial_packets: dict[str, str] = {}

        def invoke() -> object:
            if kind == "analysis":
                return service.analyze_pending(limit=1)
            if kind == "reanalysis":
                return service.prepare_reanalysis(bill, document)
            if kind == "explanation":
                return service.explain_bill(bill)
            if kind == "amendments":
                if target is not None:
                    return service.revise_amendments(bill, document, target)
                return service.summarize_amendments(bill, document, context=context, may_wait=True)
            if kind == "supplement":
                return service.digest_supplement(
                    bill,
                    document,
                    number="1319-001",
                    title=asset.get("document_title", "Controlled OSR"),
                )
            if kind == "joint":
                return service.compare_joint(bill, [other], may_wait=True)
            if kind == "name":
                return service.name_bill(bill, may_wait=True)
            if kind == "act_application":
                technical = technical_review_context(role="final_act").model_copy(
                    update={"number": bill.number}
                )
                return service.request_document_review(bill, technical)
            reviews = reviews_for(service)
            result = TrackingResult()
            reviews.request(bill, cast(ReviewPurpose, kind), result)
            reviews.advance(result, publish=False)
            return result

        for phase in ("initial", "repeat", "reconstructed_service"):
            if phase == "reconstructed_service":
                service = service_for(repo)
            before_prepares = len(backend.returned)
            before_downloads = len(downloads)
            before_cache_hits = loader.read_metrics().cache_hits
            error = None
            result_type = None
            try:
                result_type = type(invoke()).__name__
            except Exception as exc:
                error = type(exc).__name__
            queued = repo.list_queued_batch_intents()
            for intent in queued:
                if phase == "initial":
                    initial_packets[intent.request.custom_id] = intent.model_dump_json()
                elif (
                    intent.request.custom_id in initial_packets
                    and intent.model_dump_json() != initial_packets[intent.request.custom_id]
                ):
                    raise ValueError(
                        "An existing intent timing/None or frozen input changed on reuse"
                    )
            current = repo.get(10, bill.number)
            stages.append(
                {
                    "phase": phase,
                    "result_type": result_type,
                    "exception_type": error,
                    "prepare_calls": len(backend.returned) - before_prepares,
                    "packets": [packet(value) for value in queued],
                    "status": current.status if current else None,
                    "analysis_attempts": current.analysis_attempts if current else None,
                    "spent_usd": service.spent_usd,
                    "calls": [value.model_dump(mode="json") for value in service.calls],
                    "read_metrics": loader.read_metrics().model_dump(mode="json"),
                }
            )
            new_downloads = any("sha256" in value for value in downloads[before_downloads:])
            new_cache_hits = loader.read_metrics().cache_hits > before_cache_hits
            expected_read = (
                "mixed"
                if new_downloads and new_cache_hits
                else "fresh"
                if new_downloads
                else "cache_reuse"
                if new_cache_hits
                else None
            )
            values = []
            for intent in queued:
                witness = intent.meta.local_timing_witness
                if witness is not None:
                    if (
                        kind in ("name", "joint", "act_application")
                        or mode in ("passed_context", "resume")
                    ) and witness.read_completed_at is not None:
                        raise ValueError(
                            "A persisted/descriptive context acquired a guessed read clock"
                        )
                    if phase == "initial" and witness.read_kind != expected_read:
                        raise ValueError(
                            "Timing read class differs from actual invocation IO/cache evidence"
                        )
                    witness.require_request(kind, intent.request.payload_sha256)
                values.append(witness.model_dump(mode="json") if witness else None)
            timing.append(
                {
                    "phase": phase,
                    "witnesses": values,
                    "initial_expected_read_class": expected_read if phase == "initial" else None,
                    "stored_request_sha256": [value.meta.request_sha256 for value in queued],
                }
            )
        if mode == "existing_witness":
            original = repo.list_queued_batch_intents()[0]
            for seed_kind in ("none", "witness"):
                store = stack.enter_context(closing(SqliteBillRepository(Path(":memory:"))))
                store.migrate()
                enroll(store, bill.number, bill.summary.title, first=False)
                raw = original.model_dump(mode="json")
                raw["meta"].pop("local_timing_witness", None)
                raw["meta"].pop("request_sha256", None)
                if seed_kind == "witness":
                    binding = original.meta.input_binding
                    if binding is None or original.request.payload_sha256 is None:
                        raise ValueError("Prepared seed lacks its exact immutable input binding")
                    raw["meta"].update(
                        request_sha256=original.request.payload_sha256,
                        local_timing_witness=LocalTimingWitness(
                            purpose=kind,
                            request_sha256=original.request.payload_sha256,
                            input_scope=binding,
                            trigger_kind="work_requested",
                            triggered_at=NOW - timedelta(minutes=5),
                        ).model_dump(mode="json"),
                    )
                seeded = BatchIntent.model_validate(raw)
                store.save_batch_intent(seeded)
                frozen = store.list_queued_batch_intents()[0].model_dump_json()
                reused = service_for(store)
                before_count = len(backend.returned)
                loaded = store.get(10, bill.number)
                if loaded is None:
                    raise ValueError("Seeded controlled owner disappeared")
                reused.name_bill(loaded, may_wait=True)
                if store.list_queued_batch_intents()[0].model_dump_json() != frozen:
                    raise ValueError("Existing witness/None was rewritten")
                stages.append(
                    {
                        "phase": "preexisting_" + seed_kind,
                        "prepare_calls": len(backend.returned) - before_count,
                        "stored_packet_exact": True,
                        "seeded_packet": packet(seeded, fresh_origin=False),
                        "stored_packet_full_sha256": digest(frozen.encode()),
                    }
                )
        dump = repo.dump()
        repo.restore(dump)
        if repo.dump() != dump:
            raise ValueError("Offline fixture restore changed retained inputs")
        if (
            backend.client_attempts
            or backend.submit_attempts
            or backend.analyzer.synchronous_attempts
            or publisher.sent
            or publisher.edited
        ):
            raise ValueError("Offline preparation crossed a refused provider/delivery boundary")
        return {
            "core": {
                "stages": stages,
                "downloads": downloads,
                "initial_downloads": initial_downloads,
                "prepared": [
                    digest(value.model_dump_json().encode()) for value in backend.returned
                ],
                "client_attempts": backend.client_attempts,
                "synchronous_attempts": backend.analyzer.synchronous_attempts,
                "submit_attempts": backend.submit_attempts,
                "packet_immutability": True,
                "restore_exact": True,
                "clock_events": clock.events,
            },
            "timing": timing,
            "case": case,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--require-timing", action="store_true")
    args = parser.parse_args()
    plan_raw = read_artifact(args.plan)
    plan = json.loads(plan_raw)
    pins = source_fingerprints()
    reads = CorpusReads(Path("/Users/sobaleu"))
    reads.allowed += tuple(Path(name).resolve() for name in plan.get("tokenizer_resources", {}))
    with reads.guard():
        for name, sha in plan["read_inputs"].items():
            if digest(Path(name).read_bytes()) != sha:
                raise ValueError("Planned metadata pin changed")
        bodies = {}
        for key, row in plan["assets"].items():
            raw = Path(row["path"]).read_bytes()
            if len(raw) != row["bytes"] or digest(raw) != row["sha256"]:
                raise ValueError("Planned body pin changed")
            bodies[key] = raw
        results = {}
        for case in plan["cases"]:
            try:
                results[case["id"]] = snapshot(plan, bodies, case)
            except Exception as exc:
                results[case["id"]] = {
                    "case": case,
                    "core": {"qualification_error": type(exc).__name__},
                }
    if any(digest(Path(name).read_bytes()) != sha for name, sha in plan["read_inputs"].items()):
        raise ValueError("A pinned metadata/tokenizer resource changed")
    if reads.changed_inputs() or source_fingerprints() != pins:
        raise ValueError("Offline input/source changed")
    if read_artifact(args.plan) != plan_raw:
        raise ValueError("The frozen case plan changed during replay")
    report = {
        "kind": "local-timing-public-preparation-v1",
        "plan_sha256": digest(plan_raw),
        "driver_sha256": digest(Path(__file__).read_bytes()),
        "sources": pins,
        "input_sha256": reads.initial_sha256,
        "planned_cases": len(plan["cases"]),
        "results": results,
        "changed_inputs": reads.changed_inputs(),
    }
    prepared_purposes = {
        row["purpose"]
        for value in results.values()
        for stage in value["core"].get("stages", [])
        for row in stage.get("packets", [])
    }
    unexpected = [
        key
        for key, value in results.items()
        if value["core"].get("qualification_error")
        or any(stage.get("exception_type") for stage in value["core"].get("stages", []))
    ]
    report["summary"] = {
        "prepared_purposes": sorted(prepared_purposes),
        "missing_purposes": sorted(set(KINDS) - prepared_purposes),
        "unexpected_failed_cases": unexpected,
        "planned_no_preparation": [
            case["id"] for case in plan["cases"] if case["mode"] == "failed_read"
        ],
    }
    report["status"] = "failed" if unexpected or set(KINDS) - prepared_purposes else "passed"
    if args.require_timing:
        for value in results.values():
            first = value["core"].get("stages", [{}])[0]
            witnesses = value.get("timing", [{}])[0].get("witnesses", [])
            if first.get("packets") and (not witnesses or any(item is None for item in witnesses)):
                raise ValueError("A newly prepared input lacks its required timing witness")
    report["timing_required"] = args.require_timing
    if args.baseline:
        previous = json.loads(read_artifact(args.baseline))
        if (
            previous["plan_sha256"] != report["plan_sha256"]
            or previous["driver_sha256"] != report["driver_sha256"]
        ):
            raise ValueError("Paired recipe/driver differs")
        if {k: v["core"] for k, v in previous["results"].items()} != {
            k: v["core"] for k, v in results.items()
        }:
            raise ValueError("Public request/context/IO/accounting core changed")
        report["paired_core_exact"] = True
    write_json(args.output, report)
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
