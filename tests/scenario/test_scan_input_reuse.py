import base64
import hashlib

import pytest

from lexinform.models import ScannedDocument, TextDocument
from lexinform.services.documents import TextLoader
from tests.fakes import FakeTextExtractor
from tests.harness import World


@pytest.mark.parametrize("legacy", [False, True])
def test_quiet_reanalysis_keeps_the_paid_scan_manifest_or_legacy_unknown(
    legacy: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(extractor=FakeTextExtractor("", page_count=30))
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    load = TextLoader.load_scan

    def untraced(loader: TextLoader, url: str, *, cover_letter: bool) -> ScannedDocument | None:
        scan = load(loader, url, cover_letter=cover_letter)
        return scan.model_copy(update={"scan_input": None}) if scan else None

    with monkeypatch.context() as patch:
        if legacy:
            patch.setattr(TextLoader, "load_scan", untraced)
        world.run(track=False)
    original = world.bill("3039").analysis
    assert original is not None and original.source_url is not None, "the first scan was analysed"

    def reencoded(loader: TextLoader, url: str, *, cover_letter: bool) -> ScannedDocument | None:
        scan = load(loader, url, cover_letter=cover_letter)
        if scan is None or scan.scan_input is None:
            return scan
        data = base64.b64decode(scan.data) + b"\n% rewritten PDF\n"
        manifest = scan.scan_input.model_copy(
            update={"input_sha256": hashlib.sha256(data).hexdigest(), "input_bytes": len(data)}
        )
        return ScannedDocument.model_validate(
            {**scan.model_dump(), "data": base64.b64encode(data).decode(), "scan_input": manifest}
        )

    monkeypatch.setattr(TextLoader, "load_scan", reencoded)
    world.clock.advance(days=1)

    result = world.analysis.reanalyze_bill(
        world.bill("3039"), TextDocument(url=original.source_url, kind="print")
    )

    saved = world.bill("3039").analysis
    assert saved is not None, "quiet reanalysis preserves the existing record"
    assert result is None and len(world.llm.contexts) == 1
    assert saved.source_checked_at == world.clock.now() and saved.created_at == original.created_at
    assert saved.selection.scan_input == original.selection.scan_input
