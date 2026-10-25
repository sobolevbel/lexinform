import gzip
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfWriter
from tools.corpus_artifacts import digest, write_json
from tools.corpus_review import form, record, valid_answer
from tools.corpus_runs import execute, finish

from lexinform.models import PdfPageText, PdfTextExtraction

HOLDOUT = "rpw:RPW/3/2026"
QUESTIONS = (
    "independent_initiative",
    "belongs_to",
    "role",
    "required_annexes",
    "draft_date",
    "scan_norm_pages",
)


def blank_pdf(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def sealed(
    directory: Path, suite: str, report: dict[str, Any], files: dict[str, bytes] | None = None
) -> tuple[Path, str]:
    directory.mkdir(parents=True)
    for name, data in (files or {}).items():
        (directory / name).parent.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(data)
    write_json(directory / "result.json", {"status": "recorded", **report})
    write_json(directory / "started.json", {})
    finish(directory, suite, returncode=0, started={})
    return directory, digest((directory / "manifest.json").read_bytes())


def holdout_queue(tmp_path: Path) -> tuple[Path, Path, str, str]:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    original = tmp_path / "source/downloads/new.pdf"
    original.parent.mkdir(parents=True)
    original.write_bytes(blank_pdf(2))
    sha = digest(original.read_bytes())
    case = {
        "id": HOLDOUT,
        "title": "Poselski projekt ustawy",
        "group": f"doc:{sha}",
        "questions": dict.fromkeys(QUESTIONS, "unknown"),
        "originals": [{"name": "new.pdf", "sha256": sha, "local_uri": original.as_uri()}],
        "split": "holdout_pending",
        "used_for_tuning": False,
    }
    queue, pin = sealed(tmp_path / "runs/holdout", "holdout", {"cases": [case]})
    return corpus, queue, pin, sha


def answers(
    path: Path, pin: str, cases: dict[str, Any], reviewer: str = "Anna", kind: str = "human"
) -> tuple[Path, str]:
    write_json(
        path,
        {
            "version": 1,
            "queue_manifest_sha256": pin,
            "reviewer": reviewer,
            "reviewer_kind": kind,
            "reviewed_at": "2026-10-27",
            "cases": cases,
        },
    )
    return path, digest(path.read_bytes())


def page_review(sha: str, **labels: Any) -> dict[str, Any]:
    return {
        "read_ranges": [{"original_sha256": sha, "pages": [1, 2]}],
        "answers": labels,
        "evidence": [
            {
                "original_sha256": sha,
                "page": 1,
                "quote": "Poselski projekt ustawy",
                "transcription": "visual",
                "supports": sorted(labels),
            }
        ],
    }


def page_texts(monkeypatch: pytest.MonkeyPatch, *texts: str) -> None:
    def extract(self: object, data: bytes) -> PdfTextExtraction:
        return PdfTextExtraction(
            input_sha256=digest(data),
            pypdf_version="test",
            pages=tuple(PdfPageText(number=n, text=text) for n, text in enumerate(texts, 1)),
        )

    monkeypatch.setattr("tools.corpus_review.PypdfTextExtractor.extract_pages", extract)


def test_confirmed_independent_initiative_with_page_evidence_becomes_human_holdout(
    tmp_path: Path,
) -> None:
    corpus, queue, pin, sha = holdout_queue(tmp_path)
    review = page_review(sha, independent_initiative="yes", role="bill")
    target = tmp_path / "record/result.json"
    target.parent.mkdir()

    record(corpus, queue, pin, [answers(tmp_path / "a.json", pin, {HOLDOUT: review})], target)

    result = json.loads(target.read_bytes())
    case = result["cases"][0]
    assert case["status"] == "reviewed" and case["split"] == "holdout"
    assert case["oracle"] == "human_review"
    assert case["labels"]["role"] == "bill" and case["labels"]["draft_date"] == "unknown"
    assert case["reviews"][0]["read_scope"] == ["original_pages"]
    assert result["counts"]["split"] == {"holdout": 1}
    with pytest.raises(ValueError, match="new"):
        record(corpus, queue, pin, [answers(tmp_path / "b.json", pin, {HOLDOUT: review})], target)


def test_unsupported_unread_or_absent_evidence_rejects_the_whole_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page_texts(monkeypatch, "Uzasadnienie", "")
    corpus, queue, pin, sha = holdout_queue(tmp_path)
    review = page_review(sha, independent_initiative="yes", role="bill")
    review["read_ranges"] = [{"original_sha256": sha, "pages": [2]}]
    review["answers"]["draft_date"] = "2026-10-01"
    layer = page_review(sha, role="treaty")
    layer["evidence"][0] |= {"transcription": "text_layer", "quote": "Ustawa", "page": 2}
    submission = answers(
        tmp_path / "a.json",
        pin,
        {HOLDOUT: review, "rpw:RPW/9/2026": layer},
    )
    target = tmp_path / "result.json"

    with pytest.raises(ValueError) as error:
        record(corpus, queue, pin, [submission], target)

    message = str(error.value)
    assert "evidence page was not declared as read" in message
    assert "draft_date answer has no supporting evidence" in message
    assert "not in the pinned queue" in message
    assert not target.exists()
    second = answers(tmp_path / "b.json", pin, {HOLDOUT: layer}, reviewer="Ola")
    with pytest.raises(ValueError) as error:
        record(corpus, queue, pin, [second], target)
    assert "invalid role answer 'treaty'" in str(error.value)
    assert "quote is not in the page text layer" in str(error.value)


def test_human_disagreement_stays_conflict_and_agent_reading_consumes_the_holdout(
    tmp_path: Path,
) -> None:
    corpus, queue, pin, sha = holdout_queue(tmp_path)
    first = answers(
        tmp_path / "a.json", pin, {HOLDOUT: page_review(sha, independent_initiative="yes")}
    )
    second = answers(
        tmp_path / "b.json",
        pin,
        {HOLDOUT: page_review(sha, independent_initiative="no")},
        reviewer="Ola",
    )
    agent = answers(
        tmp_path / "c.json",
        pin,
        {HOLDOUT: page_review(sha, independent_initiative="yes", role="annex")},
        reviewer="Codex",
        kind="agent",
    )

    record(corpus, queue, pin, [first, second], tmp_path / "conflict.json")
    record(corpus, queue, pin, [first, agent], tmp_path / "agent.json")

    conflict = json.loads((tmp_path / "conflict.json").read_bytes())["cases"][0]
    assert conflict["status"] == "conflict" and conflict["split"] == "holdout_pending"
    assert conflict["labels"]["independent_initiative"] == {"conflict": ["yes", "no"]}
    consumed = json.loads((tmp_path / "agent.json").read_bytes())["cases"][0]
    assert consumed["split"] == "regression" and consumed["labels"]["role"] == "unknown"
    assert [row["reviewer_kind"] for row in consumed["reviews"]] == ["human", "agent"]
    with pytest.raises(ValueError, match="another queue"):
        record(
            corpus,
            queue,
            pin,
            [answers(tmp_path / "d.json", "f" * 64, {HOLDOUT: {}})],
            tmp_path / "other.json",
        )
    undated = tmp_path / "e.json"
    write_json(
        undated,
        {"version": 1, "queue_manifest_sha256": pin, "reviewer": "Anna"}
        | {
            "reviewer_kind": "human",
            "cases": {HOLDOUT: {}},
        },
    )
    with pytest.raises(ValueError, match="ISO review date"):
        record(corpus, queue, pin, [(undated, digest(undated.read_bytes()))], tmp_path / "x.json")


def test_text_evidence_is_located_once_inside_the_excerpt_the_reviewer_read(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    text = "USTAWA z dnia 1 marca. Art. 1. Termin 30 dni. Art. 2. Termin 30 dni."
    source = tmp_path / "derived/text.gz"
    source.parent.mkdir()
    source.write_bytes(gzip.compress(text.encode()))
    sha = digest(text.encode())
    case = {
        "id": "doc:1",
        "name": "projekt.docx",
        "group": "rcl:1",
        "questions": {"role": "unknown", "draft_date": "unknown"},
        "text_sha256": sha,
        "text_source": str(source),
        "read_ranges": [{"start": 0, "end": 30, "text": text[:30]}],
        "used_for_tuning": True,
    }
    queue, pin = sealed(tmp_path / "runs/annotations", "annotations", {"cases": [case]})
    excerpt = {"read_ranges": [{"text_sha256": sha, "start": 0, "end": 30}]}
    good = excerpt | {
        "answers": {"role": "bill"},
        "evidence": [{"text_sha256": sha, "quote": "USTAWA", "supports": ["role"]}],
    }
    target = tmp_path / "record.json"

    record(corpus, queue, pin, [answers(tmp_path / "a.json", pin, {"doc:1": good})], target)

    case_result = json.loads(target.read_bytes())["cases"][0]
    assert case_result["reviews"][0]["evidence"][0] == {
        "text_sha256": sha,
        "start": 0,
        "end": 6,
        "quote": "USTAWA",
        "supports": ["role"],
    }
    assert case_result["split"] == "regression"
    ambiguous = excerpt | {
        "answers": {"role": "bill"},
        "evidence": [{"text_sha256": sha, "quote": "Termin 30 dni", "supports": ["role"]}],
    }
    start = text.index("Art. 2")
    outside = excerpt | {
        "answers": {"role": "bill"},
        "evidence": [
            {
                "text_sha256": sha,
                "quote": "Art. 2",
                "start": start,
                "end": start + 6,
                "supports": ["role"],
            }
        ],
    }
    with pytest.raises(ValueError) as error:
        record(
            corpus,
            queue,
            pin,
            [
                answers(tmp_path / "b.json", pin, {"doc:1": ambiguous}),
                answers(tmp_path / "c.json", pin, {"doc:1": outside}, reviewer="Ola"),
            ],
            tmp_path / "bad.json",
        )
    assert "quote occurs 2 times" in str(error.value)
    assert "outside the declared read range" in str(error.value)


def test_form_unpacks_pinned_archive_members_and_escapes_source_names(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    (corpus / "documents/files").mkdir(parents=True)
    member = blank_pdf(3)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as package:
        package.writestr("</script><script>alert(1)</script>.pdf", member)
    raw = corpus / "documents/files/7.zip"
    raw.write_bytes(stream.getvalue())
    row = {
        "raw_path": "documents/files/7.zip",
        "raw_sha256": digest(raw.read_bytes()),
        "name": "pakiet.zip",
        "group": "rcl:7",
        "leaves": [
            {
                "members": ["</script><script>alert(1)</script>.pdf"],
                "sha256": digest(member),
                "format": "pdf",
                "pages": [{"number": n} for n in (1, 2, 3)],
                "preview": f"result/{digest(member)}.png",
            },
            {"members": ["limit.zip"], "state": "limit"},
        ],
    }
    image = b"\x89PNG first page"
    queue, pin = sealed(
        tmp_path / "runs/empty",
        "empty-review",
        {"records": {"7": row}},
        {f"result/{digest(member)}.png": image},
    )
    target = tmp_path / "form/result.json"
    target.parent.mkdir()

    form(corpus, queue, pin, target)

    result = json.loads(target.read_bytes())
    original = result["cases"][0]["originals"][0]
    assert original["pages"] == 3 and original["link"] == f"result/{digest(member)}.pdf"
    assert (target.parent / original["link"]).read_bytes() == member
    assert (target.parent / original["preview"]).read_bytes() == image
    assert result["cases"][0]["holdout_eligible"] is False
    page = target.with_suffix(".html").read_text()
    assert "<script>alert(1)</script>" not in page
    raw.write_bytes(b"changed")
    with pytest.raises(ValueError, match="container changed"):
        form(corpus, queue, pin, tmp_path / "other.json")


@pytest.mark.parametrize(
    "question,value,valid",
    [
        ("belongs_to", "rpw:RPW/35346/2026", True),
        ("belongs_to", "the minister", False),
        ("required_annexes", ["Załącznik nr 1"], True),
        ("required_annexes", [], False),
        ("scan_norm_pages", [3, 4], True),
        ("scan_norm_pages", [0], False),
        ("draft_date", "2026-02-30", False),
        ("independent_initiative", "maybe", False),
    ],
)
def test_answers_use_a_closed_vocabulary_and_unknown_is_always_allowed(
    question: str, value: Any, valid: bool
) -> None:
    assert valid_answer(question, value) is valid
    assert valid_answer(question, "unknown")


def test_review_mode_preset_cannot_be_overridden(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="controlled"):
        execute(tmp_path / "runs", "bad", "review", tmp_path / "corpus", ["--mode", "form"])
