"""Public, immutable delivery of the browser App SDK factory."""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from shared.models import HostedAppSdkDescriptor
from src.services.sdk_package import hosted_sdk_manifest, read_hosted_sdk_asset

router = APIRouter(prefix="/api/app-sdk", tags=["App SDK"])

_FINGERPRINT = re.compile(r"^[a-f0-9]{64}$")


@router.get("/v1/manifest", response_model=HostedAppSdkDescriptor)
async def get_manifest(response: Response) -> HostedAppSdkDescriptor:
    """Select the API instance's current factory without caching the pointer."""
    response.headers["Cache-Control"] = "no-store"
    return hosted_sdk_manifest()


@router.get("/assets/{fingerprint}.js", response_class=Response)
async def get_asset(fingerprint: str) -> Response:
    """Serve an exact immutable factory artifact from shared platform storage."""
    if not _FINGERPRINT.fullmatch(fingerprint):
        raise HTTPException(status_code=404, detail="App SDK artifact not found")
    try:
        content = await read_hosted_sdk_asset(fingerprint)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="App SDK artifact not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="App SDK artifact integrity check failed") from exc
    return Response(
        content=content,
        media_type="application/javascript",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
