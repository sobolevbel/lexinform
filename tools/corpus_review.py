"""Offer pinned review queues as fillable forms and record validated reviewer answers."""

import argparse
import datetime as dt
import gzip
import io
import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from lexinform.adapters.pdf_text import PypdfTextExtractor
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_extraction import MAX_BYTES, bounded_reader
from tools.corpus_health import local_path
from tools.corpus_runs import verify
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints

ROLES = (
    "bill",
    "annex",
    "justification",
    "osr",
    "letter",
    "regulation",
    "self_amendment",
    "remarks_table",
    "compliance_table",
    "consultation_report",
    "discrepancies",
    "position",
    "other",
)
QUESTION_ORDER = (
    "independent_initiative",
    "role",
    "belongs_to",
    "required_annexes",
    "draft_date",
    "scan_norm_pages",
    "ocr_pages",
)
EMPTY_REVIEW_QUESTIONS = ("role", "belongs_to", "required_annexes", "draft_date", "ocr_pages")
PAGE_QUESTIONS = ("scan_norm_pages", "ocr_pages")
IDENTITY = re.compile(r"druk:\d+[\w-]*|rpw:RPW/\d+/\d{4}|rcl:\d+|wpl:[A-Z]*\d+|doc:\d+|none")
ENTRY_FIELDS = {"read_ranges", "answers", "evidence", "notes"}


def valid_answer(question: str, value: Any) -> bool:
    if value == "unknown":
        return True
    if question == "independent_initiative":
        return value in ("yes", "no")
    if question == "role":
        return value in ROLES
    if question == "belongs_to":
        return isinstance(value, str) and IDENTITY.fullmatch(value) is not None
    if question == "draft_date":
        try:
            return isinstance(value, str) and dt.date.fromisoformat(value).isoformat() == value
        except ValueError:
            return False
    if question == "required_annexes":
        return value == "none" or (
            isinstance(value, list)
            and bool(value)
            and all(isinstance(item, str) and item.strip() for item in value)
        )
    if question in PAGE_QUESTIONS:
        return value == "none" or (
            isinstance(value, list)
            and bool(value)
            and all(type(item) is int and item >= 1 for item in value)
        )
    return False


def flat(text: str) -> str:
    return " ".join(text.split())


def ordered(questions: Any) -> list[str]:
    return sorted(questions, key=lambda key: (QUESTION_ORDER + (key,)).index(key))


def file_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError("Review original must be a local file")
    return Path(unquote(parsed.path))


def queue_targets(root: Path, queue: Path, pin: str) -> tuple[str, dict[str, Any]]:
    directory = queue.resolve(strict=True)
    if digest(read_artifact(directory / "manifest.json")) != pin:
        raise ValueError("Queue manifest SHA mismatch")
    manifest = verify(directory)
    if manifest["status"] != "recorded":
        raise ValueError("Only a sealed recorded queue can be reviewed")
    report = json.loads(read_artifact(directory / "result.json"))
    targets: dict[str, Any] = {}
    if manifest["suite"] == "holdout":
        for case in report["cases"]:
            targets[case["id"]] = {
                "title": case["title"],
                "group": case["group"],
                "questions": ordered(case["questions"]),
                "holdout_eligible": case["used_for_tuning"] is False
                and case["split"] == "holdout_pending",
                "texts": {},
                "originals": {
                    item["sha256"]: {
                        "label": item["name"],
                        "path": str(file_path(item["local_uri"])),
                        "members": [],
                    }
                    for item in case["originals"]
                },
            }
    elif manifest["suite"] == "empty-review":
        for identity, row in report["records"].items():
            originals: dict[str, Any] = {}
            for leaf in row["leaves"]:
                if "sha256" in leaf and leaf["sha256"] not in originals:
                    originals[leaf["sha256"]] = {
                        "label": " / ".join([row["raw_path"], *leaf["members"]]),
                        "path": str(root / row["raw_path"]),
                        "container_sha256": row["raw_sha256"],
                        "members": leaf["members"],
                        **(
                            {
                                "preview": str(directory / leaf["preview"]),
                                "preview_sha256": manifest["artifacts"][leaf["preview"]]["sha256"],
                            }
                            if leaf.get("preview")
                            else {}
                        ),
                    }
            targets[f"doc:{identity}"] = {
                "title": row["name"] or identity,
                "group": row["group"],
                "questions": list(EMPTY_REVIEW_QUESTIONS),
                "holdout_eligible": False,
                "texts": {},
                "originals": originals,
            }
    elif manifest["suite"] == "annotations":
        for case in report["cases"]:
            targets[case["id"]] = {
                "title": case["name"],
                "group": case["group"],
                "questions": ordered(case["questions"]),
                "holdout_eligible": case["used_for_tuning"] is False,
                "texts": {
                    case["text_sha256"]: {
                        "path": case["text_source"],
                        "shown": case["read_ranges"],
                    }
                },
                "originals": {},
            }
    else:
        raise ValueError("Unsupported review queue suite")
    if not targets:
        raise ValueError("Review queue has no cases")
    return manifest["suite"], targets


