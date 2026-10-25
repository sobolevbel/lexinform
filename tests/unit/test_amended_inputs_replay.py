import gzip
import hashlib
import json
from pathlib import Path

import pytest
from tools import replay_amended_inputs
from tools.replay_document_candidates import CachedExtractor, document_id

from lexinform.models import TextDocument
from lexinform.services.documents import TextLoader
from tests.scenario.test_document_selection_contract import law


def test_cached_amendment_replay_preserves_inputs_and_traces_only_source_fragments(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    text = corpus / "documents/text"
    text.mkdir(parents=True)
    (text / "1.txt.gz").write_bytes(gzip.compress(law().encode()))
    operations = "AUTOPOPRAWKA\nW art. 1 wyrazy 30 dni zastępuje się wyrazami 60 dni.\n" * 8
    (text / "2.txt.gz").write_bytes(gzip.compress(operations.encode()))
    document = TextDocument(
        url="https://legislacja.rcl.gov.pl/dokument1",
        kind="rcl",
        amendment_urls=("https://legislacja.rcl.gov.pl/dokument2",),
    )
    loader = TextLoader(
        {"legislacja.rcl.gov.pl": document_id}, CachedExtractor(corpus), max_bytes=32_000_000
    )

    before = replay_amended_inputs.snapshot(document, loader, mapped=False)
    after = replay_amended_inputs.snapshot(document, loader, mapped=True)

    assert before["core"] == after["core"]
    assert len(after["mapping"]) == 5
    assert all(
        item["round_trip"] and item["generated_chars"] > 0 for item in after["mapping"].values()
    )
    assert before["coverage"] != after["coverage"]

    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps(
            {"read_inputs": {}, "cases": {"1/1": {"document": document.model_dump(mode="json")}}}
        )
    )
    sha = hashlib.sha256(inventory.read_bytes()).hexdigest()
    output = tmp_path / "result.json"
    assert replay_amended_inputs.run(corpus, inventory, sha, output, mapped=True) == 0
    result = json.loads(output.read_bytes())
    assert result["status"] == "recorded" and not result["errors"]
    assert not result["changed_inputs"] and not result["changed_code"]
    with pytest.raises(ValueError, match="inventory SHA"):
        replay_amended_inputs.run(corpus, inventory, "0" * 64, tmp_path / "bad.json", mapped=True)
