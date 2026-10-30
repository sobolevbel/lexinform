import pytest

from tests.harness import RCL, TERM, World, print_url, rcl_project
from tests.scenario.test_explain import commands_only


@pytest.mark.parametrize("source", ["3039", RCL])
def test_private_explanation_unsafe_source_retains_reference_without_enrolling_bill(
    source: str,
) -> None:
    world = World()
    if source == "3039":
        world.add_bill(source, "Projekt ustawy o cudzoziemcach")
        original = world.gateway.prints[source]
        url = print_url(source) + "?token=PRIVATE"
        world.gateway.prints[source] = original.model_copy(
            update={
                "attachments": (original.attachments[0].model_copy(update={"url": url}),),
            }
        )
        world.gateway.files[url] = b"%PDF"
    else:
        project = rcl_project(consultation=None)
        main = project.text_documents()["bill"]
        stem, extension = main.url.rsplit(".", 1)
        url = f"{stem}%0A_PRIVATE.{extension}"
        altered = project.model_copy(
            update={
                "stages": tuple(
                    stage.model_copy(
                        update={
                            "folders": tuple(
                                folder.model_copy(
                                    update={
                                        "documents": tuple(
                                            document.model_copy(update={"url": url})
                                            if document.id == main.id
                                            else document
                                            for document in folder.documents
                                        )
                                    }
                                )
                                for folder in stage.folders
                            )
                        }
                    )
                    for stage in project.stages
                )
            }
        )
        world.add_rcl_project(altered)
    world.command(f"/explain {source}")

    report = commands_only(world)

    assert report.commands_failed == 0 and len(world.batch.submitted) == 1
    assert world.repo.get(TERM, source) is None and not world.publisher.new_bills
    facts = world.repo.list_source_metadata_omissions()
    assert facts and all(fact.owner.kind == "source_reference" for fact in facts)
    assert all(
        fact.owner.number == source for fact in facts if fact.owner.kind == "source_reference"
    )
    assert all("PRIVATE" not in fact.model_dump_json() for fact in facts)
    assert any(fact.parent_count > 0 for fact in facts)
    assert not world.repo.list_source_observations(TERM, source)
