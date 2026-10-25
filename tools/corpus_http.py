"""Fetch bounded public source bytes with explicit hosts, redirects and response provenance."""

import datetime as dt
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx2 as httpx

from tools.corpus_artifacts import digest

PUBLIC_HOSTS = frozenset(("legislacja.rcl.gov.pl", "api.sejm.gov.pl"))


@dataclass(frozen=True)
class SourceResponse:
    data: bytes
    provenance: dict[str, str | int | None]


def fetch(client: httpx.Client, url: str, *, max_bytes: int = 32_000_000) -> SourceResponse:
    current, started = url, time.monotonic()
    for _ in range(6):
        parsed = urlsplit(current)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in PUBLIC_HOSTS
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Source URL must be an allowlisted public HTTPS address")
        with client.stream("GET", current, follow_redirects=False) as response:
            if response.is_redirect:
                current = urljoin(current, response.headers["location"])
                continue
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError("Source requires a complete HTTP 200 response")
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise ValueError("Source exceeds the download byte limit")
                if time.monotonic() - started > 60:
                    raise TimeoutError("Source exceeds the download time limit")
            raw = bytes(data)
            return SourceResponse(
                raw,
                {
                    "requested_url": url,
                    "final_url": current,
                    "status_code": response.status_code,
                    "content_type": response.headers.get("content-type"),
                    "etag": response.headers.get("etag"),
                    "last_modified": response.headers.get("last-modified"),
                    "fetched_at": dt.datetime.now(dt.UTC).isoformat(),
                    "sha256": digest(raw),
                    "bytes": len(raw),
                },
            )
    raise ValueError("Source exceeds the redirect limit")


def attachment_extension(data: bytes, declared: str) -> str:
    head = data[:1024].lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html")) or b"<html" in head:
        raise ValueError("HTML instead of an attachment")
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return declared if declared in ("docx", "docm", "odt") else "zip"
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "doc"
    if head.startswith((b"<?xml", b"<w:worddocument", b"<pkg:package")):
        return "xml"
    raise ValueError("Unrecognized attachment signature")
