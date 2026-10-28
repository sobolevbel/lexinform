import pytest

from lexinform.errors import SejmApiUnavailableError
from lexinform.models import DocumentPart, DocumentSelection, TextDocument
from lexinform.models.document_reads import LoadedFile
from lexinform.sections import PAGE_BREAK
from lexinform.services.documents import TextLoader
from tests.fakes import FakeSejmGateway, FakeTextExtractor
from tests.scenario.test_document_selection_contract import law

ROOT = "https://api.test/"


def file(text: str | None, role: str = "bill", *, pages: int = 0) -> LoadedFile:
    part = DocumentPart.model_validate({"role": role, "text": text or ""})
    return LoadedFile(text, pages=pages, selection=DocumentSelection(parts=(part,)))


def snapshots(
    monkeypatch: pytest.MonkeyPatch, values: dict[str, LoadedFile | Exception]
) -> tuple[TextLoader, list[str]]:
    loader = TextLoader({}, FakeTextExtractor(), max_bytes=100_000)
    calls: list[str] = []

    def read(url: str) -> LoadedFile:
        calls.append(url)
        outcome = values[url]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(loader, "read", read)
    return loader, calls


def document() -> TextDocument:
    return TextDocument(
        url=ROOT + "main",
        kind="rcl",
        required_urls=(ROOT + "required",),
        normative_urls=(ROOT + "annex2", ROOT + "annex1"),
        amendment_urls=(ROOT + "amendment",),
        extra_urls=(ROOT + "support",),
        version_basis=("official/project/1",),
    )


def values() -> dict[str, LoadedFile | Exception]:
    return {
        ROOT + "main": file(law()),
        ROOT + "required": file("Art. 4. Kolejny przepis."),
        ROOT + "annex2": file("Załącznik nr 2 do projektu ustawy\nWYKAZ 200 zł"),
        ROOT + "annex1": file("Załącznik nr 1 do projektu ustawy\nWYKAZ 100 zł"),
        ROOT + "amendment": file("AUTOPOPRAWKA\nW art. 1 dodaje się wyraz."),
        ROOT + "support": file("UZASADNIENIE\nCel ustawy.", "justification"),
    }


def test_loose_file_order_and_direct_review_finalization_are_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader, calls = snapshots(monkeypatch, values())

    loaded = loader.read_document(document())

    assert calls == list(values())
    assert loaded.text is not None, "all required snapshots contain their existing text"
    assert loaded.text.index("WYKAZ 100") < loaded.text.index("WYKAZ 200")
    assert "AUTOPOPRAWKA" not in loaded.text
    assert loaded.text.endswith(PAGE_BREAK + "UZASADNIENIE\nCel ustawy.")
    assert tuple(part.role for part in loaded.selection.parts) == (
        "bill",
        "normative_continuation",
        "normative_annex",
        "normative_annex",
        "self_amendment",
        "justification",
    )
    assert loaded.selection.version == document().version_basis
    assert loaded.selection.state == "unverified" and not loaded.selection.facts
    assert loaded.selection.policy_version is None


@pytest.mark.parametrize("stage", ["main", "required", "annex2", "amendment"])
def test_terminal_snapshot_stops_all_later_reads(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    inputs = values()
    inputs[ROOT + stage] = file(None)
    loader, calls = snapshots(monkeypatch, inputs)

    loaded = loader.read_document(document())

    assert calls == list(inputs)[: list(inputs).index(ROOT + stage) + 1]
    assert loaded.text is None
    assert loaded.selection.state == ("unverified" if stage == "main" else "waiting")


@pytest.mark.parametrize("stage", ["required", "annex2", "amendment", "support"])
@pytest.mark.parametrize("outage", [False, True])
def test_required_and_optional_read_failures_keep_their_distinct_contracts(
    monkeypatch: pytest.MonkeyPatch, stage: str, outage: bool
) -> None:
    inputs = values()
    error = SejmApiUnavailableError("host unavailable") if outage else ValueError("invalid file")
    inputs[ROOT + stage] = error
    loader, calls = snapshots(monkeypatch, inputs)

    if outage or stage in ("required", "amendment"):
        with pytest.raises(type(error), match=str(error)):
            loader.read_document(document())
    else:
        loaded = loader.read_document(document())
        assert loaded.selection.state == ("waiting" if stage == "annex2" else "unverified")
        assert loaded.selection.rejected == (
            ROOT
            + stage
            + (": normative annex unavailable" if stage == "annex2" else ": ValueError"),
        )
    assert calls == list(inputs)[: list(inputs).index(ROOT + stage) + 1]


def test_compare_probe_cache_reads_and_pre_download_conflict_guard_stay_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second, foreign = (ROOT + name for name in ("old", "new", "foreign"))
    gateway = FakeSejmGateway(files={first: b"old", second: b"new"})
    loader = TextLoader(
        {"api.test": gateway.download},
        FakeTextExtractor(by_content={b"old": law(), b"new": law(day=2)}, page_count=0),
        max_bytes=100_000,
    )
    calls: list[str] = []
    original_read = loader.read

    def read(url: str) -> LoadedFile:
        calls.append(url)
        return original_read(url)

    monkeypatch.setattr(loader, "read", read)
    source = TextDocument(
        url=first,
        kind="rcl",
        compare_versions=True,
        alternatives=(
            TextDocument(url=foreign, kind="rcl", official_link_conflict=True),
            TextDocument(url=second, kind="rcl"),
        ),
    )

    selected, loaded = loader.select_document(source)

    assert selected.url == second and loaded.text == law(day=2)
    assert calls == [first, second, second]
    assert gateway.calls == ["download:" + first, "download:" + second]
    assert loader.read_metrics().cache_hits == 1
    assert loaded.selection.policy_version is not None


@pytest.mark.parametrize("multiple", [False, True])
@pytest.mark.parametrize("invalid", [False, True])
def test_fallback_rethrows_only_the_sole_main_error(
    monkeypatch: pytest.MonkeyPatch, multiple: bool, invalid: bool
) -> None:
    error = ValueError("invalid") if invalid else RuntimeError("missing")
    source = TextDocument(url=ROOT + "main", kind="print", compare_versions=multiple)
    loader, calls = snapshots(monkeypatch, {source.url: error})

    if multiple:
        _, loaded = loader.select_document(source)
        assert calls == [source.url, source.url]
        assert loaded.selection.state == ("unsupported" if invalid else "waiting")
        assert loaded.selection.rejected == (f"{source.url}: {type(error).__name__}: {error}",)
    else:
        with pytest.raises(type(error), match=str(error)):
            loader.select_document(source)
        assert calls == [source.url]
