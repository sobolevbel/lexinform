"""Live checks against api.sejm.gov.pl. Run with: uv run pytest -m integration"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.adapters.sejm_api import BILL_DOCUMENT_TYPE, SejmApiClient

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client() -> SejmApiClient:
    return SejmApiClient()


def test_modified_since_narrows_results(client: SejmApiClient) -> None:
    recent = list(
        client.iter_processes(
            10,
            modified_since=datetime.now(UTC) - timedelta(days=14),
            document_type=BILL_DOCUMENT_TYPE,
        )
    )
    assert 0 < len(recent) < 500
    assert all(p.document_type_enum.value == "BILL" for p in recent)


def test_print_3039_has_pdf_with_text(client: SejmApiClient) -> None:
    info = client.get_print(10, "3039")
    assert info.main_pdf is not None and info.main_pdf.name == "3039.pdf"
    text = PypdfTextExtractor().extract(client.download(info.main_pdf.url))
    assert len(text) > 1000


def test_process_detail_has_stages(client: SejmApiClient) -> None:
    detail = client.get_process(10, "1962")
    assert detail.passed is True and detail.last_stage is not None
