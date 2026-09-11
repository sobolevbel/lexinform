"""The inbox writer against a mock GitHub Contents API."""

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime

import httpx2 as httpx
import pytest

from lexinform.adapters.github_inbox import GitHubError, GitHubInboxWriter, GitHubUnavailableError
from lexinform.models import IncomingCommand

Handler = Callable[[httpx.Request], httpx.Response]
COMMAND = IncomingCommand(
    update_id=5,
    chat_id="-1001",
    message_id=42,
    text="/analyze 3039",
    received_at=datetime(2026, 9, 11, 8, 0, tzinfo=UTC),
)


def _writer(handler: Handler, sleeps: list[float] | None = None) -> GitHubInboxWriter:
    record = sleeps if sleeps is not None else []
    return GitHubInboxWriter(
        "owner/repo", "TOKEN", transport=httpx.MockTransport(handler), sleep=record.append
    )


def _github(request: httpx.Request) -> httpx.Response:
    """A GitHub that accepts the file and the dispatch."""
    if request.url.path.endswith("/dispatches"):
        return httpx.Response(204)
    return httpx.Response(201, json={"content": {"path": "inbox/5.json"}})


def test_put_creates_the_file_on_the_inbox_branch_and_starts_the_run() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _github(request)

    _writer(handler).put(COMMAND)

    put, dispatch = requests
    assert put.method == "PUT"
    assert put.url.path == "/repos/owner/repo/contents/inbox/5.json"
    assert put.headers["Authorization"] == "Bearer TOKEN"
    body = json.loads(put.content)
    assert body["branch"] == "inbox" and body["message"] == "inbox: /analyze 3039 (update 5)"
    filed = json.loads(base64.b64decode(body["content"]))
    assert filed["update_id"] == 5 and filed["text"] == "/analyze 3039"
    assert dispatch.method == "POST" and dispatch.url.path == "/repos/owner/repo/dispatches"
    assert json.loads(dispatch.content)["event_type"] == "inbox"


def test_a_file_that_exists_already_is_not_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/dispatches"):
            return httpx.Response(204)
        return httpx.Response(422, json={"message": '"sha" wasn\'t supplied.'})

    _writer(handler).put(COMMAND)  # the same update filed twice: fine


def test_a_failed_kick_is_not_an_error_the_file_is_what_counts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/dispatches"):
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(201, json={})

    _writer(handler).put(COMMAND)  # the next scheduled run answers it


def test_a_moved_branch_is_retried_and_a_bad_token_is_not() -> None:
    calls = {"n": 0}

    def conflict_then_ok(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/dispatches"):
            return httpx.Response(204)
        calls["n"] += 1
        return httpx.Response(409 if calls["n"] == 1 else 201, json={})

    sleeps: list[float] = []
    _writer(conflict_then_ok, sleeps).put(COMMAND)
    assert calls["n"] == 2 and sleeps == [2.0]

    with pytest.raises(GitHubError):
        _writer(lambda _: httpx.Response(401, json={"message": "Bad credentials"})).put(COMMAND)
    with pytest.raises(GitHubUnavailableError):
        _writer(lambda _: httpx.Response(503, json={})).put(COMMAND)