class Sources:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.reads = CorpusReads(root)
        self.external: dict[str, str] = {}
        self.views: dict[str, dict[str, Any]] = {}

    def read(self, path: Path) -> bytes:
        resolved = path.resolve(strict=True)
        if resolved.stat().st_size > MAX_BYTES:
            raise ValueError("Review source exceeds byte limit")
        if not resolved.is_relative_to(self.root):
            data = read_artifact(resolved)
            self.external[str(resolved)] = digest(data)
            return data
        with self.reads.guard():
            return local_path(self.root, str(resolved.relative_to(self.root))).read_bytes()

    def original(self, item: dict[str, Any], sha: str) -> bytes:
        data = self.read(Path(item["path"]))
        if "container_sha256" in item and digest(data) != item["container_sha256"]:
            raise ValueError("Review container changed")
        for name in item["members"]:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                info = archive.getinfo(name)
                if info.file_size > MAX_BYTES:
                    raise ValueError("Review member exceeds byte limit")
                with archive.open(info) as stream:
                    data = stream.read(MAX_BYTES + 1)
        if digest(data) != sha:
            raise ValueError("Review original SHA mismatch; archive member names may be ambiguous")
        return data

    def view(self, item: dict[str, Any], sha: str) -> dict[str, Any]:
        if sha not in self.views:
            data = self.original(item, sha)
            if b"%PDF-" in data[:1024]:
                pages = PypdfTextExtractor().extract_pages(data).pages
                self.views[sha] = {"pages": [page.text for page in pages]}
            else:
                self.views[sha] = {"text": bounded_reader().extract(data)}
        return self.views[sha]

    def text(self, item: dict[str, Any], sha: str) -> str:
        data = self.read(Path(item["path"]))
        text = (gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data).decode()
        if digest(text.encode()) != sha:
            raise ValueError("Review text SHA mismatch")
        return text


def read_ranges(
    target: dict[str, Any], ranges: Any, sources: Sources, errors: list[str]
) -> tuple[dict[str, set[int] | None], dict[str, list[tuple[int, int]] | None], list[str]]:
    pages: dict[str, set[int] | None] = {}
    spans: dict[str, list[tuple[int, int]] | None] = {}
    scope: set[str] = set()
    if not isinstance(ranges, list) or not ranges:
        errors.append("review must declare what was read")
        return pages, spans, []
    for item in ranges:
        if not isinstance(item, dict):
            errors.append("read range must be an object")
        elif item.get("original_sha256") in target["originals"]:
            sha = item["original_sha256"]
            view = sources.view(target["originals"][sha], sha)
            numbers = item.get("pages")
            if "text" in view:
                if numbers is not None:
                    errors.append("a non-PDF original is read whole; pages must be null")
                pages[sha] = None
                scope.add("original_file")
            elif (
                isinstance(numbers, list)
                and numbers
                and all(type(page) is int and 1 <= page <= len(view["pages"]) for page in numbers)
            ):
                pages[sha] = (pages.get(sha) or set()) | set(numbers)
                scope.add("original_pages")
            else:
                errors.append(f"read pages must lie within the original's {len(view['pages'])}")
        elif item.get("text_sha256") in target["texts"]:
            sha = item["text_sha256"]
            length = len(sources.text(target["texts"][sha], sha))
            start, end = item.get("start"), item.get("end")
            if start is None and end is None:
                spans[sha] = None
                scope.add("full_text")
            elif type(start) is int and type(end) is int and 0 <= start < end <= length:
                current = spans.get(sha, [])
                if current is not None:
                    spans[sha] = [*current, (start, end)]
                scope.add("excerpt")
            else:
                errors.append("read text range lies outside its source")
        else:
            errors.append("read range names a source outside this case")
    return pages, spans, sorted(scope)


