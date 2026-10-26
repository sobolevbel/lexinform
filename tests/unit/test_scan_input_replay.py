import gzip
import io
import json
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject
from tools.corpus_artifacts import digest
from tools.replay_scan_inputs import inventory, mutation_probes, page_signatures, run


def test_real_pdf_replay_checks_maps_mutations_pins_and_changed_raw_sources(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    (root / "sejm/term10/prints").mkdir(parents=True)
    (root / "sejm/term10/files").mkdir()
    (root / "documents").mkdir()
    (root / "documents/index.json").write_text("{}")
    (root / "page_raster.json.gz").write_bytes(gzip.compress(b'{"files":{}}'))
    (root / "sejm/term10/prints/125.json").write_text(
        '{"number":"125","attachments":[{"name":"alternative.pdf"}]}'
    )
    import_data = tmp_path / "manifest.json"
    writer, data = PdfWriter(), io.BytesIO()
    writer.add_blank_page(width=612, height=792)
    writer.add_blank_page(width=612, height=792)
    writer.write(data)
    source = root / "sejm/term10/files/125__alternative.pdf"
    source.write_bytes(data.getvalue())
    inventory(root, import_data)
    pin = digest(import_data.read_bytes())
    first, second = tmp_path / "first.json", tmp_path / "second.json"

    assert run(root, import_data, pin, first, None, None) == 0
    assert run(root, import_data, pin, second, first, digest(first.read_bytes())) == 0
    result = json.loads(second.read_bytes())
    assert result["status"] == "passed" and not result["changes"]
    assert len(result["scan_manifests"]) == 2
    assert all(mutation_probes(data.getvalue()).values())
    with pytest.raises(ValueError, match="inventory SHA"):
        run(root, import_data, "0" * 64, tmp_path / "wrong.json", None, None)
    source.write_bytes(b"changed source")
    assert run(root, import_data, pin, tmp_path / "changed.json", None, None) == 1


def test_encoded_jbig2_streams_are_compared_without_an_external_decoder() -> None:
    signatures = []
    for payload in (b"opaque JBIG2 stream", b"different JBIG2 stream"):
        writer, output = PdfWriter(), io.BytesIO()
        page = writer.add_blank_page(width=612, height=792)
        image = DecodedStreamObject()
        image.set_data(payload)
        image.update(
            {
                NameObject("/Filter"): NameObject("/JBIG2Decode"),
                NameObject("/Subtype"): NameObject("/Image"),
                NameObject("/Width"): NumberObject(1),
                NameObject("/Height"): NumberObject(1),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/XObject"): DictionaryObject({NameObject("/Scan"): image})}
        )
        writer.write(output)
        signatures.append(page_signatures(output.getvalue()))

    assert signatures[0] != signatures[1]
