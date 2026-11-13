"""File commands durably in the inbox branch and signal the shared Actions job."""

import base64
import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx2 as httpx

from lexinform.adapters.inbox_files import inbox_file_name
from lexinform.adapters.state_snapshot import MANIFEST_NAME
from lexinform.errors import ServiceUnavailableError
from lexinform.models import IncomingCommand

log = logging.getLogger(__name__)


class GitHubUnavailableError(ServiceUnavailableError):
    system = "GitHub API"


class GitHubError(RuntimeError):
    """A 4xx answer that a retry will not fix (bad token, wrong repository or branch)."""


def _asks_for_a_sha(response: httpx.Response) -> bool:
    """Whether a 422 means "this path is already there", which GitHub says as `"sha" wasn't
    supplied`.

    Asked of the `message` field and not the raw body, or any other 422 whose text contains "sha"
    is swallowed as "filed already" and the command vanishes with `put` reporting success.
    """
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and "sha" in str(body.get("message", "")).lower()


class GitHubInboxWriter:
    MAX_ATTEMPTS = 4
    DISPATCH_EVENT = "inbox"  # `on: repository_dispatch: types: [inbox]` in daily.yml

    def __init__(
        self,
        repo: str,
        token: str,
        *,
        branch: str = "inbox",
        directory: str = "inbox",
        base_url: str = "https://api.github.com",
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not repo or not token:
            raise ValueError("GitHub repository and token are required to write the inbox")
        self._repo = repo
        self._branch = branch
        self._dir = directory.strip("/")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        self._sleep = sleep

    def put(self, command: IncomingCommand) -> None:
        """File the command, then ask GitHub to run the workflow. Raises when the file could
        not be written; a failed kick is only logged."""
        self._file(command)
        try:
            self._kick(command)
        except (GitHubError, GitHubUnavailableError) as exc:
            log.warning(
                "inbox: filed update %d but could not start the run: %s", command.update_id, exc
            )

    def _kick(self, command: IncomingCommand) -> None:
        self.dispatch(self.DISPATCH_EVENT, {"update_id": command.update_id})
        log.info("inbox: run requested for update %d", command.update_id)

    def dispatch(self, event_type: str, client_payload: Mapping[str, Any] | None = None) -> None:
        """Wake the workflow with the Contents-scoped relay token."""
        payload = {"event_type": event_type, "client_payload": dict(client_payload or {})}
        try:
            response = self._client.post(f"/repos/{self._repo}/dispatches", json=payload)
        except httpx.TransportError as exc:
            raise GitHubUnavailableError(f"dispatch: {type(exc).__name__}") from exc
        if response.status_code == 204:
            return
        if response.status_code >= 500:
            raise GitHubUnavailableError(f"dispatch: HTTP {response.status_code}")
        raise GitHubError(f"dispatch: HTTP {response.status_code}: {response.text[:200]}")

    def _file(self, command: IncomingCommand) -> None:
        path = f"{self._dir}/{inbox_file_name(command)}"
        body = command.model_dump_json(indent=2) + "\n"
        payload: dict[str, Any] = {
            "message": f"inbox: {command.text[:60]} (update {command.update_id})",
            "content": base64.b64encode(body.encode("utf-8")).decode("ascii"),
            "branch": self._branch,
        }
        url = f"/repos/{self._repo}/contents/{path}"
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                response = self._client.put(url, json=payload)
            except httpx.TransportError as exc:
                if attempt == self.MAX_ATTEMPTS:
                    raise GitHubUnavailableError(f"PUT {path}: {type(exc).__name__}") from exc
                self._sleep(2.0 * attempt)
                continue
            if response.status_code in (200, 201):
                log.info("inbox: filed %s", path)
                return
            if response.status_code == 422 and _asks_for_a_sha(response):
                log.info("inbox: %s exists already", path)  # the same update filed twice
                return
            if response.status_code in (409, 429) or response.status_code >= 500:
                # 409: the branch moved under us (the workflow deleting handled files).
                if attempt == self.MAX_ATTEMPTS:
                    raise GitHubUnavailableError(f"PUT {path}: HTTP {response.status_code}")
                self._sleep(2.0 * attempt)
                continue
            raise GitHubError(f"PUT {path}: HTTP {response.status_code}: {response.text[:200]}")
        raise GitHubUnavailableError(f"PUT {path}: gave up after {self.MAX_ATTEMPTS} attempts")

    def close(self) -> None:
        self._client.close()

    def read_pending_batches(self, branch: str) -> str:
        try:
            response = self._client.get(
                f"/repos/{self._repo}/contents/{MANIFEST_NAME}",
                params={"ref": branch},
                headers={"Accept": "application/vnd.github.raw+json"},
            )
        except httpx.TransportError as exc:
            raise GitHubUnavailableError(f"read state: {type(exc).__name__}") from exc
        if response.status_code == 200:
            return response.text
        if response.status_code >= 500 or response.status_code == 429:
            raise GitHubUnavailableError(f"read state: HTTP {response.status_code}")
        raise GitHubError(f"read state: HTTP {response.status_code}")
