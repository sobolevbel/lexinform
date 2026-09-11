"""The inbox branch written from outside git: one file per command through the GitHub
Contents API (`PUT /repos/{owner}/{repo}/contents/{path}`), which commits and pushes in one
call. Pushes made with a personal access token start the `on: push` workflow; the workflow's
own commits (GITHUB_TOKEN) do not, so the run that deletes the files starts no run.
"""

import base64
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx2 as httpx

from lexinform.adapters.inbox_files import inbox_file_name
from lexinform.errors import ServiceUnavailableError
from lexinform.models import IncomingCommand

log = logging.getLogger(__name__)


class GitHubUnavailableError(ServiceUnavailableError):
    system = "GitHub API"


class GitHubError(RuntimeError):
    """A 4xx answer that a retry will not fix (bad token, wrong repository or branch)."""


class GitHubInboxWriter:
    MAX_ATTEMPTS = 4

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
            if response.status_code == 422 and "sha" in response.text:
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
