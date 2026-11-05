from tools.replay_amendment_inputs import snapshot

from tests.unit.test_input_receipts import SOURCE
from tests.unit.test_physical_extraction import docx


def test_public_ordinary_and_correction_prepare_once_with_corroborated_receipts() -> None:
    report = {
        "url": "https://api.sejm.gov.pl/sejm/term10/prints/69-A/69-A.pdf",
        "stage_observations": [
            {
                "owner_print": "31",
                "stage": {
                    "stage_type": "CommitteeReport",
                    "stage_name": "Sprawozdanie komisji",
                    "print_number": "69-A",
                    "proposal": "przyjąć poprawki",
                },
            }
        ],
    }
    for correction in (False, True):
        result = snapshot(docx(SOURCE * 30), report, correction=correction)
        core = result["core"]
        assert core["prepare_count"] == core["queued_count"] == 1
        assert core["waiting"] and core["exception_type"] is None
        assert len(core["downloads"]) == 1 and core["calls"] == []
        assert result["receipt"]["profile"] == "amendment-single-part-text-v1"
        assert core["text_chars"] > 500 and core["payload_sha256"]