def check_evidence(
    target: dict[str, Any],
    item: Any,
    pages: dict[str, set[int] | None],
    spans: dict[str, list[tuple[int, int]] | None],
    sources: Sources,
) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(item, dict):
        return None, "evidence must be an object"
    supports, quote = item.get("supports"), item.get("quote")
    if not (
        isinstance(supports, list)
        and supports
        and all(isinstance(key, str) for key in supports)
        and set(supports) <= set(target["questions"])
    ):
        return None, "evidence must support known questions"
    if not isinstance(quote, str) or not quote.strip():
        return None, "evidence requires a quote"
    if "text_sha256" in item:
        sha = item["text_sha256"]
        if sha not in spans:
            return None, "evidence text was not declared as read"
        text = sources.text(target["texts"][sha], sha)
        start, end = item.get("start"), item.get("end")
        if start is None and end is None:
            found = [match.start() for match in re.finditer(re.escape(quote), text)]
            if len(found) != 1:
                return None, f"quote occurs {len(found)} times; give start and end"
            start, end = found[0], found[0] + len(quote)
        if not (
            type(start) is int
            and type(end) is int
            and 0 <= start < end <= len(text)
            and text[start:end] == quote
        ):
            return None, "quote differs from its source span"
        allowed = spans[sha]
        if allowed is not None and not any(a <= start and end <= b for a, b in allowed):
            return None, "evidence lies outside the declared read range"
        return {
            "text_sha256": sha,
            "start": start,
            "end": end,
            "quote": quote,
            "supports": sorted(set(supports)),
        }, None
    sha = item.get("original_sha256")
    if sha not in pages:
        return None, "evidence original was not declared as read"
    view = sources.view(target["originals"][sha], sha)
    page, transcription = item.get("page"), item.get("transcription")
    if "text" in view:
        if page is not None or transcription != "text_layer":
            return None, "non-PDF evidence is a text_layer quote without a page"
        if flat(quote) not in flat(view["text"]):
            return None, "quote is not in the original's extracted text"
    else:
        read = pages[sha]
        if type(page) is not int or read is None or page not in read:
            return None, "evidence page was not declared as read"
        if transcription == "text_layer":
            if flat(quote) not in flat(view["pages"][page - 1]):
                return None, "quote is not in the page text layer"
        elif transcription != "visual":
            return None, "transcription must be text_layer or visual"
    return {
        "original_sha256": sha,
        "page": page,
        "quote": quote,
        "transcription": transcription,
        "supports": sorted(set(supports)),
    }, None


def check_review(
    target: dict[str, Any], entry: Any, sources: Sources
) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(entry, dict):
        return {}, ["case entry must be an object"]
    errors = [f"unknown field {field}" for field in sorted(set(entry) - ENTRY_FIELDS)]
    given = entry.get("answers", {})
    if not isinstance(given, dict):
        errors.append("answers must be an object")
        given = {}
    for question, value in given.items():
        if question not in target["questions"]:
            errors.append(f"unknown question {question}")
        elif not valid_answer(question, value):
            errors.append(f"invalid {question} answer {value!r}")
    answers = {question: given.get(question, "unknown") for question in target["questions"]}
    pages, spans, scope = read_ranges(target, entry.get("read_ranges"), sources, errors)
    evidence, supported = [], set()
    items = entry.get("evidence", [])
    for item in items if isinstance(items, list) else [None]:
        checked, problem = check_evidence(target, item, pages, spans, sources)
        if checked is None:
            errors.append(problem or "invalid evidence")
            continue
        evidence.append(checked)
        supported.update(checked["supports"])
    errors.extend(
        f"{question} answer has no supporting evidence"
        for question, value in answers.items()
        if value != "unknown" and question not in supported
    )
    notes = entry.get("notes", "")
    if not isinstance(notes, str):
        errors.append("notes must be text")
    review = {
        "answers": answers,
        "read_scope": scope,
        "read_ranges": entry.get("read_ranges"),
        "evidence": evidence,
        **({"notes": notes} if notes else {}),
    }
    return review, errors


