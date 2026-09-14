"""The inbox writer against a mock GitHub Contents API."""

import base64
import functools
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


def _refusal(request: httpx.Request, *, status: int) -> httpx.Response:
    return httpx.Response(status, json={"message": "Not Found"})


def test_start_run_dispatches_the_workflow_with_its_inputs() -> None:
    """`/run` is not filed anywhere: the relay asks GitHub to run `daily.yml` on main."""
    seen: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), json.loads(request.content)))
        return httpx.Response(204)

    where = _writer(handler).start_run({"since": "2026-09-01", "dry_run": "true"})

    ((url, payload),) = seen
    assert url.endswith("/repos/owner/repo/actions/workflows/daily.yml/dispatches")
    assert payload == {"ref": "main", "inputs": {"since": "2026-09-01", "dry_run": "true"}}
    assert where == "https://github.com/owner/repo/actions/workflows/daily.yml"


def test_a_token_without_actions_says_which_scope_is_missing() -> None:
    """403 and 404 mean the same thing here — a token that may not see Actions — and the
    operator cannot tell them apart from the reply, so both name the scope."""
    for status in (403, 404):
        refuse = functools.partial(_refusal, status=status)

        with pytest.raises(GitHubError, match="Actions: read and write"):
            _writer(refuse).start_run({})


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


def test_another_422_is_an_error_and_not_a_silently_dropped_command() -> None:
    """The check used to be a substring test on the whole body: any 422 whose text happened to
    carry "sha" was read as "filed already", `put` returned success, the relay moved its offset
    past the command and nothing was left to notice it had gone."""
    body = {"message": "Validation failed", "errors": [{"resource": "Commit", "field": "sha"}]}
    with pytest.raises(GitHubError):
        _writer(lambda _: httpx.Response(422, json=body)).put(COMMAND)


def test_a_422_that_is_not_json_is_an_error_too() -> None:
    with pytest.raises(GitHubError):
        _writer(lambda _: httpx.Response(422, text='"sha" wasn\'t supplied.')).put(COMMAND)


def test_a_transport_error_while_filing_ends_the_phase() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    sleeps: list[float] = []
    with pytest.raises(GitHubUnavailableError):
        _writer(refuse, sleeps).put(COMMAND)
    assert sleeps == [2.0, 4.0, 6.0]


def test_a_branch_that_keeps_moving_ends_the_phase_rather_than_losing_the_command() -> None:
    sleeps: list[float] = []
    with pytest.raises(GitHubUnavailableError):
        _writer(lambda _: httpx.Response(409, json={}), sleeps).put(COMMAND)
    assert len(sleeps) == GitHubInboxWriter.MAX_ATTEMPTS - 1
