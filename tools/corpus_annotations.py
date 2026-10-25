"""Prepare grouped span reviews while keeping machine observations distinct from gold labels."""

import argparse
import gzip
import html
import json
from pathlib import Path
from typing import Any

from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_health import local_path
from tools.corpus_rcl import DerivedTexts
from tools.corpus_runtime import CorpusReads, source_fingerprints


def initiative_groups(index: dict[str, Any]) -> dict[str, str]:
    parent: dict[str, str] = {}

    def find(identity: str) -> str:
        parent.setdefault(identity, identity)
        while identity != parent[identity]:
            parent[identity] = parent[parent[identity]]
            identity = parent[identity]
        return identity

    def join(left: str, right: str) -> None:
        a, b = sorted((find(left), find(right)))
        parent[b] = a

    entities = index["entities"]
    for identity, entity in entities.items():
        find(identity)
        for field in ("rcl", "druk", "rpw"):
            related = entity.get(field)
            if isinstance(related, str) and related in entities:
                join(identity, related)
        for document in entity.get("documents", []):
            join(identity, f"doc:{document}")
    return {identity: find(identity) for identity in parent}


def validate_annotation(case: dict[str, Any], annotation: dict[str, Any], text: str) -> None:
    if (
        digest(text.encode()) != case["text_sha256"]
        or annotation.get("text_sha256") != case["text_sha256"]
    ):
        raise ValueError("Annotation source identity mismatch")
    if not annotation.get("reviewer") or annotation.get("reviewer_kind") not in ("human", "agent"):
        raise ValueError("Annotation requires an explicit reviewer and reviewer kind")
    if annotation.get("read_scope") not in ("excerpt", "full_text", "original_pages"):
        raise ValueError("Annotation must declare the actual reading scope")
    if not annotation.get("evidence"):
        raise ValueError("Reviewed annotation requires evidence")
    for span in annotation["evidence"]:
        start, end = span["start"], span["end"]
        if not 0 <= start < end <= len(text) or text[start:end] != span["quote"]:
            raise ValueError("Annotation quote differs from its source span")
    if annotation.get("split") == "holdout" and (
        annotation["reviewer_kind"] != "human" or case.get("used_for_tuning")
    ):
        raise ValueError("Agent or previously consumed cases cannot become independent holdout")


def viewer(cases: dict[str, Any]) -> str:
    cards = []
    for key, case in cases.items():
        excerpt = "\n\n[…]\n\n".join(part["text"] for part in case["read_ranges"])
        link = (
            f'<a href="{html.escape(case["url"], quote=True)}">Original source</a>'
            if case["url"].startswith(("https://", "http://"))
            else "Source URL unavailable"
        )
        cards.append(
            f"<article><h2>{html.escape(key)}: {html.escape(case['name'])}</h2>"
            f"<p>Group: {html.escape(case['group'])}; read scope: excerpt; status: pending</p>"
            f"<p>{link}</p>"
            f"<pre>{html.escape(excerpt)}</pre></article>"
        )
    payload = json.dumps(cases, ensure_ascii=False).replace("<", "\\u003c")
    return (
        '<!doctype html><html lang="ru"><meta charset="utf-8"><title>Corpus review queue</title>'
        "<style>body{font:16px system-ui;max-width:1000px;margin:auto;padding:2rem}"
        "pre{white-space:pre-wrap}article{border-top:1px solid #999;margin-top:2rem}</style>"
        "<h1>Очередь разметки корпуса</h1><p>Показаны выдержки, не полное прочтение. "
        "Заполните reviewer, reviewer_kind, read_scope и evidence с точными координатами. "
        "Шаблон содержит pending cases и не является gold-разметкой.</p>"
        '<button onclick="save()">Скачать шаблон аннотаций</button>'
        + "".join(cards)
        + "<script>const cases="
        + payload
        + ';function save(){const a=document.createElement("a");'
        "a.href=URL.createObjectURL(new Blob([JSON.stringify({version:1,cases},null,2)],"
        '{type:"application/json"}));'
        'a.download="annotations-pending.json";a.click();URL.revokeObjectURL(a.href)}</script></html>'
    )