def load_answers(path: Path, pin: str, queue_pin: str) -> dict[str, Any]:
    data = read_artifact(path)
    if digest(data) != pin:
        raise ValueError(f"Answers SHA mismatch: {path.name}")
    value = json.loads(data)
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Answers must be a version 1 object")
    if value.get("queue_manifest_sha256") != queue_pin:
        raise ValueError("Answers were written for another queue")
    if not isinstance(value.get("reviewer"), str) or not value["reviewer"].strip():
        raise ValueError("Answers require a named reviewer")
    if value.get("reviewer_kind") not in ("human", "agent"):
        raise ValueError("Reviewer kind must be human or agent")
    if value.get("reviewed_at") == "unknown" or not valid_answer(
        "draft_date", value.get("reviewed_at")
    ):
        raise ValueError("Answers require an ISO review date")
    if not isinstance(value.get("cases"), dict) or not value["cases"]:
        raise ValueError("Answers contain no case")
    return value


def settle(identity: str, target: dict[str, Any], reviews: list[dict[str, Any]]) -> dict[str, Any]:
    human = [review for review in reviews if review["reviewer_kind"] == "human"]
    labels: dict[str, Any] = {}
    for question in target["questions"]:
        values: list[Any] = []
        for review in human:
            value = review["answers"][question]
            if value != "unknown" and value not in values:
                values.append(value)
        labels[question] = (
            "unknown" if not values else values[0] if len(values) == 1 else {"conflict": values}
        )
    status = (
        "pending"
        if not human
        else "conflict"
        if any(isinstance(value, dict) for value in labels.values())
        else "reviewed"
    )
    independent = labels.get("independent_initiative")
    split = (
        "regression"
        if not target["holdout_eligible"] or len(human) != len(reviews)
        else "holdout"
        if status == "reviewed" and independent == "yes"
        else "regression"
        if status == "reviewed" and independent == "no"
        else "holdout_pending"
    )
    return {
        "id": identity,
        "title": target["title"],
        "group": target["group"],
        "status": status,
        "split": split,
        "oracle": "human_review" if human else None,
        "labels": labels,
        "reviews": reviews,
    }


def guarded_output(root: Path, queue: Path, output: Path) -> Path:
    target = output.resolve()
    html_path = target.with_suffix(".html")
    if (
        target.exists()
        or output.is_symlink()
        or html_path.exists()
        or html_path.is_symlink()
        or target.with_suffix("").exists()
        or target.is_relative_to(root)
        or target.is_relative_to(queue.resolve())
    ):
        raise ValueError("Review output must be new and outside its inputs")
    return target


def record(
    corpus: Path, queue: Path, queue_pin: str, answers: list[tuple[Path, str]], output: Path
) -> None:
    root = corpus.resolve(strict=True)
    target = guarded_output(root, queue, output)
    if not answers:
        raise ValueError("At least one answers file is required")
    code = source_fingerprints()
    suite, targets = queue_targets(root, queue, queue_pin)
    sources = Sources(root)
    reviews: dict[str, list[dict[str, Any]]] = {identity: [] for identity in targets}
    errors: list[str] = []
    answer_inputs: dict[str, str] = {}
    for path, pin in answers:
        submission = load_answers(path, pin, queue_pin)
        answer_inputs[str(path.resolve())] = pin
        for identity, entry in sorted(submission["cases"].items()):
            where = f"{path.name}: {identity}"
            if identity not in targets:
                errors.append(f"{where}: not in the pinned queue")
                continue
            if any(row["reviewer"] == submission["reviewer"] for row in reviews[identity]):
                errors.append(f"{where}: duplicate review by one reviewer")
                continue
            review, problems = check_review(targets[identity], entry, sources)
            errors.extend(f"{where}: {problem}" for problem in problems)
            if not problems:
                reviews[identity].append(
                    {
                        "reviewer": submission["reviewer"],
                        "reviewer_kind": submission["reviewer_kind"],
                        "reviewed_at": submission["reviewed_at"],
                        "answers_sha256": pin,
                        **review,
                    }
                )
    if errors:
        raise ValueError("Review answers rejected:\n" + "\n".join(errors))
    cases = [settle(identity, targets[identity], reviews[identity]) for identity in targets]
    if (
        sources.reads.changed_inputs()
        or code != source_fingerprints()
        or digest(read_artifact(queue / "manifest.json")) != queue_pin
        or any(digest(read_artifact(Path(path))) != sha for path, sha in answer_inputs.items())
    ):
        raise ValueError("Review inputs or code changed during recording")
    write_json(
        target,
        {
            "version": 1,
            "status": "recorded",
            "scope": "Validated reviewer answers over a pinned queue; unreviewed cases pending",
            "code_sha": code_version(),
            "source_sha256": code,
            "queue": {"path": str(queue.resolve()), "manifest_sha256": queue_pin, "suite": suite},
            "answers": answer_inputs,
            "read_inputs": sources.reads.initial_sha256,
            "external_inputs": sources.external,
            "cases": cases,
            "counts": {
                "status": dict(Counter(case["status"] for case in cases)),
                "split": dict(Counter(case["split"] for case in cases)),
            },
            "exclusions": [
                "Only human reviews form labels; agent reviews stay separate observations.",
                "Unanswered questions stay unknown; disagreeing human answers stay conflicts.",
                "Visual transcriptions are bound to an original page, not to a text layer.",
                "Holdout requires a human-confirmed independent initiative and no agent review.",
                "Declared read ranges are the reviewer's statement; unread pages stay unread.",
            ],
        },
    )


