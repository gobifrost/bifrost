"""
Model resolver — shared infrastructure for picking the right model at chat time.

The resolver walks the cascade:

    platform_models (is_active=true)
            ↓ intersect (optional)
    Org.allowed_chat_models
            ↓ then defaults cascade
    Org → Role → Workspace → User → Conversation → Message

Most-specific *default* wins, but every choice must still be in the
intersection of platform_models and the org allowlist.

It also resolves logical aliases (`bifrost-fast`, `bifrost-balanced`,
`bifrost-premium`, plus org-defined ones) and applies deprecation remaps at
*lookup time* — `Message.model` is never remapped (immutable history).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.organizations import Organization
from src.models.orm.platform_models import (
    ModelDeprecation,
    OrgModelAlias,
    PlatformModel,
)
from src.models.orm.users import Role, User
from src.models.orm.workspaces import Workspace

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelResolutionContext:
    """All inputs the resolver needs in one place.

    Pass IDs, not ORM objects, so callers don't need to pre-load relations.
    The resolver fetches what it needs via the provided session.
    """

    organization_id: UUID
    user_id: UUID | None = None
    role_ids: tuple[UUID, ...] = ()
    workspace_id: UUID | None = None
    conversation_current_model: str | None = None
    message_override: str | None = None
    required_capabilities: frozenset[str] = frozenset()
    # The org allowlist gates *user-facing chat model selection*. System
    # tasks (summarization, tuning, agent-internal completions) configure a
    # specific model and shouldn't be filtered by the per-org chat allowlist
    # — set to False for those call sites.
    enforce_allowlist: bool = True


@dataclass(frozen=True)
class ModelChoice:
    """The resolver's verdict.

    `model_id` is the concrete provider model ID to call. `cost_tier`,
    `display_name`, and `capabilities` come from `platform_models` and are
    convenient for the caller (no second lookup needed).

    `provenance` names the level that decided the choice — used by the UI
    to render "restricted by ..." tooltips and the picker's source badges.
    """

    model_id: str
    cost_tier: str
    display_name: str
    capabilities: dict[str, Any]
    provenance: str  # one of: message, conversation, user, workspace, role, org, platform
    # The model's context window in tokens (from platform_models), or None when
    # the model isn't in the cache. Used by M5 compaction to compute the
    # per-model auto-compaction threshold (0.85 * context_window).
    context_window: int | None = None


class ModelResolutionError(RuntimeError):
    """Raised when no model in the chain is reachable for this context."""


_PROVENANCE_ORDER = (
    "message",
    "conversation",
    "user",
    "workspace",
    "role",
    "org",
    "platform",
)


async def resolve_model(
    db: AsyncSession,
    ctx: ModelResolutionContext,
) -> ModelChoice:
    """Resolve a `ModelResolutionContext` into a concrete `ModelChoice`.

    Steps:
    1. Load platform_models (active, deduped).
    2. Intersect with org allowlist (if non-empty).
    3. Walk the cascade of defaults bottom-up; first one that's in the
       intersection wins. The platform's first active model is the floor.
    4. Apply alias resolution and deprecation remap.
    5. Return the resolved row.
    """
    # 1. Capability cache (used for enrichment only — NOT a constraint).
    # platform_models powers the picker's price/context/capability info,
    # but a model not in the cache is still callable via the provider —
    # it just won't have rich metadata in the picker.
    platform_rows = (
        await db.scalars(
            select(PlatformModel).where(PlatformModel.is_active.is_(True))
        )
    ).all()
    by_id = {pm.model_id: pm for pm in platform_rows}

    # 2. Resolve the organization
    org = await db.get(Organization, ctx.organization_id)
    if org is None:
        raise ModelResolutionError(
            f"organization {ctx.organization_id} not found"
        )

    # 3. The allowlist + default rules:
    #
    #     empty allowlist  → only the resolved default model is selectable.
    #                        This is the safety guardrail: an admin who
    #                        hasn't configured an allowlist still can't
    #                        accidentally route users at expensive models.
    #     non-empty        → users can pick any allowlisted entry.
    #
    # Either way, platform_models is NOT a gate. The provider's /v1/models
    # response is the actual list of callable models; our cache lags it.
    org_allowlist: list[str] = list(org.allowed_chat_models or [])
    enforce = ctx.enforce_allowlist

    # 3. Cascade — most-specific wins. (model_id, provenance) tuples.
    candidates: list[tuple[str | None, str]] = []
    candidates.append((ctx.message_override, "message"))
    candidates.append((ctx.conversation_current_model, "conversation"))

    user = await db.get(User, ctx.user_id) if ctx.user_id else None
    candidates.append((user.default_chat_model if user else None, "user"))

    workspace = (
        await db.get(Workspace, ctx.workspace_id) if ctx.workspace_id else None
    )
    candidates.append(
        (workspace.default_model if workspace else None, "workspace")
    )

    # Roles: take the first role that has a default. Order is the order given.
    role_default: str | None = None
    if ctx.role_ids:
        roles = (
            await db.scalars(select(Role).where(Role.id.in_(ctx.role_ids)))
        ).all()
        roles_by_id = {r.id: r for r in roles}
        for rid in ctx.role_ids:
            r = roles_by_id.get(rid)
            if r and r.default_chat_model:
                role_default = r.default_chat_model
                break
    candidates.append((role_default, "role"))

    candidates.append((org.default_chat_model, "org"))

    # Selection rules:
    #
    #   enforce=False (system tasks like summarization/tuning):
    #       accept the most-specific non-empty candidate as-is. The caller
    #       configured a specific model and the user-facing allowlist
    #       shouldn't break it.
    #
    #   enforce=True with allowlist set (chat path, allowlist non-empty):
    #       accept the most-specific candidate that is *in the allowlist*.
    #       Skip candidates that aren't (e.g. user-default points at a
    #       model the org no longer allows). Walk down to org default,
    #       which the org admin presumably keeps in sync.
    #
    #   enforce=True with empty allowlist (chat path, no narrowing):
    #       accept only the org default. The picker shouldn't have let the
    #       user pick anything else, so a non-default cascade value is
    #       stale and should fall through. This is the "empty allowlist
    #       means only the default model is selectable" rule.
    org_default_resolved: str | None = None
    if org.default_chat_model:
        org_default_resolved = await _resolve_alias_and_deprecation(
            db, org.default_chat_model, ctx.organization_id
        )

    allowlist_set: set[str] = set(org_allowlist)

    chosen_id: str | None = None
    chosen_provenance: str = "platform"
    for raw, prov in candidates:
        if not raw:
            continue
        resolved = await _resolve_alias_and_deprecation(
            db, raw, ctx.organization_id
        )
        accept = False
        if not enforce:
            accept = True
        elif allowlist_set:
            accept = resolved in allowlist_set
        else:
            # Empty allowlist: only the org default is acceptable. Anything
            # more specific that doesn't equal it is treated as stale.
            accept = (
                prov == "org"
                or (org_default_resolved is not None and resolved == org_default_resolved)
            )
        if accept:
            chosen_id = resolved
            chosen_provenance = prov
            break

    if chosen_id is None:
        # Last-resort floor: pick the org default if there is one. If there
        # isn't, and the allowlist has at least one entry, pick its first.
        if org_default_resolved is not None:
            chosen_id = org_default_resolved
            chosen_provenance = "org"
        elif allowlist_set:
            chosen_id = sorted(allowlist_set)[0]
            chosen_provenance = "platform"
        else:
            raise ModelResolutionError(
                f"no model available for org {ctx.organization_id} "
                "(no allowlist entries and no default configured)"
            )

    # Capability/price enrichment from cache (None when uncached).
    pm = by_id.get(chosen_id)

    # 5. Capability pre-check (if caller asked for any). We can only check
    # against models we have cached metadata for. Uncached = pass-through.
    if ctx.required_capabilities and pm is not None:
        if not has_capabilities(pm, ctx.required_capabilities):
            # Try to find a peer with the capability inside the allowed set.
            peer_pool: set[str]
            if allowlist_set:
                peer_pool = allowlist_set
            elif org_default_resolved is not None:
                peer_pool = {org_default_resolved}
            else:
                peer_pool = set(by_id.keys())
            peer = pick_compatible_from_set(
                peer_pool, by_id, ctx.required_capabilities
            )
            if peer is None:
                raise ModelResolutionError(
                    f"no model in allowed set supports {sorted(ctx.required_capabilities)!r}"
                )
            chosen_id = peer
            chosen_provenance = "capability-fallback"
            pm = by_id[chosen_id]

    # Build the choice. Use cache when we have it; otherwise pass through
    # the raw chosen_id and let the picker / consumer fill metadata from
    # the live provider response.
    if pm is not None:
        return ModelChoice(
            model_id=pm.model_id,
            cost_tier=pm.cost_tier,
            display_name=pm.display_name,
            capabilities=dict(pm.capabilities or {}),
            provenance=chosen_provenance,
            context_window=pm.context_window,
        )
    return ModelChoice(
        model_id=chosen_id,
        cost_tier="balanced",
        display_name=chosen_id.split("/")[-1],
        capabilities={},
        provenance=chosen_provenance,
        context_window=None,
    )


async def _resolve_alias_and_deprecation(
    db: AsyncSession,
    raw_model_id: str,
    organization_id: UUID,
) -> str:
    """Resolve org-level aliases first, then platform deprecations.

    Returns the canonical concrete model_id.
    """
    # Org alias?
    alias_row = await db.scalar(
        select(OrgModelAlias).where(
            OrgModelAlias.organization_id == organization_id,
            OrgModelAlias.alias == raw_model_id,
        )
    )
    if alias_row is not None:
        raw_model_id = alias_row.target_model_id

    # Org-level deprecation wins over platform-wide.
    org_deprecation = await db.scalar(
        select(ModelDeprecation).where(
            ModelDeprecation.organization_id == organization_id,
            ModelDeprecation.old_model_id == raw_model_id,
        )
    )
    if org_deprecation is not None:
        return org_deprecation.new_model_id

    platform_deprecation = await db.scalar(
        select(ModelDeprecation).where(
            ModelDeprecation.organization_id.is_(None),
            ModelDeprecation.old_model_id == raw_model_id,
        )
    )
    if platform_deprecation is not None:
        return platform_deprecation.new_model_id

    return raw_model_id


def has_capabilities(pm: PlatformModel, required: frozenset[str]) -> bool:
    caps = pm.capabilities or {}
    return all(bool(caps.get(c)) for c in required)


def pick_compatible_from_set(
    allowed_ids: set[str],
    by_id: dict[str, PlatformModel],
    required: frozenset[str],
) -> str | None:
    """Return the cheapest-tier compatible model from allowed_ids, or None.

    Tier preference: balanced > fast > premium (i.e., prefer balanced when
    capability compatibility forces a switch — fast may not have the capability,
    premium is expensive).
    """
    tier_order = {"balanced": 0, "fast": 1, "premium": 2}
    candidates = [
        by_id[mid]
        for mid in allowed_ids
        if mid in by_id and has_capabilities(by_id[mid], required)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda pm: (tier_order.get(pm.cost_tier, 99), pm.model_id))
    return candidates[0].model_id


def check_message_compat(
    pm: PlatformModel,
    *,
    has_image: bool = False,
    has_pdf: bool = False,
    needs_tool_use: bool = False,
    has_audio: bool = False,
) -> list[str]:
    """Return human-readable incompatibility reasons; empty list = compatible.

    Used by the composer pre-flight check. Composer wiring lives in M4
    (attachments); this is the underlying utility.
    """
    reasons: list[str] = []
    caps = pm.capabilities or {}
    if has_image and not caps.get("supports_images_in"):
        reasons.append(f"{pm.display_name} can't read images")
    if has_pdf and not caps.get("supports_pdf_in"):
        reasons.append(f"{pm.display_name} can't read PDFs")
    if needs_tool_use and not caps.get("supports_tool_use"):
        reasons.append(f"{pm.display_name} doesn't support tools")
    if has_audio and not caps.get("supports_audio_in"):
        reasons.append(f"{pm.display_name} doesn't support audio input")
    return reasons


async def model_supports_vision(session: AsyncSession, model_id: str) -> bool:
    """Return True if the given model accepts image input (vision).

    Looks the model up in ``platform_models`` and reads the
    ``supports_images_in`` capability flag. Unknown models are treated as
    non-vision (conservative: text-only).
    """
    pm = await session.get(PlatformModel, model_id)
    if pm is None:
        return False
    caps = pm.capabilities or {}
    return bool(caps.get("supports_images_in"))


__all__ = [
    "ModelResolutionContext",
    "ModelResolutionError",
    "ModelChoice",
    "resolve_model",
    "has_capabilities",
    "model_supports_vision",
    "pick_compatible_from_set",
    "check_message_compat",
    "_PROVENANCE_ORDER",
]
