"""Object storage owned by one ad-hoc Agent Skill.

Uploaded Skills must not mutate the git-backed ``_repo/`` workspace. Their
files live under ``_agent_skills/{agent_id}/`` instead. Solution-managed Agents
continue to use ``SolutionStorage`` because their bundle paths are relative to
the Solution workspace root and are replaced only by deploy.
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from uuid import UUID

from src.config import Settings, get_settings
from src.services.repo_storage import _get_shared_session

AGENT_SKILLS_ROOT = "_agent_skills"


class AgentSkillStorage:
    """S3 storage scoped to one non-Solution Agent."""

    def __init__(self, agent_id: UUID | str, settings: Settings | None = None):
        self.agent_id = str(agent_id)
        self.prefix = f"{AGENT_SKILLS_ROOT}/{self.agent_id}/"
        self._settings = settings or get_settings()
        self._bucket = self._settings.s3_bucket or ""

    @asynccontextmanager
    async def _get_client(self):
        session = _get_shared_session()
        async with session.create_client(
            "s3",
            endpoint_url=self._settings.s3_endpoint_url,
            aws_access_key_id=self._settings.s3_access_key,
            aws_secret_access_key=self._settings.s3_secret_key,
            region_name=self._settings.s3_region,
        ) as client:
            yield client

    def _key(self, path: str) -> str:
        return f"{self.prefix}{path.lstrip('/')}"

    async def write(self, path: str, content: bytes) -> str:
        content_hash = hashlib.sha256(content).hexdigest()
        async with self._get_client() as client:
            await client.put_object(
                Bucket=self._bucket,
                Key=self._key(path),
                Body=content,
            )
        return content_hash

    async def read(self, path: str) -> bytes:
        async with self._get_client() as client:
            response = await client.get_object(
                Bucket=self._bucket,
                Key=self._key(path),
            )
            return await response["Body"].read()

    async def delete(self, path: str) -> None:
        async with self._get_client() as client:
            await client.delete_object(Bucket=self._bucket, Key=self._key(path))

    async def list(self, prefix: str = "") -> list[str]:
        full_prefix = self._key(prefix)
        strip = len(self.prefix)
        paths: list[str] = []
        continuation_token = None
        async with self._get_client() as client:
            while True:
                kwargs: dict = {"Bucket": self._bucket, "Prefix": full_prefix}
                if continuation_token:
                    kwargs["ContinuationToken"] = continuation_token
                response = await client.list_objects_v2(**kwargs)
                for obj in response.get("Contents", []):
                    key = obj.get("Key")
                    if key:
                        paths.append(key[strip:])
                if not response.get("IsTruncated"):
                    break
                continuation_token = response.get("NextContinuationToken")
        return paths

    async def clear(self) -> None:
        for path in await self.list():
            await self.delete(path)
