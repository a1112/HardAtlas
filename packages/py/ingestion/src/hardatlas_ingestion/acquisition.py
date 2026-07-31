import hashlib
import ipaddress
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from hardatlas_domain import (
    SourceDefinition,
    SourceSnapshot,
    evaluate_source_policy,
)


class AcquisitionError(ValueError):
    pass


@dataclass(frozen=True)
class AcquisitionResult:
    snapshot: SourceSnapshot
    content: bytes


def _validate_target(source: SourceDefinition, url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AcquisitionError("acquisition URL must use http or https")
    if parsed.username or parsed.password:
        raise AcquisitionError("acquisition URL must not include credentials")
    if parsed.query:
        raise AcquisitionError("acquisition URL must not include query parameters")
    if parsed.fragment:
        raise AcquisitionError("acquisition URL must not include fragment")
    hostname = parsed.hostname.casefold().rstrip(".")
    allowed = any(
        hostname == candidate or hostname.endswith(f".{candidate}")
        for candidate in source.allowed_hosts
    )
    if not allowed:
        raise AcquisitionError("acquisition URL host is not allow-listed")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        # hostnames that are not raw IP literals are validated later against allow-list only
        return
    if not address.is_global:
        raise AcquisitionError("acquisition URL host is blocked")


def validate_acquisition_url(source: SourceDefinition, url: str) -> None:
    _validate_target(source, url)


def acquire_http_source(
    source: SourceDefinition,
    *,
    url: str | None = None,
    client: httpx.Client | None = None,
) -> AcquisitionResult:
    decision = evaluate_source_policy(source)
    if not decision.allowed:
        raise AcquisitionError("; ".join(decision.blockers))
    target = url or source.base_url
    _validate_target(source, target)
    owns_client = client is None
    active_client = client or httpx.Client(timeout=20, follow_redirects=False)
    try:
        response = active_client.get(
            target,
            headers={
                "accept": ", ".join(source.allowed_media_types),
                "user-agent": "AtlasSourceMonitor/0.1 (+governed acquisition)",
            },
        )
    finally:
        if owns_client:
            active_client.close()
    if 300 <= response.status_code < 400:
        raise AcquisitionError("redirects require a new allow-list policy check")
    if response.status_code != 200:
        raise AcquisitionError(f"source returned HTTP {response.status_code}")
    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
    if media_type not in {item.casefold() for item in source.allowed_media_types}:
        raise AcquisitionError(f"media type is not allowed: {media_type or 'missing'}")
    content = response.content
    if len(content) > source.max_bytes:
        raise AcquisitionError(
            f"response exceeds source byte limit: {len(content)} > {source.max_bytes}"
        )
    digest = hashlib.sha256(content).hexdigest()
    snapshot = SourceSnapshot(
        id=f"snapshot-{source.id}-{digest[:16]}",
        source_id=source.id,
        source_version=source.version,
        url=target,
        content_sha256=digest,
        storage_key=f"sources/{source.id}/{digest}",
        media_type=media_type,
        byte_size=len(content),
        http_status=response.status_code,
        etag=response.headers.get("etag"),
        last_modified=response.headers.get("last-modified"),
        license_id=source.license_id,
        capture_status="captured",
    )
    return AcquisitionResult(snapshot=snapshot, content=content)
