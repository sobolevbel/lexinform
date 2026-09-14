"""The one external system of this project with no live check: the GitHub Contents API.

The relay's whole job is to put a file on the `inbox` branch through it, and everything that can
stop it is invisible from here — a rotated token, a branch someone deleted, a renamed repository.
Every existing test answers a `MockTransport`, so none of them would notice.

Read-only on purpose: a write would leave commits on the inbox branch every Monday, and what
actually breaks in production is access, not the PUT. Runs only when the token is in the
environment (`LEXINFORM_GITHUB_TOKEN`), so an ordinary checkout skips it.
"""

import httpx2 as httpx
import pytest

from lexinform.settings import Settings

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def github() -> httpx.Client:
    settings = Settings()
    if not settings.github_token or not settings.github_repo:
        pytest.skip("LEXINFORM_GITHUB_TOKEN / LEXINFORM_GITHUB_REPO are not set")
    return httpx.Client(
        base_url="https://api.github.com",
        timeout=30.0,
        headers={
            "Authorization": f"Bearer {settings.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def test_the_token_still_opens_the_repository_the_relay_writes_to(github: httpx.Client) -> None:
    settings = Settings()
    response = github.get(f"/repos/{settings.github_repo}")
    assert response.status_code == 200, response.text[:200]
    assert response.json()["full_name"].lower() == settings.github_repo.lower()


def test_the_inbox_branch_is_there_and_the_contents_api_answers_for_it(
    github: httpx.Client,
) -> None:
    """The relay writes `inbox/{update_id}.json` on this branch; a 404 here is a command lost
    with nothing but a log line to say so."""
    settings = Settings()
    branch = github.get(f"/repos/{settings.github_repo}/branches/{settings.inbox_branch}")
    assert branch.status_code == 200, branch.text[:200]

    listing = github.get(
        f"/repos/{settings.github_repo}/contents/inbox",
        params={"ref": settings.inbox_branch},
    )
    assert listing.status_code == 200, listing.text[:200]
    assert isinstance(listing.json(), list)


def test_filing_a_path_that_exists_is_answered_the_way_the_writer_reads_it(
    github: httpx.Client,
) -> None:
    """`GitHubInboxWriter` treats one 422 as "this command is filed already" and every other one
    as an error. The distinguishing wording is GitHub's, so it is checked against GitHub: a PUT
    without a `sha` to a path that exists must answer 422 with "sha" in its `message`."""
    settings = Settings()
    response = github.put(
        f"/repos/{settings.github_repo}/contents/inbox/.gitkeep",
        json={"message": "probe", "content": "", "branch": settings.inbox_branch},
    )
    assert response.status_code == 422, response.text[:200]
    assert "sha" in str(response.json().get("message", "")).lower()
