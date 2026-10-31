"""Bounded logical object-store measurements for the benchmark API pod.

The API pod already has the configured S3 transport and credentials.  The
output intentionally contains aggregate sizes only: object keys, endpoints,
and credentials are never emitted.  ``S3Transport`` currently exposes only
``ListObjectsV2``, so retained versions and delete markers remain explicitly
unmeasured instead of being guessed from visible objects.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


# This is a reporting taxonomy, rather than a discovered list of keys.  Keep
# ``other`` so every currently visible object is counted without publishing a
# new key prefix in artifacts.
TOP_LEVEL_PREFIXES = ("_apps/", "_repo/", "_audit/", "_application_artifacts/", "apps/", "workflows/", ".bifrost/")


def _empty_totals() -> dict[str, int]:
    return {"object_count": 0, "object_bytes": 0}


def _prefix_for(key: object) -> str:
    if not isinstance(key, str):
        raise RuntimeError("S3 list response contained a non-string object key")
    return next((prefix for prefix in TOP_LEVEL_PREFIXES if key.startswith(prefix)), "other")


async def logical_objects() -> dict:
    """List visible current payloads and group them into a fixed safe taxonomy."""
    from src.config import get_settings
    from src.core.s3_transport import S3Transport

    settings = get_settings()
    if not settings.s3_bucket:
        raise RuntimeError("configured API S3 bucket is missing")
    prefixes = {prefix: _empty_totals() for prefix in (*TOP_LEVEL_PREFIXES, "other")}
    continuation: str | None = None
    async with S3Transport(
        endpoint_url=settings.s3_endpoint_url,
        access_key_id=settings.s3_access_key,
        secret_access_key=settings.s3_secret_key,
        region=settings.s3_region,
    ) as client:
        while True:
            arguments: dict[str, str] = {"Bucket": settings.s3_bucket}
            if continuation is not None:
                arguments["ContinuationToken"] = continuation
            page = await client.list_objects_v2(**arguments)
            contents = page.get("Contents", [])
            if not isinstance(contents, list):
                raise RuntimeError("S3 list response contained a non-list Contents field")
            for item in contents:
                if not isinstance(item, dict) or type(item.get("Size")) is not int or item["Size"] < 0:
                    raise RuntimeError("S3 list response contained an invalid object size")
                totals = prefixes[_prefix_for(item.get("Key"))]
                totals["object_count"] += 1
                totals["object_bytes"] += item["Size"]
            if not page.get("IsTruncated"):
                break
            continuation = page.get("NextContinuationToken")
            if not isinstance(continuation, str) or not continuation:
                raise RuntimeError("truncated S3 listing did not provide a continuation token")
    total = _empty_totals()
    for value in prefixes.values():
        total["object_count"] += value["object_count"]
        total["object_bytes"] += value["object_bytes"]
    return {
        "basis": "visible_current_logical_payload",
        "local_not_billable": True,
        "prefixes": prefixes,
        "total": total,
        "retained_versions": {
            "status": "unmeasured",
            "reason": "S3Transport exposes ListObjectsV2 only; version and delete-marker listing is unavailable",
        },
    }


async def retention_policy() -> dict:
    """Read the canonical policy, including its default, without exposing settings."""
    from src.core.database import get_db_context
    from src.services.artifact_retention import ArtifactRetentionSettingsService
    from src.services.audit_retention.settings import AuditRetentionSettingsService
    from src.services.run_retention.settings import RunRetentionSettingsService

    async with get_db_context() as db:
        policy = await RunRetentionSettingsService(db).get_settings()
        audit = await AuditRetentionSettingsService(db).get_settings()
        artifacts = await ArtifactRetentionSettingsService(db).get_settings()
    return {"run_retention": policy.model_dump(),
            "audit_retention": audit.model_dump(), "artifact_retention": artifacts.model_dump(),
            "basis": "canonical retention settings services; declared policies are not evidence that cleanup ran"}


def main() -> None:
    mode = sys.argv[1]
    # ``Cluster.exec_python`` writes JSON args to a file to avoid argv limits.
    json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    if mode not in {"logical-objects", "retention-policy"}:
        raise ValueError(f"unsupported storage metric mode {mode!r}")
    print(json.dumps(asyncio.run(logical_objects() if mode == "logical-objects" else retention_policy()), allow_nan=False))


if __name__ == "__main__":
    main()