def asset_name(sha: str, item: dict[str, Any], data: bytes) -> str:
    suffix = Path(item["members"][-1]).suffix.lower() if item["members"] else ""
    if b"%PDF-" in data[:1024]:
        suffix = ".pdf"
    elif not re.fullmatch(r"\.[a-z0-9]{1,5}", suffix):
        suffix = ".bin"
    return sha + suffix


def place(assets: Path, name: str, data: bytes) -> str:
    assets.mkdir(exist_ok=True)
    if not (assets / name).exists():
        with (assets / name).open("xb") as stream:
            stream.write(data)
    return f"{assets.name}/{name}"


def form(corpus: Path, queue: Path, queue_pin: str, output: Path) -> None:
    root = corpus.resolve(strict=True)
    target = guarded_output(root, queue, output)
    code = source_fingerprints()
    suite, targets = queue_targets(root, queue, queue_pin)
    sources = Sources(root)
    assets = target.with_suffix("")
    cases = []
    for identity, case in targets.items():
        originals = []
        for sha, item in case["originals"].items():
            data = sources.original(item, sha)
            pdf = b"%PDF-" in data[:1024]
            link = Path(item["path"]).as_uri()
            if item["members"]:
                link = place(assets, asset_name(sha, item, data), data)
            preview = None
            if "preview" in item:
                image = read_artifact(Path(item["preview"]))
                if digest(image) != item["preview_sha256"]:
                    raise ValueError("Review preview changed")
                preview = place(assets, f"{sha}-page-1.png", image)
            originals.append(
                {
                    "sha256": sha,
                    "label": item["label"],
                    "link": link,
                    "pages": PypdfTextExtractor().pages(data) if pdf else None,
                    "preview": preview,
                }
            )
        texts = []
        for sha, item in case["texts"].items():
            sources.text(item, sha)
            texts.append({"sha256": sha, "ranges": item["shown"]})
        cases.append(
            {
                "id": identity,
                "title": case["title"],
                "group": case["group"],
                "questions": case["questions"],
                "holdout_eligible": case["holdout_eligible"],
                "originals": originals,
                "texts": texts,
            }
        )
    if (
        sources.reads.changed_inputs()
        or code != source_fingerprints()
        or digest(read_artifact(queue / "manifest.json")) != queue_pin
    ):
        raise ValueError("Review inputs or code changed while preparing the form")
    queue_meta = {"path": str(queue.resolve()), "manifest_sha256": queue_pin, "suite": suite}
    with target.with_suffix(".html").open("x") as stream:
        stream.write(form_page(queue_meta, cases))
    write_json(
        target,
        {
            "version": 1,
            "status": "recorded",
            "scope": "Fillable review form over a pinned queue; it creates no labels",
            "code_sha": code_version(),
            "source_sha256": code,
            "queue": queue_meta,
            "read_inputs": sources.reads.initial_sha256,
            "external_inputs": sources.external,
            "cases": cases,
            "exclusions": [
                "The form shows links, page counts and queue excerpts; it reads no legal text.",
                "Saved answers become labels only after the recording suite validates them.",
            ],
        },
    )


