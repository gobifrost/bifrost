"""Direct backup downloads authenticate with both response header overrides."""
from uuid import uuid4

import httpx
import pytest

from src.services.file_storage import FileStorageService


@pytest.mark.e2e
async def test_signed_download_serves_bytes_with_attachment_headers(db_session):
    storage = FileStorageService(db_session)
    key = f"solution-exports/tests/{uuid4()}/backup.zip"
    content = b"PK\x03\x04synthetic backup bytes"
    await storage.write_raw_to_s3(key, content)
    try:
        url = await storage.generate_presigned_download_url(
            key,
            response_content_type="application/zip",
            response_content_disposition='attachment; filename="backup.zip"',
        )
        async with httpx.AsyncClient() as client:
            response = await client.get(url)
        assert response.status_code == 200
        assert response.content == content
        assert response.headers["content-type"] == "application/zip"
        assert response.headers["content-disposition"] == 'attachment; filename="backup.zip"'
    finally:
        await storage.delete_raw_from_s3(key)
