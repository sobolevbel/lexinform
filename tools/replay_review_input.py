"""Verify ordered review-document text fields in a frozen provider request."""

import argparse
from pathlib import Path
from typing import Literal

from lexinform.adapters.input_receipts import capture_frozen_request_manifest, parse_batch_request
from lexinform.adapters.review_prepared_input import verify_review_provider_fields
from lexinform.models.batch import BatchRequest
from tools.corpus_artifacts import digest, read_artifact, write_json

Provider = Literal["openai", "anthropic"]


def verify_review_input(request: BatchRequest, provider: Provider) -> dict[str, object]:
    manifest = capture_frozen_request_manifest(request, provider)
    if manifest is None:
        raise ValueError("Exact frozen provider request is unavailable")
    fields = verify_review_provider_fields(request, manifest)
    projected = any(field != "text" for field in fields.document_fields)
    return {
        "version": "review-provider-text-v2" if projected else "review-provider-text-v1",
        "coordinates": (
            "decoded-review-source-text-python-string"
            if projected
            else "decoded-review-json-field-python-string"
        ),
        "request_manifest": manifest.model_dump(mode="json"),
        "context_sha256": fields.context_sha256,
        "provider_prompt_pointer": fields.pointer,
        "documents": [
            {
                "pointer": f"/documents/{index}/{fields.document_fields[index]}",
                **document.model_dump(exclude={"chars"}),
                "span": [0, document.chars],
            }
            for index, document in enumerate(fields.documents)
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("openai", "anthropic"), required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--request-file-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        raw = read_artifact(args.request)
        if digest(raw) != args.request_file_sha256:
            raise ValueError("Pinned review request file SHA-256 differs")
        proof = verify_review_input(parse_batch_request(raw.decode()), args.provider)
        write_json(args.output, proof)
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
