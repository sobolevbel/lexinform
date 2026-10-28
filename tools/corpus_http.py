"""Fetch bounded public source bytes with explicit hosts, redirects and response provenance."""

import datetime as dt
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx2 as httpx

from tools.corpus_artifacts import digest

PUBLIC_HOSTS = frozenset(("legislacja.rcl.gov.pl", "api.sejm.gov.pl", "orka.sejm.gov.pl"))


@dataclass(frozen=True)
class SourceResponse:
    data: bytes
    provenance: dict[str, str | int | None]


def receipt_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        authority = parsed.hostname or ""
        if parsed.port is not None:
            authority += f":{parsed.port}"
        return parsed._replace(netloc=authority, query="", fragment="").geturl()[:2048]
    except ValueError:
        return "invalid-public-url"


def fetch(
    client: httpx.Client,
    url: str,
    *,
    max_bytes: int = 32_000_000,
    chunk_size: int | None = None,
    strict: bool = False,
    receipt: dict[str, Any] | None = None,
) -> SourceResponse:
    current, started = url, time.monotonic()
    observed = receipt if receipt is not None else {}
    observed.pop("sha256", None)
    observed.pop("final_query_sha256", None)
    observed.update(
        complete=False,
        body_started=False,
        bytes_read=0,
        requested_url=receipt_url(url) if strict else url[:2048],
        redirects=[],
    )
    phase = "url"
    try:
        if max_bytes < 1 or (chunk_size is not None and chunk_size < 1):
            raise ValueError("Source byte and chunk limits must be positive")
        for _ in range(6):
            parsed = urlsplit(current)
            if (
                parsed.scheme != "https"
                or parsed.hostname not in PUBLIC_HOSTS
                or parsed.username
                or parsed.password
                or (strict and parsed.port not in (None, 443))
                or (strict and len(current) > 2048)
            ):
                raise ValueError("Source URL must be an allowlisted public HTTPS address")
            safe_url = receipt_url(current) if strict else current
            observed.pop("final_query_sha256", None)
            observed["final_url"] = safe_url
            if strict and parsed.query:
                observed["final_query_sha256"] = digest(parsed.query.encode())
            if time.monotonic() - started > 60:
                phase = "time_limit"
                raise TimeoutError("Source exceeds the download time limit")
            phase = "transport"
            with client.stream(
                "GET",
                current,
                follow_redirects=False,
                headers={"Accept-Encoding": "identity"} if strict else None,
            ) as response:
                observed.update(
                    status_code=response.status_code,
                    headers={
                        name: response.headers[name][:256]
                        for name in (
                            "content-type",
                            "content-length",
                            "content-range",
                            "content-encoding",
                            "etag",
                            "last-modified",
                        )
                        if name in response.headers
                    },
                )
                observed["redirects"].append({"url": safe_url, "status_code": response.status_code})
                if response.is_redirect:
                    phase = "url"
                    current = urljoin(current, response.headers["location"])
                    continue
                phase = "http_status"
                response.raise_for_status()
                if response.status_code != 200:
                    raise ValueError("Source requires a complete HTTP 200 response")
                phase = "response_headers"
                length = response.headers.get("content-length")
                if strict and (
                    "content-range" in response.headers
                    or response.headers.get("content-encoding", "identity").lower()
                    not in ("", "identity")
                ):
                    raise ValueError("Source range or encoding is unsupported")
                if (
                    strict
                    and length is not None
                    and (not length.isdecimal() or int(length) > max_bytes)
                ):
                    raise ValueError("Source length exceeds the download byte limit")
                data = bytearray()
                observed["body_started"] = True
                phase = "body"
                for chunk in response.iter_bytes(chunk_size=chunk_size):
                    observed["bytes_read"] += len(chunk)
                    if len(data) + len(chunk) > max_bytes:
                        phase = "byte_limit"
                        raise ValueError("Source exceeds the download byte limit")
                    data.extend(chunk)
                    if time.monotonic() - started > 60:
                        phase = "time_limit"
                        raise TimeoutError("Source exceeds the download time limit")
                if strict and length is not None and len(data) != int(length):
                    phase = "incomplete_body"
                    raise ValueError("Source body differs from Content-Length")
                raw = bytes(data)
                provenance: dict[str, str | int | None] = {
                    "requested_url": receipt_url(url) if strict else url,
                    "final_url": safe_url,
                    "status_code": response.status_code,
                    "content_type": response.headers.get("content-type"),
                    "etag": response.headers.get("etag"),
                    "last_modified": response.headers.get("last-modified"),
                    "fetched_at": dt.datetime.now(dt.UTC).isoformat(),
                    "sha256": digest(raw),
                    "bytes": len(raw),
                }
                if strict:
                    for name in ("content_type", "etag", "last_modified"):
                        value = provenance[name]
                        if isinstance(value, str):
                            provenance[name] = value[:256]
                observed.update(
                    complete=True, sha256=provenance["sha256"], fetched_at=provenance["fetched_at"]
                )
                return SourceResponse(raw, provenance)
        phase = "redirect_limit"
        raise ValueError("Source exceeds the redirect limit")
    except Exception as exc:
        observed.update(failure_category=phase, exception_type=type(exc).__name__[:128])
        raise
    finally:
        observed["elapsed_seconds"] = round(time.monotonic() - started, 6)


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