def form_page(queue: dict[str, Any], cases: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        {"queue": queue, "cases": cases, "roles": ROLES, "pageQuestions": PAGE_QUESTIONS},
        ensure_ascii=False,
    ).replace("<", "\\u003c")
    return (
        '<!doctype html><html lang="ru"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Corpus review form</title><style>" + FORM_STYLE + "</style>"
        "<h1>Ручная проверка корпуса</h1><p>Откройте оригинал, укажите прочитанные страницы, "
        "ответьте только на то, что подтверждает источник, и добавьте цитату для каждого ответа. "
        "Неизвестное оставьте unknown. Сохранённый файл проверяется suite <code>review</code>; "
        "сама форма разметку не создаёт.</p>"
        '<div class="bar"><label>Reviewer <input id="reviewer"></label>'
        '<label>Дата <input id="reviewed" type="date"></label>'
        '<button id="save">Сохранить ответы</button>'
        '<label class="file">Загрузить ответы <input id="load" type="file" accept=".json"></label>'
        '<span id="status"></span></div><main id="cases"></main>'
        '<script type="application/json" id="data">' + payload + "</script>"
        "<script>" + FORM_SCRIPT + "</script></html>"
    )


FORM_STYLE = (
    ":root{color-scheme:light dark}body{font:16px system-ui;max-width:1100px;margin:auto;"
    "padding:1rem}article{border-top:1px solid #999;margin-top:2rem;padding-top:.5rem}"
    "pre{white-space:pre-wrap;max-height:20rem;overflow:auto;background:#8881;padding:.5rem}"
    "label{display:block;margin:.4rem 0}textarea{width:100%;min-height:3rem}"
    "input:not([type=checkbox]),select{max-width:100%}img{max-width:100%;border:1px solid #999}"
    ".bar{position:sticky;top:0;background:Canvas;padding:.5rem 0;display:flex;gap:1rem;"
    "flex-wrap:wrap;align-items:end;border-bottom:1px solid #999}.bar label{margin:0}"
    ".evidence{border-left:3px solid #999;padding-left:.5rem;margin:.5rem 0}"
    ".supports label{display:inline;margin-right:.8rem}.file input{display:block}"
    "li{overflow-wrap:anywhere}"
)

