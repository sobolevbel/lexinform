"""Clock-free omission accounting retains at most eight hash-only example identities."""

import json
from collections import Counter
from threading import Lock

from lexinform.metadata_urls import MetadataUrlOmissionReason
from lexinform.models.metadata_omissions import (
    MAX_METADATA_OMISSION_BYTES,
    MAX_METADATA_OMISSION_EXAMPLES,
    MetadataUrlOmissionExample,
    MetadataUrlOmissions,
    MetadataUrlReasonCount,
)

type _ExampleKey = tuple[str, str, str]


class MetadataUrlOmissionCollector:
    def __init__(self) -> None:
        self._lock = Lock()
        self._counts: Counter[MetadataUrlOmissionReason] = Counter()
        self._examples: dict[_ExampleKey, MetadataUrlOmissionExample] = {}
        self._total = 0
        self._omitted = 0

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()
            self._examples.clear()
            self._total = 0
            self._omitted = 0

    def observe(
        self,
        reason: MetadataUrlOmissionReason,
        *,
        requested_url_sha256: str | None,
        artifact_sha256: str | None = None,
        count: int = 1,
    ) -> None:
        example = MetadataUrlOmissionExample(
            reason=reason,
            requested_url_sha256=requested_url_sha256,
            artifact_sha256=artifact_sha256,
            count=count,
        )
        key = example.key
        with self._lock:
            if self._total + count >= 2**63:
                raise ValueError("metadata omission observation count exceeds the integer cap")
            self._total += count
            self._counts[reason] += count
            previous = self._examples.get(key)
            if previous is not None:
                self._examples[key] = previous.model_copy(update={"count": previous.count + count})
                return
            if len(self._examples) == MAX_METADATA_OMISSION_EXAMPLES:
                last = max(self._examples)
                if key > last:
                    self._omitted += count
                    return
                self._omitted += self._examples.pop(last).count
            self._examples[key] = example

    def snapshot(self) -> MetadataUrlOmissions | None:
        with self._lock:
            if not self._total:
                return None
            base = MetadataUrlOmissions(
                total=self._total,
                reasons=tuple(
                    MetadataUrlReasonCount(reason=reason, count=self._counts[reason])
                    for reason in sorted(self._counts)
                ),
                omitted_observations=self._total,
            )
            examples = [self._examples[key] for key in sorted(self._examples)]
            omitted = self._omitted
            payload = base.model_dump(mode="json")
            while True:
                payload["examples"] = [item.model_dump(mode="json") for item in examples]
                payload["omitted_observations"] = omitted
                if (
                    len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
                    <= MAX_METADATA_OMISSION_BYTES
                ):
                    return MetadataUrlOmissions.model_validate(payload)
                omitted += examples.pop().count
