"""Shared fixtures: recorded Sejm API responses and an in-memory repository."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.adapters.sejm_api import parse_print, parse_process_detail, parse_process_summary
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import PrintInfo, ProcessDetail, ProcessSummary

FIXTURES = Path(__file__).parent / "fixtures" / "sejm"


def load_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def repo() -> SqliteBillRepository:
    repository = SqliteBillRepository(":memory:")
    repository.migrate()
    return repository


@pytest.fixture
def process_3039() -> ProcessDetail:
    """A deputies' bill referred to the ASW committee (first reading pending)."""
    return parse_process_detail(load_json("process_3039.json"))


@pytest.fixture
def process_1962() -> ProcessDetail:
    """A bill that went through the Senate with amendments and was adopted."""
    return parse_process_detail(load_json("process_1962.json"))


@pytest.fixture
def process_950() -> ProcessDetail:
    """A bill with its first reading at a plenary sitting."""
    return parse_process_detail(load_json("process_950.json"))


@pytest.fixture
def print_3039() -> PrintInfo:
    return parse_print(load_json("print_3039.json"), term=10)


@pytest.fixture
def processes_page() -> list[ProcessSummary]:
    return [parse_process_summary(item) for item in load_json("processes_page.json")]


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 9, 7, 6, 0, tzinfo=UTC)


@pytest.fixture(scope="session")
def print_3039_pdf_text() -> str:
    return PypdfTextExtractor().extract((FIXTURES / "print_3039.pdf").read_bytes())
