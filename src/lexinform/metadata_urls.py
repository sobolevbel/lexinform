"""Prospective metadata admission never changes requested URLs or historical URL validation."""

import hashlib
import ipaddress
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl, unquote, urlsplit

METADATA_URL_POLICY_VERSION: Literal["metadata-url-safety-v1"] = "metadata-url-safety-v1"
MAX_METADATA_URL_BYTES = 4096
MAX_METADATA_QUERY_FIELDS = 16
MetadataUrlOmissionReason = Literal[
    "invalid_unicode",
    "oversized_url",
    "control_character",
    "invalid_url",
    "userinfo",
    "fragment",
    "unrecognized_query",
    "opaque_failure_prose",
]

_BAD_PERCENT = re.compile(r"%(?![0-9a-fA-F]{2})")
_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_SEJM_PAGE = re.compile(
    r"/Sejm([1-9][0-9]{0,18})\.nsf/(agent|druk|przebiegproc|contact)\.xsp", re.I
)
_ISAP_ADDRESS = re.compile(r"(?:WDU|WMP)[0-9]{8,16}")
_RM_NUMBER = re.compile(r"RM(?:[-/][0-9]{1,8}){2,5}")
_PRINT_NUMBER = re.compile(r"[1-9][0-9]{0,18}(?:-[A-Za-z0-9]{1,8})?")
_SUBMISSION = re.compile(r"RPW/[1-9][0-9]{0,18}/[0-9]{4}")
_COMMITTEE = re.compile(r"[A-Z][A-Z0-9]{1,7}")


@dataclass(frozen=True)
class MetadataUrlDecision:
    requested_url_sha256: str | None
    reason: MetadataUrlOmissionReason | None = None

    @property
    def admitted(self) -> bool:
        return self.reason is None


def _unicode_control(value: str) -> bool:
    return any(unicodedata.category(char) in ("Cc", "Cf", "Zl", "Zp") for char in value)


def _encoded_control(value: str) -> bool:
    current = value
    while True:
        if _unicode_control(current):
            return True
        decoded = unquote(current)
        if decoded == current:
            return False
        current = decoded


def _valid_host(host: str) -> bool:
    if not host or "%" in host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    try:
        ascii_host = host.encode("idna").decode("ascii").rstrip(".")
    except UnicodeError:
        return False
    return (
        bool(ascii_host)
        and len(ascii_host) <= 253
        and all(_DNS_LABEL.fullmatch(label) is not None for label in ascii_host.split("."))
    )


def _public_rm(value: str) -> bool:
    return _RM_NUMBER.fullmatch("".join(value.split()).upper()) is not None


def _positive_integer(value: str) -> bool:
    return re.fullmatch(r"[1-9][0-9]{0,18}", value) is not None and int(value) < 2**63


def _sejm_query(path: str, query: dict[str, str]) -> bool:
    match = _SEJM_PAGE.fullmatch(path)
    if match is None or not _positive_integer(match[1]):
        return False
    endpoint = match[2].casefold()
    keys = set(query)
    if endpoint in ("druk", "przebiegproc"):
        return keys == {"nr"} and _PRINT_NUMBER.fullmatch(query["nr"]) is not None
    if endpoint == "contact":
        return keys == {"type"} and _COMMITTEE.fullmatch(query["type"]) is not None
    symbol = query.get("symbol")
    if symbol == "RPL":
        return keys == {"symbol", "Id"} and _public_rm(query["Id"])
    if symbol in ("KONSULTOWANY_PROJEKT", "KONSULTACJE_WYNIKI"):
        return (
            keys == {"symbol", "NrProjektu"}
            and _SUBMISSION.fullmatch(query["NrProjektu"]) is not None
        )
    if symbol == "KOMISJAST":
        return (
            keys == {"symbol", "NrKadencji", "KodKom"}
            and query["NrKadencji"] == match[1]
            and _COMMITTEE.fullmatch(query["KodKom"]) is not None
        )
    return False


def _public_query(host: str, path: str, query: dict[str, str]) -> bool:
    if host == "isap.sejm.gov.pl":
        return (
            path.casefold() == "/isap.nsf/docdetails.xsp"
            and set(query) == {"id"}
            and _ISAP_ADDRESS.fullmatch(query["id"]) is not None
        )
    if host == "legislacja.rcl.gov.pl":
        return (
            path == "/getIdFromLegislacja"
            and set(query) == {"number"}
            and _public_rm(query["number"])
        )
    if host in ("www.sejm.gov.pl", "sejm.gov.pl"):
        return _sejm_query(path, query)
    return False


def metadata_url_decision(value: str) -> MetadataUrlDecision:
    """Admitted values keep their original bytes; omission never authorizes a changed request."""
    digest = hashlib.sha256()
    size = 0
    try:
        for offset in range(0, len(value), 1024):
            encoded = value[offset : offset + 1024].encode()
            digest.update(encoded)
            size += len(encoded)
    except UnicodeEncodeError:
        return MetadataUrlDecision(None, "invalid_unicode")
    identity = digest.hexdigest()
    if size > MAX_METADATA_URL_BYTES:
        return MetadataUrlDecision(identity, "oversized_url")
    if _encoded_control(value):
        return MetadataUrlDecision(identity, "control_character")
    if (
        not value.lower().startswith(("http://", "https://"))
        or "\\" in value
        or _BAD_PERCENT.search(value)
    ):
        return MetadataUrlDecision(identity, "invalid_url")
    try:
        parsed = urlsplit(value)
        if parsed.username is not None or parsed.password is not None:
            return MetadataUrlDecision(identity, "userinfo")
        if (
            not _valid_host(parsed.hostname or "")
            or parsed.port is not None
            and not 0 < parsed.port < 65536
        ):
            return MetadataUrlDecision(identity, "invalid_url")
        if parsed.fragment:
            return MetadataUrlDecision(identity, "fragment")
        if not parsed.query:
            return MetadataUrlDecision(identity)
        if parsed.port not in (None, 443 if parsed.scheme == "https" else 80):
            return MetadataUrlDecision(identity, "unrecognized_query")
        pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            errors="strict",
            max_num_fields=MAX_METADATA_QUERY_FIELDS,
        )
    except ValueError, UnicodeError:
        return MetadataUrlDecision(identity, "invalid_url")
    if len({key for key, _ in pairs}) != len(pairs):
        return MetadataUrlDecision(identity, "unrecognized_query")
    if not _public_query(parsed.hostname or "", parsed.path, dict(pairs)):
        return MetadataUrlDecision(identity, "unrecognized_query")
    return MetadataUrlDecision(identity)
