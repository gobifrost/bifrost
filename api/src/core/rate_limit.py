"""
Rate Limiting

Provides Redis-based rate limiting for auth endpoints to prevent brute force attacks.
Uses a sliding window counter pattern for accurate rate tracking.
"""

import logging
from typing import Callable

from fastapi import HTTPException, Request, status

from src.config import get_settings
from src.core.cache import get_shared_redis
from src.core.cache.keys import rate_limit_key, TTL_RATE_LIMIT

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Redis-based rate limiter using sliding window counter.

    Tracks request counts per endpoint and identifier (IP address or user ID).
    When limit is exceeded, raises 429 Too Many Requests.
    """

    def __init__(
        self,
        max_requests: int = 10,
        window_seconds: int = TTL_RATE_LIMIT,
    ):
        """
        Initialize rate limiter.

        Args:
            max_requests: Maximum requests allowed in the window
            window_seconds: Time window in seconds (default: 60)
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    async def check(self, endpoint: str, identifier: str, force: bool = False) -> None:
        """
        Check if request should be rate limited.

        Rate limiting is disabled in testing environment to allow E2E tests
        to run without being blocked.

        Args:
            endpoint: Endpoint name for rate limit key
            identifier: IP address or user ID
            force: If True, bypass the is_testing early-return so the limiter
                   can be exercised from unit tests. Should only be passed by
                   the webhook ingress path.

        Raises:
            HTTPException: If rate limit exceeded (429)
        """
        # Skip rate limiting in testing environment (unless forced for test coverage)
        settings = get_settings()
        if settings.is_testing and not force:
            return

        r = await get_shared_redis()
        key = rate_limit_key(endpoint, identifier)

        # Increment counter
        current = await r.incr(key)

        # Set expiry on first request
        if current == 1:
            await r.expire(key, self.window_seconds)

        if current > self.max_requests:
            # Get remaining TTL for Retry-After header
            ttl = await r.ttl(key)
            logger.warning(
                "Rate limit exceeded",
                extra={
                    "requests": current,
                    "limit": self.max_requests,
                }
            )
            # Track the hit for ops visibility (24h sliding count)
            hit_key = f"bifrost:rate_limit_hits:{identifier}"
            pipe = r.pipeline()
            pipe.incr(hit_key)
            pipe.expire(hit_key, 86400)
            await pipe.execute()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later.",
                headers={"Retry-After": str(ttl)},
            )

    async def get_remaining(self, endpoint: str, identifier: str) -> int:
        """
        Get remaining requests in current window.

        Args:
            endpoint: Endpoint name for rate limit key
            identifier: IP address or user ID

        Returns:
            Number of remaining requests (0 if limit exceeded)
        """
        r = await get_shared_redis()
        key = rate_limit_key(endpoint, identifier)
        current = await r.get(key)

        if current is None:
            return self.max_requests

        count = int(current)
        return max(0, self.max_requests - count)


def get_client_ip(request: Request) -> str:
    """
    Get client IP address from request.

    Handles X-Forwarded-For header for requests behind proxy/load balancer.

    Args:
        request: FastAPI request object

    Returns:
        Client IP address
    """
    # Check for forwarded header (behind proxy)
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # Take first IP (original client)
        return forwarded.split(",")[0].strip()

    # Direct connection
    if request.client:
        return request.client.host

    return "unknown"


def rate_limit(
    endpoint: str,
    max_requests: int = 10,
    window_seconds: int = TTL_RATE_LIMIT,
) -> Callable:
    """
    Create rate limiting dependency for FastAPI endpoints.

    Usage:
        @router.post("/auth/login")
        async def login(
            request: Request,
            _: None = Depends(rate_limit("login", max_requests=5)),
        ):
            ...

    Args:
        endpoint: Name for rate limit tracking
        max_requests: Max requests in window
        window_seconds: Window duration

    Returns:
        FastAPI dependency function
    """
    limiter = RateLimiter(max_requests, window_seconds)

    async def check_rate_limit(request: Request) -> None:
        identifier = get_client_ip(request)
        await limiter.check(endpoint, identifier)

    return check_rate_limit


# Pre-configured rate limiters for common auth scenarios
auth_limiter = RateLimiter(max_requests=10, window_seconds=60)  # 10 req/min
mfa_limiter = RateLimiter(max_requests=5, window_seconds=60)    # 5 req/min
password_reset_limiter = RateLimiter(max_requests=3, window_seconds=300)  # 3 req/5min
