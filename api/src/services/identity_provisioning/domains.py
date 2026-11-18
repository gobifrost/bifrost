"""Organization domain claims, proven by a DNS TXT record.

An organization adds a claim and receives a verification token. It publishes
``bifrost-verification=<token>`` as a TXT record on the domain itself (the
same apex convention Microsoft 365 uses), then asks Bifrost to verify. A
domain can be verified by only one organization; the partial unique index on
``organization_domains`` enforces that even under concurrent verification.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from uuid import UUID

import dns.asyncresolver
import dns.exception
import dns.resolver
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.identity_providers import OrganizationDomain

TXT_PREFIX = "bifrost-verification="
_DNS_TIMEOUT_SECONDS = 5.0
# RFC 1035 labels; at least two labels; no trailing dot.
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$"
)


class DomainClaimError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def normalize_domain(value: str) -> str:
    domain = value.strip().lower().rstrip(".")
    if domain.startswith("@"):
        domain = domain[1:]
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError:
        raise DomainClaimError(422, "Enter a valid domain name") from None
    if not _DOMAIN_RE.match(domain):
        raise DomainClaimError(422, "Enter a valid domain name")
    return domain


def email_domain(email: str) -> str | None:
    _, at, domain = email.rpartition("@")
    if not at or not domain:
        return None
    return domain.lower()


def txt_record_value(claim: OrganizationDomain) -> str:
    return f"{TXT_PREFIX}{claim.verification_token}"


async def verified_domains(session: AsyncSession, organization_id: UUID) -> set[str]:
    rows = await session.scalars(
        select(OrganizationDomain.domain).where(
            OrganizationDomain.organization_id == organization_id,
            OrganizationDomain.verified_at.is_not(None),
        )
    )
    return set(rows)


async def list_claims(session: AsyncSession, organization_id: UUID) -> list[OrganizationDomain]:
    rows = await session.scalars(
        select(OrganizationDomain)
        .where(OrganizationDomain.organization_id == organization_id)
        .order_by(OrganizationDomain.domain)
    )
    return list(rows)


async def get_claim(
    session: AsyncSession, organization_id: UUID, claim_id: UUID
) -> OrganizationDomain:
    claim = await session.scalar(
        select(OrganizationDomain).where(
            OrganizationDomain.id == claim_id,
            OrganizationDomain.organization_id == organization_id,
        )
    )
    if claim is None:
        raise DomainClaimError(404, "Domain claim not found")
    return claim


async def add_claim(
    session: AsyncSession, organization_id: UUID, domain: str, *, created_by: str
) -> OrganizationDomain:
    domain = normalize_domain(domain)
    existing = await session.scalar(
        select(OrganizationDomain).where(
            OrganizationDomain.organization_id == organization_id,
            OrganizationDomain.domain == domain,
        )
    )
    if existing is not None:
        raise DomainClaimError(409, f"This organization already claims {domain}")
    claim = OrganizationDomain(
        organization_id=organization_id,
        domain=domain,
        verification_token=secrets.token_urlsafe(24),
        created_by=created_by,
    )
    session.add(claim)
    await session.flush()
    return claim


async def lookup_txt_records(domain: str) -> list[str]:
    """TXT strings published on ``domain``; empty when there are none."""
    resolver = dns.asyncresolver.Resolver()
    try:
        answer = await resolver.resolve(domain, "TXT", lifetime=_DNS_TIMEOUT_SECONDS)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except (dns.resolver.NoNameservers, dns.exception.Timeout):
        raise DomainClaimError(
            503, f"DNS lookup for {domain} failed; try again in a few minutes"
        ) from None
    return [
        b"".join(rdata.strings).decode("utf-8", errors="replace")
        for rdata in answer
    ]


async def verify_claim(session: AsyncSession, claim: OrganizationDomain) -> OrganizationDomain:
    """Check DNS and mark the claim verified when the record is present."""
    now = datetime.now(timezone.utc)
    records = await lookup_txt_records(claim.domain)
    claim.last_checked_at = now
    if txt_record_value(claim) not in {record.strip() for record in records}:
        await session.flush()
        return claim
    if claim.verified_at is None:
        # Flush first: begin_nested flushes pending changes before its
        # savepoint, and the claim must only be marked verified inside it.
        await session.flush()
        # Read before the savepoint: rolling it back expires the claim.
        domain = claim.domain
        try:
            async with session.begin_nested():
                claim.verified_at = now
                await session.flush()
        except IntegrityError:
            raise DomainClaimError(
                409, f"{domain} is already verified by another organization"
            ) from None
    return claim
