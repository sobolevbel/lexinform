from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lexinform.adapters.sejm_api import parse_print, parse_process_detail, parse_process_summary
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import PrintInfo, ProcessDetail, ProcessSummary

FIXTURES = Path(__file__).parent / "fixtures" / "sejm"


def load_json(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def repo() -> SqliteBillRepository:
    r = SqliteBillRepository(":memory:")
    r.migrate()
    return r


@pytest.fixture
def process_3039() -> ProcessDetail:
    return parse_process_detail(load_json("process_3039.json"))


@pytest.fixture
def process_1962() -> ProcessDetail:
    return parse_process_detail(load_json("process_1962.json"))


@pytest.fixture
def process_950() -> ProcessDetail:
    return parse_process_detail(load_json("process_950.json"))


@pytest.fixture
def print_3039() -> PrintInfo:
    return parse_print(load_json("print_3039.json"), term=10)


@pytest.fixture
def processes_page() -> list[ProcessSummary]:
    return [parse_process_summary(i) for i in load_json("processes_page.json")]


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 9, 7, 6, 0, tzinfo=UTC)


@pytest.fixture(scope="session")
def print_3039_pdf_text() -> str:
    from lexinform.adapters.pdf_text import PypdfTextExtractor

    return PypdfTextExtractor().extract((FIXTURES / "print_3039.pdf").read_bytes())
