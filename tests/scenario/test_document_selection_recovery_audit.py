import pytest

from tests.scenario.test_senate_document_selection import FIXTURES, world_with_sources


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="B104: a source that appears after the stage checkpoint is never selected again",
)
def test_late_senate_attachment_is_revisited_without_another_stage_change() -> None:
    resolution = (FIXTURES / "senate_3213_resolution.txt").read_text()
    world = world_with_sources({"3213.docx": resolution})
    del world.gateway.files["https://api.test/3213.docx"]

    world.run()
    assert not world.llm.amendment_contexts
    world.gateway.files["https://api.test/3213.docx"] = b"3213.docx"
    world.clock.advance(days=1)
    world.run()

    assert len(world.llm.amendment_contexts) == 1
    assert world.llm.amendment_contexts[0].text == resolution