FORM_SCRIPT = r"""
const data = JSON.parse(document.getElementById("data").textContent);
const storeKey = "corpus-review:" + data.queue.manifest_sha256;
const today = new Date().toLocaleDateString("sv-SE");
let state = {reviewer: "", reviewed_at: today, cases: {}};
try { state = JSON.parse(localStorage.getItem(storeKey)) || state; } catch (e) {}
const el = (tag, props = {}, ...children) => {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
};
const caseState = id => state.cases[id] ||= {read: {}, answers: {}, evidence: [], notes: ""};
const persist = () => {
  try { localStorage.setItem(storeKey, JSON.stringify(state)); } catch (e) {}
};
function parsePages(text) {
  const pages = [];
  for (const part of text.split(",").map(s => s.trim()).filter(Boolean)) {
    const m = part.match(/^(\d+)(?:-(\d+))?$/);
    if (!m) throw new Error("Страницы: " + text);
    for (let p = Number(m[1]); p <= Number(m[2] || m[1]); p++) pages.push(p);
  }
  return pages;
}
function control(c, q) {
  const s = caseState(c.id);
  const value = s.answers[q] || "";
  const update = e => { s.answers[q] = e.target.value; persist(); };
  if (q === "independent_initiative" || q === "role") {
    const options = q === "role" ? data.roles : ["yes", "no"];
    const select = el("select", {onchange: update},
      el("option", {value: "", textContent: "unknown"}),
      ...options.map(o => el("option", {value: o, textContent: o})));
    select.value = value;
    return select;
  }
  if (q === "draft_date") return el("input", {type: "date", value, onchange: update});
  if (q === "required_annexes") return el("textarea", {value, oninput: update,
    placeholder: "по одному названию в строке, или none; пусто = unknown"});
  const hint = data.pageQuestions.includes(q) ? "1,3-4 или none; пусто = unknown"
    : "druk:123, rpw:RPW/1/2026, rcl:123, wpl:UD1 или none; пусто = unknown";
  return el("input", {value, oninput: update, placeholder: hint, size: 40});
}
function evidenceRow(c, ev, index) {
  const s = caseState(c.id);
  const sources = [...c.originals.map(o => ["o:" + o.sha256, (o.pages ? "PDF " : "") + o.label]),
    ...c.texts.map(t => ["t:" + t.sha256, "текст " + t.sha256.slice(0, 12)])];
  const source = el("select", {onchange: e => { ev.source = e.target.value; persist(); }},
    ...sources.map(([v, t]) => el("option", {value: v, textContent: t})));
  ev.source ||= sources[0] && sources[0][0];
  source.value = ev.source;
  const transcription = el("select",
    {onchange: e => { ev.transcription = e.target.value; persist(); }},
    el("option", {value: "visual", textContent: "visual (видно на странице)"}),
    el("option", {value: "text_layer", textContent: "text_layer (копия текстового слоя)"}));
  transcription.value = ev.transcription ||= "visual";
  const supports = el("div", {className: "supports"}, "Подтверждает: ", ...c.questions.map(q => {
    const box = el("input", {type: "checkbox", checked: (ev.supports || []).includes(q),
      onchange: e => { ev.supports = c.questions.filter(k => k === q ? e.target.checked
        : (ev.supports || []).includes(k)); persist(); }});
    return el("label", {}, box, " " + q);
  }));
  return el("div", {className: "evidence"},
    el("label", {}, "Источник ", source),
    el("label", {}, "Страница PDF ", el("input", {type: "number", min: 1, value: ev.page || "",
      oninput: e => { ev.page = e.target.value; persist(); }}), " ", transcription),
    el("label", {}, "Точная цитата", el("textarea", {value: ev.quote || "",
      oninput: e => { ev.quote = e.target.value; persist(); }})),
    supports,
    el("button", {textContent: "Удалить цитату", onclick: () => {
      s.evidence.splice(index, 1); persist(); render(); }}));
}
function card(c) {
  const s = caseState(c.id);
  const originals = el("ul", {}, ...c.originals.map(o => {
    const read = o.pages === null
      ? el("label", {}, el("input", {type: "checkbox", checked: s.read[o.sha256] === true,
          onchange: e => { s.read[o.sha256] = e.target.checked; persist(); }}), " прочитан целиком")
      : el("label", {}, "Прочитанные страницы (из " + o.pages + ") ", el("input", {
          value: s.read[o.sha256] || "", placeholder: "1-3,5",
          oninput: e => { s.read[o.sha256] = e.target.value; persist(); }}));
    return el("li", {}, el("a", {href: o.link, target: "_blank", textContent: o.label}),
      " · SHA " + o.sha256.slice(0, 12), read,
      ...(o.preview ? [el("details", {}, el("summary", {textContent: "Страница 1"}),
        el("img", {src: o.preview, loading: "lazy", alt: "Первая страница оригинала"}))] : []));
  }));
  const texts = c.texts.map(t => el("div", {},
    el("pre", {textContent: t.ranges.map(r => r.text).join("\n\n[…]\n\n")}),
    el("label", {}, el("input", {type: "checkbox", checked: s.read[t.sha256] === true,
      onchange: e => { s.read[t.sha256] = e.target.checked; persist(); }}),
      " прочитаны показанные выдержки")));
  const evidence = el("div", {}, ...s.evidence.map((ev, i) => evidenceRow(c, ev, i)));
  return el("article", {id: c.id},
    el("h2", {textContent: c.id + " — " + c.title}),
    el("p", {textContent: "Group: " + c.group + (c.holdout_eligible ? " · holdout_pending" : "")}),
    originals, ...texts,
    ...c.questions.map(q => el("label", {}, q + " ", control(c, q))),
    evidence,
    el("button", {textContent: "Добавить цитату", onclick: () => {
      s.evidence.push({supports: []}); persist(); render(); }}),
    el("label", {}, "Заметки", el("textarea", {value: s.notes,
      oninput: e => { s.notes = e.target.value; persist(); }})));
}
function answerValue(q, raw) {
  const value = (raw || "").trim();
  if (!value) return "unknown";
  if (value === "none" || q === "independent_initiative" || q === "role" || q === "belongs_to"
      || q === "draft_date") return value;
  if (q === "required_annexes") return value.split("\n").map(s => s.trim()).filter(Boolean);
  return parsePages(value);
}
function exportAnswers() {
  const cases = {};
  for (const c of data.cases) {
    const s = state.cases[c.id];
    if (!s) continue;
    const read_ranges = [];
    for (const o of c.originals) {
      const value = s.read[o.sha256];
      if (o.pages === null ? value === true : value && value.trim())
        read_ranges.push({original_sha256: o.sha256,
          pages: o.pages === null ? null : parsePages(value)});
    }
    for (const t of c.texts) if (s.read[t.sha256] === true)
      for (const r of t.ranges) if (r.end > r.start)
        read_ranges.push({text_sha256: t.sha256, start: r.start, end: r.end});
    const answers = {};
    for (const q of c.questions) {
      const value = answerValue(q, s.answers[q]);
      if (value !== "unknown") answers[q] = value;
    }
    const evidence = s.evidence.filter(ev => (ev.quote || "").trim()).map(ev => {
      const [kind, sha] = ev.source.split(":");
      const original = c.originals.find(o => o.sha256 === sha);
      return kind === "t" ? {text_sha256: sha, quote: ev.quote, supports: ev.supports || []}
        : {original_sha256: sha, quote: ev.quote, supports: ev.supports || [],
           page: original.pages === null ? null : Number(ev.page),
           transcription: original.pages === null ? "text_layer" : ev.transcription};
    });
    const notes = s.notes.trim();
    if (!read_ranges.length && !Object.keys(answers).length && !evidence.length && !notes) continue;
    cases[c.id] = {read_ranges, answers, evidence, ...(notes ? {notes} : {})};
  }
  return {version: 1, queue_manifest_sha256: data.queue.manifest_sha256,
    reviewer: state.reviewer.trim(), reviewer_kind: "human", reviewed_at: state.reviewed_at, cases};
}
function importAnswers(answers) {
  if (answers.queue_manifest_sha256 !== data.queue.manifest_sha256)
    throw new Error("Другая очередь");
  state = {reviewer: answers.reviewer || "", reviewed_at: answers.reviewed_at || today, cases: {}};
  for (const [id, entry] of Object.entries(answers.cases || {})) {
    const s = caseState(id);
    for (const r of entry.read_ranges || []) {
      if (r.text_sha256) s.read[r.text_sha256] = true;
      else s.read[r.original_sha256] = r.pages === null ? true : r.pages.join(",");
    }
    for (const [q, v] of Object.entries(entry.answers || {}))
      s.answers[q] = Array.isArray(v) ? v.join(q === "required_annexes" ? "\n" : ",") : v;
    s.evidence = (entry.evidence || []).map(ev => ({
      source: ev.text_sha256 ? "t:" + ev.text_sha256 : "o:" + ev.original_sha256,
      page: ev.page || "", transcription: ev.transcription || "visual",
      quote: ev.quote, supports: ev.supports}));
    s.notes = entry.notes || "";
  }
}
function render() {
  const reviewer = document.getElementById("reviewer");
  reviewer.value = state.reviewer;
  reviewer.oninput = e => { state.reviewer = e.target.value; persist(); };
  const reviewed = document.getElementById("reviewed");
  reviewed.value = state.reviewed_at;
  reviewed.onchange = e => { state.reviewed_at = e.target.value; persist(); };
  document.getElementById("cases").replaceChildren(...data.cases.map(card));
}
document.getElementById("save").onclick = () => {
  const status = document.getElementById("status");
  try {
    if (!state.reviewer.trim()) throw new Error("Укажите reviewer");
    const answers = exportAnswers();
    if (!Object.keys(answers.cases).length) throw new Error("Нет заполненных случаев");
    const link = el("a", {download: "review-answers-" + answers.reviewed_at + ".json",
      href: URL.createObjectURL(new Blob([JSON.stringify(answers, null, 2) + "\n"],
        {type: "application/json"}))});
    link.click();
    URL.revokeObjectURL(link.href);
    status.textContent = Object.keys(answers.cases).length + " случаев сохранено";
  } catch (e) { status.textContent = e.message; }
};
document.getElementById("load").onchange = async e => {
  const status = document.getElementById("status");
  try {
    importAnswers(JSON.parse(await e.target.files[0].text()));
    persist();
    render();
    status.textContent = "Ответы загружены";
  } catch (error) { status.textContent = error.message; }
};
render();
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("form", "record"), required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--queue-sha256", required=True)
    parser.add_argument("--answers", type=Path, action="append", default=[])
    parser.add_argument("--answers-sha256", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "form":
        if args.answers or args.answers_sha256:
            raise ValueError("A form takes no answers")
        form(args.corpus, args.queue, args.queue_sha256, args.output)
        return
    if len(args.answers) != len(args.answers_sha256):
        raise ValueError("Every answers file needs its SHA")
    record(
        args.corpus,
        args.queue,
        args.queue_sha256,
        list(zip(args.answers, args.answers_sha256, strict=True)),
        args.output,
    )


if __name__ == "__main__":
    main()