def run(
    corpus: Path,
    health: Path,
    pin: str,
    output: Path,
    *,
    derivatives: Path | None = None,
    derivatives_sha256: str | None = None,
) -> None:
    root, target = corpus.resolve(strict=True), output.resolve()
    if target.exists() or output.is_symlink() or target.is_relative_to(root):
        raise ValueError("Output must be new and outside the corpus")
    raw = read_artifact(health)
    if digest(raw) != pin:
        raise ValueError("Health report SHA mismatch")
    baseline = json.loads(raw)
    derived = DerivedTexts(root, derivatives, derivatives_sha256) if derivatives else None
    reads, code = CorpusReads(root), source_fingerprints()
    if derived is not None:
        reads.allowed += (derived.base,)
    cases: dict[str, Any] = {}
    external_inputs = derived.inputs if derived is not None else {}
    with reads.guard():
        index = json.loads(local_path(root, "INDEX.json").read_bytes())
        records = json.loads(local_path(root, "documents/index.json").read_bytes())
        groups = initiative_groups(index)
        for identity, row in baseline["records"].items():
            if row["text_state"] != "empty":
                continue
            data = local_path(root, row["text_path"]).read_bytes()
            if digest(data) != baseline["read_inputs"][row["text_path"]]:
                raise ValueError("Review source changed since health inspection")
            text = gzip.decompress(data).decode()
            source_path = str(root / row["text_path"])
            current = derived.get(identity) if derived is not None else None
            if current is not None and derived is not None:
                text = current
                source_path = str(derived.base / derived.rows[identity]["extraction"])
            meta = records[identity]
            cases[f"doc:{identity}"] = {
                "status": "pending",
                "group": groups.get(f"doc:{identity}", f"doc:{identity}"),
                "name": meta.get("name", identity),
                "url": meta.get("url", ""),
                "text_sha256": digest(text.encode()),
                "text_source": source_path,
                "read_scope": "excerpt",
                "read_ranges": [{"start": 0, "end": min(2000, len(text)), "text": text[:2000]}]
                + (
                    [{"start": len(text) - 600, "end": len(text), "text": text[-600:]}]
                    if len(text) > 2600
                    else []
                ),
                "reviewer": None,
                "reviewer_kind": None,
                "evidence": [],
                "split": "unassigned",
                "questions": {
                    "role": "unknown",
                    "belongs_to": "unknown",
                    "required_annexes": "unknown",
                    "draft_date": "unknown",
                },
                "used_for_tuning": True,
            }
    if (
        not cases
        or reads.changed_inputs()
        or code != source_fingerprints()
        or any(digest(read_artifact(Path(path))) != sha for path, sha in external_inputs.items())
    ):
        raise ValueError("Review population is empty or its inputs/code changed")
    html_path = target.with_suffix(".html")
    if html_path.exists() or html_path.is_symlink():
        raise ValueError("Review viewer must be a new file")
    with html_path.open("x") as stream:
        stream.write(viewer(cases))
    write_json(
        target,
        {
            "status": "recorded",
            "scope": "Pending review of known empty caches; no new independent holdout",
            "cases": list({"id": key, **value} for key, value in cases.items()),
            "read_inputs": reads.initial_sha256,
            "source_sha256": code,
            "derivative_inputs": external_inputs,
            "group_count": len({case["group"] for case in cases.values()}),
            "exclusions": [
                "Empty cached text needs review of its original or a new derivative.",
                "These known diagnostic cases are consumed and cannot be an independent holdout.",
            ],
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--health", type=Path, required=True)
    parser.add_argument("--health-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--derivatives", type=Path)
    parser.add_argument("--derivatives-sha256")
    args = parser.parse_args()
    run(
        args.corpus,
        args.health,
        args.health_sha256,
        args.output,
        derivatives=args.derivatives,
        derivatives_sha256=args.derivatives_sha256,
    )


if __name__ == "__main__":
    main()
