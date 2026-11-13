"""
Distributed Lock Service

Provides Redis-based distributed locks for exclusive operations.
Used primarily for upload locking to prevent concurrent uploads.

Lock Flow:
1. Client attempts to acquire lock with owner info
2. If lock exists and not owned by client, return existing lock info
3. If lock acquired, set TTL for auto-release on crash
4. On completion (success or failure), release the lock
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import redis.asyncio as redis

from src.config import get_settings

logger = logging.getLogger(__name__)

# Redis key prefix
LOCK_KEY_PREFIX = "bifrost:lock:"

# Default lock TTL (5 minutes safety timeout)
DEFAULT_LOCK_TTL_SECONDS = 300


@dataclass
class LockInfo:
    """Information about a lock."""

    owner_user_id: str
    owner_email: str
    operation: str
    locked_at: datetime
    expires_at: datetime

    def to_dict(self) -> dict:
        return {
            "owner_user_id": self.owner_user_id,
            "owner_email": self.owner_email,
            "operation": self.operation,
            "locked_at": self.locked_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LockInfo":
        return cls(
            owner_user_id=data["owner_user_id"],
            owner_email=data["owner_email"],
            operation=data["operation"],
            locked_at=datetime.fromisoformat(data["locked_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
        )


class DistributedLockService:
    """
    Service for distributed locking using Redis.

    Provides:
    - Acquire/release named locks
    - Lock with owner info (for visibility)
    - TTL-based auto-release on crash
    - Check lock status without acquiring
    """

    def __init__(self):
        self._redis: redis.Redis | None = None

    async def _get_redis(self) -> redis.Redis:
        """Get or create Redis connection."""
        if self._redis is None:
            settings = get_settings()
            self._redis = redis.from_url(
                settings.redis_url,
                decode_responses=True,
            )
        return self._redis

    async def acquire_lock(
        self,
        lock_name: str,
        owner_user_id: str,
        owner_email: str,
        operation: str,
        ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
    ) -> tuple[bool, LockInfo | None]:
        """
        Attempt to acquire a named lock.

        Uses Redis SETNX for atomic lock acquisition.

        Args:
            lock_name: Unique name for the lock (e.g., "upload")
            owner_user_id: User ID acquiring the lock
            owner_email: Email of user acquiring the lock
            operation: Description of the operation
            ttl_seconds: Lock TTL for auto-release on crash

        Returns:
            Tuple of (success, existing_lock_info)
            - (True, None) if lock acquired
            - (False, LockInfo) if lock held by someone else
        """
        redis_client = await self._get_redis()
        key = f"{LOCK_KEY_PREFIX}{lock_name}"
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl_seconds)

        lock_info = LockInfo(
            owner_user_id=owner_user_id,
            owner_email=owner_email,
            operation=operation,
            locked_at=now,
            expires_at=expires_at,
        )

        try:
            # Attempt atomic set-if-not-exists
            acquired = await redis_client.setnx(key, json.dumps(lock_info.to_dict()))

            if acquired:
                # Set TTL for safety (auto-release on crash)
                await redis_client.expire(key, ttl_seconds)
                logger.info(f"Lock acquired: {lock_name} by {owner_email}")
                return True, None

            # Lock exists, get info about who holds it
            existing = await self.get_lock_info(lock_name)
            logger.info(
                f"Lock denied: {lock_name} held by "
                f"{existing.owner_email if existing else 'unknown'}"
            )
            return False, existing

        except Exception as e:
            logger.exception(f"Failed to acquire lock {lock_name}: {e}")
            raise

    async def release_lock(
        self,
        lock_name: str,
        owner_user_id: str,
    ) -> bool:
        """
        Release a lock.

        Only the owner can release their lock.

        Args:
            lock_name: Name of the lock to release
            owner_user_id: User ID attempting to release

        Returns:
            True if released, False if not owner or not locked
        """
        redis_client = await self._get_redis()
        key = f"{LOCK_KEY_PREFIX}{lock_name}"

        try:
            # Get current lock to verify ownership
            data = await redis_client.get(key)
            if data is None:
                return False

            lock_info = LockInfo.from_dict(json.loads(data))
            if lock_info.owner_user_id != owner_user_id:
                logger.warning(
                    f"Lock release denied: {lock_name} owned by "
                    f"{lock_info.owner_user_id}, not {owner_user_id}"
                )
                return False

            await redis_client.delete(key)
            logger.info(f"Lock released: {lock_name} by {lock_info.owner_email}")
            return True

        except Exception as e:
            logger.exception(f"Failed to release lock {lock_name}: {e}")
            raise

    async def extend_lock(
        self,
        lock_name: str,
        owner_user_id: str,
        additional_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
    ) -> bool:
        """
        Extend a lock's TTL.

        Only the owner can extend their lock.

        Args:
            lock_name: Name of the lock to extend
            owner_user_id: User ID attempting to extend
            additional_seconds: Additional TTL to set

        Returns:
            True if extended, False if not owner or not locked
        """
        redis_client = await self._get_redis()
        key = f"{LOCK_KEY_PREFIX}{lock_name}"

        try:
            # Get current lock to verify ownership
            data = await redis_client.get(key)
            if data is None:
                return False

            lock_info = LockInfo.from_dict(json.loads(data))
            if lock_info.owner_user_id != owner_user_id:
                return False

            # Update expiration
            now = datetime.now(timezone.utc)
            lock_info.expires_at = now + timedelta(seconds=additional_seconds)

            await redis_client.setex(
                key, additional_seconds, json.dumps(lock_info.to_dict())
            )
            logger.debug(f"Lock extended: {lock_name} by {additional_seconds}s")
            return True

        except Exception as e:
            logger.exception(f"Failed to extend lock {lock_name}: {e}")
            raise

    async def get_lock_info(
        self,
        lock_name: str,
    ) -> LockInfo | None:
        """
        Get information about a lock without acquiring it.

        Args:
            lock_name: Name of the lock to check

        Returns:
            LockInfo if locked, None if not locked
        """
        redis_client = await self._get_redis()
        key = f"{LOCK_KEY_PREFIX}{lock_name}"

        try:
            data = await redis_client.get(key)
            if data is None:
                return None

            return LockInfo.from_dict(json.loads(data))

        except Exception as e:
            logger.exception(f"Failed to get lock info {lock_name}: {e}")
            raise

    async def is_locked(self, lock_name: str) -> bool:
        """Check if a lock is currently held."""
        return await self.get_lock_info(lock_name) is not None

    async def force_release_lock(self, lock_name: str) -> bool:
        """
        Force release a lock (admin only).

        Should only be used for stuck locks.

        Args:
            lock_name: Name of the lock to force release

        Returns:
            True if released, False if not locked
        """
        redis_client = await self._get_redis()
        key = f"{LOCK_KEY_PREFIX}{lock_name}"

        try:
            result = await redis_client.delete(key)
            if result > 0:
                logger.warning(f"Lock force released: {lock_name}")
                return True
            return False

        except Exception as e:
            logger.exception(f"Failed to force release lock {lock_name}: {e}")
            raise

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis:
            await self._redis.aclose()
            self._redis = None


# Singleton instance
_lock_service: DistributedLockService | None = None


def get_lock_service() -> DistributedLockService:
    """Get singleton lock service instance."""
    global _lock_service
    if _lock_service is None:
        _lock_service = DistributedLockService()
    return _lock_service


async def close_lock_service() -> None:
    """Close lock service."""
    global _lock_service
    if _lock_service:
        await _lock_service.close()
        _lock_service = None


# Convenience constants for well-known lock names
UPLOAD_LOCK_NAME = "upload"
