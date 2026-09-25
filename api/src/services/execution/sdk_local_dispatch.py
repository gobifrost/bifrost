"""
Parent-side dispatcher for the engine-local SDK operation transport.

The worker parent (``ProcessPoolManager``) serves ``config`` requests
(get, set, list, delete), ``integrations`` requests (get,
list_mappings, get_mapping, upsert_mapping, delete_mapping,
refresh_token), the SDK workflow/execution reads
(``workflows.list``, ``executions.list``/``executions.get``), and the
SDK form reads (``forms.list``, ``forms.get``) arriving
on each child's dedicated SDK channel. The fixed ``roles`` facade
(``create/get/list/update/delete/list_users/list_forms/assign_users/
assign_forms``) and the fixed ``users`` facade
(``list/create/get/update/delete``) and the fixed ``organizations``
facade (``create/get/list/update/delete``) ride the same channel
through the shared ``sdk_roles``/``sdk_users``/``sdk_organizations``
services; every roles, users, and organizations operation requires
the HTTP
``CurrentSuperuser`` token-equivalent principal (workflow engine tokens
pass, supervised service tokens do not), and child frame actor, org,
and Solution claims can never
grant access. The synchronous client-context
read (``sdk.context``) rides the dedicated import channel instead, so
the synchronous ``BifrostClient.context`` property never deadlocks a
running child event loop.
Identity, scope, and Solution install id come exclusively from the parent's
own dispatch context (:func:`principal_from_context`) — child-supplied
scope strings are treated as untrusted requests and re-validated through
the same ``resolve_effective_scope`` rule table the HTTP path uses. A
per-call Solution target is checked against the parent-owned caller scope,
and the actor
email for mutation audit is the effective SDK actor (engine sentinel for
workflows, service identity for ``@service`` children), never the
initiating user's ``caller.email`` and never child frames.

Each operation runs on the parent's pooled database engine with one short
session, and calls the exact shared business service
(``shared.sdk_config``, ``shared.sdk_integrations``) the HTTP handler calls, so cascade,
external-user behavior, secret handling, type coercion, audit
attribution, commit/cache ordering, declared-Solution behavior, OAuth
token cascade, and missing-key mapping are identical
by construction. Large payloads in either direction travel as bounded
chunked frames (header plus ordered parts, every frame within the wire
bound); small payloads use a single frame.

Allowlist: ``config.get/set/list/delete``, the full
``integrations`` facade (``get/list_mappings/get_mapping/upsert_mapping/
delete_mapping/refresh_token``), the full tables facade, and artifact
write/read/list/download URL plus artifact generation
(``create_document``/``create_spreadsheet``/``create_text``/
``create_image``), the durable video ``create_video`` enqueue plus its
fixed ``video_status`` poll, all fixed file operations, the SDK agent
``enqueue``/``get_run`` operations, the SDK workflow and execution
reads (``workflows.list``, ``executions.list``/``executions.get``),
the SDK form reads (``forms.list``, ``forms.get``),
the fixed ``roles`` facade (``create/get/list/update/delete/
list_users/list_forms/assign_users/assign_forms``, token-equivalent
superuser only), the fixed ``users`` facade
(``list/create/get/update/delete``, token-equivalent superuser only),
the fixed ``organizations`` facade
(``create/get/list/update/delete``, token-equivalent superuser only),
and the SDK workflow
``execute``/``cancel`` mutations. Engine import fast path: ``modules.resolve``
and ``modules.fetch`` (served on the dedicated import channel through the
shared ``sdk_modules`` service, scoped by the parent-derived principal),
plus the synchronous ``sdk.context`` read (served on the same import
channel through the shared ``sdk_context`` service, so the sync client
property never touches the async channel's lock).
Unknown operations or wire versions get an error response — never silent
acceptance, never arbitrary route forwarding. The existing engine token
path is untouched for every operation not yet migrated.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from uuid import UUID

from bifrost._local_transport import (
    _CHUNK_RAW_BYTES,
    MAX_FRAME_BYTES,
    OP_AGENTS_ENQUEUE,
    OP_AGENTS_GET_RUN,
    OP_FORMS_GET,
    OP_FORMS_LIST,
    OP_ROLES_CREATE,
    OP_ROLES_GET,
    OP_ROLES_LIST,
    OP_ROLES_UPDATE,
    OP_ROLES_DELETE,
    OP_ROLES_LIST_USERS,
    OP_ROLES_LIST_FORMS,
    OP_ROLES_ASSIGN_USERS,
    OP_ROLES_ASSIGN_FORMS,
    OP_USERS_LIST,
    OP_USERS_CREATE,
    OP_USERS_GET,
    OP_USERS_UPDATE,
    OP_USERS_DELETE,
    OP_ORGANIZATIONS_CREATE,
    OP_ORGANIZATIONS_GET,
    OP_ORGANIZATIONS_LIST,
    OP_ORGANIZATIONS_UPDATE,
    OP_ORGANIZATIONS_DELETE,
    OP_ARTIFACTS_CREATE_DOCUMENT,
    OP_ARTIFACTS_CREATE_IMAGE,
    OP_ARTIFACTS_CREATE_SPREADSHEET,
    OP_ARTIFACTS_CREATE_TEXT,
    OP_ARTIFACTS_CREATE_VIDEO,
    OP_ARTIFACTS_VIDEO_STATUS,
    OP_EXECUTIONS_GET,
    OP_EXECUTIONS_LIST,
    OP_WORKFLOWS_EXECUTE,
    OP_WORKFLOWS_CANCEL,
    OP_WORKFLOWS_LIST,
    OP_ARTIFACTS_WRITE,
    OP_ARTIFACTS_READ,
    OP_ARTIFACTS_LIST,
    OP_ARTIFACTS_GET_DOWNLOAD_URL,
    OP_FILES_DELETE,
    OP_FILES_EXISTS,
    OP_FILES_LIST,
    OP_FILES_READ,
    OP_FILES_SEARCH,
    OP_FILES_SIGNED_URL,
    OP_FILES_STAT,
    OP_FILES_WRITE,
    OP_CONFIG_DELETE,
    OP_CONFIG_GET,
    OP_CONFIG_LIST,
    OP_CONFIG_SET,
    OP_INTEGRATIONS_DELETE_MAPPING,
    OP_INTEGRATIONS_GET,
    OP_INTEGRATIONS_GET_MAPPING,
    OP_INTEGRATIONS_LIST_MAPPINGS,
    OP_INTEGRATIONS_REFRESH_TOKEN,
    OP_INTEGRATIONS_UPSERT_MAPPING,
    OP_KNOWLEDGE_DELETE,
    OP_KNOWLEDGE_DELETE_NAMESPACE,
    OP_KNOWLEDGE_GET,
    OP_KNOWLEDGE_LIST_NAMESPACES,
    OP_KNOWLEDGE_SEARCH,
    OP_KNOWLEDGE_STORE,
    OP_KNOWLEDGE_STORE_MANY,
    OP_TABLES_COUNT,
    OP_TABLES_BATCH_DELETE,
    OP_TABLES_BATCH,
    OP_TABLES_DELETE_DOCUMENT,
    OP_TABLES_UPDATE,
    OP_TABLES_UPSERT,
    OP_TABLES_INSERT,
    OP_TABLES_DELETE,
    OP_TABLES_LIST,
    OP_TABLES_CREATE,
    OP_TABLES_GET,
    OP_TABLES_QUERY,
    TRANSPORT_VERSION,
    decode_frame,
)
from bifrost._import_transport import (
    OP_MODULES_FETCH,
    OP_MODULES_RESOLVE,
    OP_SDK_CONTEXT,
)
from shared.sdk_config import ScopeResolutionError, resolve_sdk_scope

logger = logging.getLogger(__name__)

# Operations this dispatcher can dispatch. Stage 3a: the config facade, the
# full integrations facade (reads, mapping mutations, token refresh), and
# the table document reads (get/query/unfiltered count). The SDK workflow
# and execution reads (``workflows.list``, ``executions.list``/
# ``executions.get``) ride the same channel through the shared
# ``sdk_execution_reads`` service. The SDK form reads (``forms.list``,
# ``forms.get``) ride the same channel through the shared ``sdk_forms``
# service. Engine import
# fast path: cold module-name resolution and candidate source fetch
# (served on the dedicated import channel through the shared sdk_modules
# service) plus the synchronous client-context read (served on the same
# import channel through the shared ``sdk_context`` service, so the sync
# ``BifrostClient.context`` property never touches the async channel).
SDK_CHANNEL_ALLOWED_OPS = frozenset(
    {
        OP_CONFIG_GET,
        OP_CONFIG_SET,
        OP_CONFIG_LIST,
        OP_CONFIG_DELETE,
        OP_INTEGRATIONS_GET,
        OP_INTEGRATIONS_LIST_MAPPINGS,
        OP_INTEGRATIONS_GET_MAPPING,
        OP_INTEGRATIONS_UPSERT_MAPPING,
        OP_INTEGRATIONS_DELETE_MAPPING,
        OP_INTEGRATIONS_REFRESH_TOKEN,
        OP_TABLES_GET,
        OP_TABLES_QUERY,
        OP_TABLES_COUNT,
        OP_KNOWLEDGE_STORE,
        OP_KNOWLEDGE_STORE_MANY,
        OP_KNOWLEDGE_SEARCH,
        OP_KNOWLEDGE_DELETE,
        OP_KNOWLEDGE_DELETE_NAMESPACE,
        OP_KNOWLEDGE_LIST_NAMESPACES,
        OP_KNOWLEDGE_GET,
        OP_ARTIFACTS_WRITE,
        OP_ARTIFACTS_READ,
        OP_ARTIFACTS_LIST,
        OP_ARTIFACTS_GET_DOWNLOAD_URL,
        OP_ARTIFACTS_CREATE_DOCUMENT,
        OP_ARTIFACTS_CREATE_SPREADSHEET,
        OP_ARTIFACTS_CREATE_TEXT,
        OP_ARTIFACTS_CREATE_IMAGE,
        OP_ARTIFACTS_CREATE_VIDEO,
        OP_ARTIFACTS_VIDEO_STATUS,
        OP_FILES_READ,
        OP_FILES_WRITE,
        OP_FILES_LIST,
        OP_FILES_DELETE,
        OP_FILES_EXISTS,
        OP_FILES_STAT,
        OP_FILES_SIGNED_URL,
        OP_FILES_SEARCH,
        OP_TABLES_BATCH_DELETE,
        OP_TABLES_BATCH,
        OP_TABLES_DELETE_DOCUMENT,
        OP_TABLES_UPDATE,
        OP_TABLES_UPSERT,
        OP_TABLES_INSERT,
        OP_TABLES_DELETE,
        OP_TABLES_LIST,
        OP_TABLES_CREATE,
        OP_AGENTS_ENQUEUE,
        OP_AGENTS_GET_RUN,
        OP_FORMS_LIST,
        OP_FORMS_GET,
        OP_ROLES_CREATE,
        OP_ROLES_GET,
        OP_ROLES_LIST,
        OP_ROLES_UPDATE,
        OP_ROLES_DELETE,
        OP_ROLES_LIST_USERS,
        OP_ROLES_LIST_FORMS,
        OP_ROLES_ASSIGN_USERS,
        OP_ROLES_ASSIGN_FORMS,
        OP_USERS_LIST,
        OP_USERS_CREATE,
        OP_USERS_GET,
        OP_USERS_UPDATE,
        OP_USERS_DELETE,
        OP_ORGANIZATIONS_CREATE,
        OP_ORGANIZATIONS_GET,
        OP_ORGANIZATIONS_LIST,
        OP_ORGANIZATIONS_UPDATE,
        OP_ORGANIZATIONS_DELETE,
        OP_WORKFLOWS_EXECUTE,
        OP_WORKFLOWS_CANCEL,
        OP_WORKFLOWS_LIST,
        OP_EXECUTIONS_LIST,
        OP_EXECUTIONS_GET,
    }
)
IMPORT_CHANNEL_ALLOWED_OPS = frozenset(
    {OP_MODULES_RESOLVE, OP_MODULES_FETCH, OP_SDK_CONTEXT}
)
ALLOWLIST = SDK_CHANNEL_ALLOWED_OPS | IMPORT_CHANNEL_ALLOWED_OPS

# Wall-clock bound for one parent-side operation (short session + indexed
# read). A stall fails that request loudly; the child has its own timeout
# and treats a missing response as fatal (no HTTP fallback).
DISPATCH_TIMEOUT_SECONDS = 25.0
OAUTH_REFRESH_DISPATCH_TIMEOUT_SECONDS = 30.0

# Parent-side bounds for artifact generation. ``create_image`` runs a
# provider HTTP call with a 180s httpx timeout (see
# ``src.services.media_generation.generate_image_with_config``) on no DB
# connection, then stores and records usage; the bound leaves room for
# that call plus the store/commit. Render operations (document/
# spreadsheet/text) are CPU-bound in a worker thread holding no DB
# connection, then store once. Each sits ~10s under the matching child
# deadline so a parent timeout still returns an error frame instead of
# tripping the child deadline first.
ARTIFACT_RENDER_DISPATCH_TIMEOUT_SECONDS = 60.0
ARTIFACT_IMAGE_DISPATCH_TIMEOUT_SECONDS = 185.0

# Type alias for a zero-argument factory returning short sessions on the
# parent's pooled engine (e.g. ``src.core.database.get_session_factory``).
SessionFactory = Callable[[], Any]


@dataclass(frozen=True)
class LocalDispatchPrincipal:
    """Parent-derived identity for one child channel.

    Built once per fork from the parent-owned dispatch context — never from
    child claims.

    ``actor_email`` is the effective SDK actor: ``engine@bifrost.internal``
    for workflow executions (the ``mint_engine_token()`` address HTTP
    workflow calls authenticate as) or ``service-<id>@bifrost.internal``
    for supervised services (the ``mint_service_token()`` address, via the
    shared ``service_sdk_actor_email`` helper). It never comes from
    ``caller.email`` (the initiating user) or from child frames.
    """

    caller_org_id: UUID | None
    is_platform_admin: bool = False
    is_provider_org: bool = False
    # Engine executions always run under the engine sentinel, never as a
    # direct EXTERNAL portal caller, so the full org+global merge applies
    # (matches the HTTP engine path where the sentinel is non-external).
    is_external: bool = False
    # Parent-derived effective SDK actor for mutation audit
    # (``updated_by``). Reads never need it; set/delete fail closed without
    # it rather than attributing the write to a child claim or blank value.
    actor_email: str | None = None
    # True for supervised ``@service`` children (service identity preserved
    # from the parent-owned ``service`` block, never from child frames).
    # Services resolve provider bypass live (see ``_resolve_frame_scope``);
    # workflows keep engine-token semantics.
    is_service: bool = False
    # Solution install id this execution belongs to (None for plain _repo/
    # executions). Derived from the parent-owned dispatch context — never
    # from child frames — so a child cannot forge another install's
    # declared-connection 424. A malformed value fails dispatch closed
    # (see ``principal_from_context``) rather than silently downgrading to
    # the loose (silent-None) behavior.
    solution_id: UUID | None = None
    # Workflow execution id (``context_data["execution_id"]``) or, for
    # services, the attempt id. Carried onto the token-equivalent table
    # principal as the signed ``engine_execution_id`` claim so
    # ``resolve_trustworthy_caller`` attests the caller's own install
    # exactly like the HTTP engine-token path. None degrades safely to
    # "outside any install" (inbound gate denies) rather than forging.
    execution_id: str | None = None
    # Service definition id for supervised ``@service`` children, from the
    # parent-owned ``service`` block. Carried onto the token-equivalent
    # table principal as the ``service_id`` claim (with
    # ``service_attempt_id`` below) so service table access matches the
    # ``mint_service_token`` HTTP path: system-user non-superuser, org
    # confinement, no initiator-admin bypass.
    service_id: str | None = None
    # Service attempt id for supervised ``@service`` children, from the
    # parent-owned ``service`` block. See ``service_id``.
    service_attempt_id: str | None = None
    # Whether Solution-managed code in this execution may import from the
    # bare workspace repository (the install's ``global_repo_access``
    # flag). Derived from the parent-owned
    # ``context_data["solution_global_repo_access"]`` — never from child
    # frames — and combined with ``solution_id`` into the authoritative
    # ``ModuleSourceScope`` for the ``modules.*`` import operations.
    solution_global_repo_access: bool = False


class LocalPrincipalError(ValueError):
    """Parent dispatch context carries an unusable identity.

    Raised before any local pump starts — the pool fails the dispatch
    loudly instead of serving SDK calls under a downgraded (e.g. global)
    scope.
    """


def _solution_id_from_context(context_data: Mapping[str, Any]) -> UUID | None:
    """Derive the Solution install id from parent-owned dispatch context.

    Missing or empty means a plain (non-solution) execution. A malformed
    non-empty value fails closed — declared-connection behavior must not
    silently downgrade to the loose silent-None path.
    """
    raw = context_data.get("solution_id")
    if raw is None or raw == "":
        return None
    if isinstance(raw, UUID):
        return raw
    if isinstance(raw, str):
        try:
            return UUID(raw)
        except ValueError:
            raise LocalPrincipalError(
                f"local dispatch: solution id {raw!r} is not a "
                "valid UUID; refusing to serve local SDK calls (no silent "
                "declared-connection downgrade)"
            ) from None
    raise LocalPrincipalError(
        f"local dispatch: solution id {raw!r} is not a "
        "string; refusing to serve local SDK calls (no silent "
        "declared-connection downgrade)"
    )


def _execution_id_from_context(context_data: Mapping[str, Any]) -> str | None:
    """Execution/attempt id for the token-equivalent table principal.

    A non-empty string rides onto the ``engine_execution_id`` claim so
    ``resolve_trustworthy_caller`` attests the caller's own install exactly
    like the HTTP engine-token path. Anything else degrades to None
    (outside any install — the inbound gate then denies rather than forges).
    """
    raw = context_data.get("execution_id")
    if isinstance(raw, str) and raw:
        return raw
    return None


def _global_repo_access_from_context(context_data: Mapping[str, Any]) -> bool:
    """Whether Solution code may import from the bare workspace repository.

    Reads only the parent-owned ``solution_global_repo_access`` (set by the
    workflow and service producers from the install's
    ``global_repo_access`` flag). A missing value defaults to False (no
    fallback imports — the sealed-Solution posture). A present-but-malformed
    (non-bool) value fails closed: silently coercing truthy junk would widen
    a sealed install's import surface.
    """
    raw = context_data.get("solution_global_repo_access", False)
    if isinstance(raw, bool):
        return raw
    raise LocalPrincipalError(
        f"local dispatch: solution_global_repo_access {raw!r} is not a "
        "bool; refusing to serve local SDK calls (no silent import-scope "
        "downgrade)"
    )


def principal_from_context(context_data: Mapping[str, Any]) -> LocalDispatchPrincipal:
    """Derive the dispatch principal from parent-owned execution context.

    Reads only ``organization`` (id/is_provider), ``is_platform_admin``,
    ``solution_id``, and the parent-owned ``service`` block assembled by the
    parent consumer. Never reads child frames, and never reads
    ``caller.email``: the effective SDK actor is the engine sentinel for
    workflows (``engine@bifrost.internal``, the ``mint_engine_token()`` address HTTP
    workflow calls authenticate as) and the shared
    ``service_sdk_actor_email`` derivation for services (the
    ``mint_service_token()`` address). A missing or empty org id means a
    genuinely global execution; a malformed non-empty id fails closed. A
    present-but-malformed ``service`` block (non-mapping, missing or
    non-UUID ``service_id``) fails closed rather than attributing service
    writes to a forged or blank value. A missing or empty ``solution_id``
    means a plain (non-solution) execution; a malformed non-empty value
    fails closed rather than silently downgrading declared-connection
    behavior to silent-None. ``execution_id`` and the service
    ``service_id``/``attempt_id`` ride along for the token-equivalent table
    principal (signed engine/service claims) — non-string values degrade to
    None (outside any install) rather than failing the whole dispatch.
    ``solution_global_repo_access`` rides along for the authoritative
    module-source scope — missing defaults to False (sealed), malformed
    fails closed.
    """
    from src.core.security import ENGINE_SDK_ACTOR_EMAIL, service_sdk_actor_email

    org = context_data.get("organization") or {}
    raw_org_id = org.get("id")
    caller_org_id: UUID | None = None
    if raw_org_id is None or raw_org_id == "":
        caller_org_id = None
    elif isinstance(raw_org_id, str):
        try:
            caller_org_id = UUID(raw_org_id)
        except ValueError:
            raise LocalPrincipalError(
                f"local dispatch: organization id {raw_org_id!r} is not a "
                "valid UUID; refusing to serve local SDK calls (no silent "
                "global downgrade)"
            ) from None
    else:
        raise LocalPrincipalError(
            f"local dispatch: organization id {raw_org_id!r} is not a "
            "string; refusing to serve local SDK calls (no silent global "
            "downgrade)"
        )
    service_raw = context_data.get("service")
    if service_raw is None:
        return LocalDispatchPrincipal(
            caller_org_id=caller_org_id,
            is_platform_admin=bool(context_data.get("is_platform_admin", False)),
            is_provider_org=bool(org.get("is_provider", False)),
            is_external=False,
            actor_email=ENGINE_SDK_ACTOR_EMAIL,
            is_service=False,
            solution_id=_solution_id_from_context(context_data),
            execution_id=_execution_id_from_context(context_data),
            solution_global_repo_access=_global_repo_access_from_context(
                context_data
            ),
        )
    if not isinstance(service_raw, Mapping):
        raise LocalPrincipalError(
            "local dispatch: service identity is not a mapping; refusing "
            "to serve local SDK calls"
        )
    raw_service_id = service_raw.get("service_id")
    try:
        actor_email = service_sdk_actor_email(raw_service_id)
    except ValueError as e:
        raise LocalPrincipalError(
            f"local dispatch: malformed service identity ({e}); refusing "
            "to serve local SDK calls"
        ) from None
    raw_attempt_id = service_raw.get("attempt_id")
    service_attempt_id = (
        raw_attempt_id
        if isinstance(raw_attempt_id, str) and raw_attempt_id
        else None
    )
    return LocalDispatchPrincipal(
        caller_org_id=caller_org_id,
        # Service tokens are never superuser by construction — force False
        # rather than trusting any flag the context might carry.
        is_platform_admin=False,
        is_provider_org=bool(org.get("is_provider", False)),
        is_external=False,
        actor_email=actor_email,
        is_service=True,
        solution_id=_solution_id_from_context(context_data),
        execution_id=_execution_id_from_context(context_data),
        service_id=raw_service_id if isinstance(raw_service_id, str) else None,
        service_attempt_id=service_attempt_id,
        solution_global_repo_access=_global_repo_access_from_context(
            context_data
        ),
    )


def _error(frame_id: str | None, status: int, detail: str) -> dict[str, Any]:
    return {
        "v": TRANSPORT_VERSION,
        "id": frame_id,
        "ok": False,
        "status": status,
        "detail": detail,
    }


async def dispatch_frames(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve one frame with the same audit actor as its HTTP SDK token."""
    from src.core.constants import SYSTEM_USER_UUID
    from src.services.audit_context import ActorContext, clear_actor, set_actor

    actor_token = set_actor(
        ActorContext(
            user_id=SYSTEM_USER_UUID,
            organization_id=principal.caller_org_id if principal.is_service else None,
            email=principal.actor_email,
            name=(
                f"service-{principal.service_id.replace('-', '')[:12]}"
                if principal.is_service and principal.service_id
                else "Bifrost Engine"
            ),
        )
    )
    try:
        return await _dispatch_frames_impl(session_factory, principal, frame)
    finally:
        clear_actor(actor_token)


async def _dispatch_frames_impl(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve one validated request frame; always returns response frames.

    Never raises except on cancellation: every validation, scope, and
    service failure maps to an ``ok: false`` response carrying an HTTP-style
    status the child maps back to the public exception. Large results are
    returned as a lazy iterable yielding a header frame plus ordered part
    frames (bounded chunked transfer); everything else is a single frame.

    The chunked iterable is lazy: each part is base64-encoded only as the
    caller iterates, so ``serve_channel`` generates one part, sends it,
    and only then generates the next (sequential backpressure). The single
    ``raw_result`` bytes buffer is shared across yields, as the HTTP path
    materializes one body.
    """
    frame_id = frame.get("id") if isinstance(frame.get("id"), str) else None
    if frame.get("v") != TRANSPORT_VERSION:
        return [_error(frame_id, 400, f"unsupported local transport version: {frame.get('v')!r}")]
    op = frame.get("op")
    if op not in ALLOWLIST:
        return [_error(frame_id, 404, f"local SDK operation not allowed: {op!r}")]
    if op == OP_CONFIG_GET:
        return await _dispatch_config_frames(session_factory, principal, frame_id, frame)
    if op == OP_CONFIG_SET:
        return await _dispatch_config_set(session_factory, principal, frame_id, frame)
    if op == OP_CONFIG_LIST:
        return await _dispatch_config_list(session_factory, principal, frame_id, frame)
    if op == OP_CONFIG_DELETE:
        return await _dispatch_config_delete(session_factory, principal, frame_id, frame)
    if op == OP_INTEGRATIONS_GET:
        return await _dispatch_integrations_get(session_factory, principal, frame_id, frame)
    if op == OP_INTEGRATIONS_LIST_MAPPINGS:
        return await _dispatch_integrations_list_mappings(session_factory, principal, frame_id, frame)
    if op == OP_INTEGRATIONS_GET_MAPPING:
        return await _dispatch_integrations_get_mapping(session_factory, principal, frame_id, frame)
    if op == OP_INTEGRATIONS_UPSERT_MAPPING:
        return await _dispatch_integrations_upsert_mapping(session_factory, principal, frame_id, frame)
    if op == OP_INTEGRATIONS_DELETE_MAPPING:
        return await _dispatch_integrations_delete_mapping(session_factory, principal, frame_id, frame)
    if op == OP_INTEGRATIONS_REFRESH_TOKEN:
        return await _dispatch_integrations_refresh_token(session_factory, principal, frame_id, frame)
    if op == OP_TABLES_CREATE:
        return await _dispatch_tables_create(session_factory, principal, frame_id, frame)
    if op == OP_TABLES_LIST:
        return await _dispatch_tables_list(session_factory, principal, frame_id, frame)
    if op == OP_TABLES_DELETE:
        return await _dispatch_tables_delete(session_factory, principal, frame_id, frame)
    if op == OP_TABLES_INSERT:
        return await _dispatch_tables_insert(session_factory, principal, frame_id, frame)
    if op == OP_TABLES_UPSERT:
        return await _dispatch_tables_upsert(session_factory, principal, frame_id, frame)
    if op == OP_TABLES_UPDATE:
        return await _dispatch_tables_update(session_factory, principal, frame_id, frame)
    if op == OP_TABLES_DELETE_DOCUMENT:
        return await _dispatch_tables_delete_document(session_factory, principal, frame_id, frame)
    if op == OP_TABLES_BATCH:
        return await _dispatch_tables_batch(session_factory, principal, frame_id, frame)
    if op == OP_TABLES_BATCH_DELETE:
        return await _dispatch_tables_batch_delete(session_factory, principal, frame_id, frame)
    if op == OP_TABLES_GET:
        return await _dispatch_tables_get(session_factory, principal, frame_id, frame)
    if op == OP_TABLES_QUERY:
        return await _dispatch_tables_query(session_factory, principal, frame_id, frame)
    if op == OP_TABLES_COUNT:
        return await _dispatch_tables_count(session_factory, principal, frame_id, frame)
    if op == OP_ARTIFACTS_WRITE:
        return await _dispatch_artifacts_write(session_factory, principal, frame_id, frame)
    if op == OP_ARTIFACTS_READ:
        return await _dispatch_artifacts_read(session_factory, principal, frame_id, frame)
    if op == OP_ARTIFACTS_LIST:
        return await _dispatch_artifacts_list(session_factory, principal, frame_id, frame)
    if op == OP_ARTIFACTS_GET_DOWNLOAD_URL:
        return await _dispatch_artifacts_download_url(session_factory, principal, frame_id, frame)
    if op == OP_ARTIFACTS_CREATE_DOCUMENT:
        return await _dispatch_artifacts_create_document(session_factory, principal, frame_id, frame)
    if op == OP_ARTIFACTS_CREATE_SPREADSHEET:
        return await _dispatch_artifacts_create_spreadsheet(session_factory, principal, frame_id, frame)
    if op == OP_ARTIFACTS_CREATE_TEXT:
        return await _dispatch_artifacts_create_text(session_factory, principal, frame_id, frame)
    if op == OP_ARTIFACTS_CREATE_IMAGE:
        return await _dispatch_artifacts_create_image(session_factory, principal, frame_id, frame)
    if op == OP_ARTIFACTS_CREATE_VIDEO:
        return await _dispatch_artifacts_create_video(session_factory, principal, frame_id, frame)
    if op == OP_ARTIFACTS_VIDEO_STATUS:
        return await _dispatch_artifacts_video_status(session_factory, principal, frame_id, frame)
    if op == OP_FILES_READ:
        return await _dispatch_files_read(session_factory, principal, frame_id, frame)
    if op == OP_FILES_WRITE:
        return await _dispatch_files_write(session_factory, principal, frame_id, frame)
    if op == OP_FILES_LIST:
        return await _dispatch_files_list(session_factory, principal, frame_id, frame)
    if op == OP_FILES_DELETE:
        return await _dispatch_files_delete(session_factory, principal, frame_id, frame)
    if op == OP_FILES_EXISTS:
        return await _dispatch_files_exists(session_factory, principal, frame_id, frame)
    if op == OP_FILES_STAT:
        return await _dispatch_files_stat(session_factory, principal, frame_id, frame)
    if op == OP_FILES_SIGNED_URL:
        return await _dispatch_files_signed_url(session_factory, principal, frame_id, frame)
    if op == OP_FILES_SEARCH:
        return await _dispatch_files_search(session_factory, principal, frame_id, frame)
    if op == OP_KNOWLEDGE_STORE:
        return await _dispatch_knowledge_store(session_factory, principal, frame_id, frame)
    if op == OP_KNOWLEDGE_STORE_MANY:
        return await _dispatch_knowledge_store_many(session_factory, principal, frame_id, frame)
    if op == OP_KNOWLEDGE_SEARCH:
        return await _dispatch_knowledge_search(session_factory, principal, frame_id, frame)
    if op == OP_KNOWLEDGE_DELETE:
        return await _dispatch_knowledge_delete(session_factory, principal, frame_id, frame)
    if op == OP_KNOWLEDGE_DELETE_NAMESPACE:
        return await _dispatch_knowledge_delete_namespace(session_factory, principal, frame_id, frame)
    if op == OP_KNOWLEDGE_LIST_NAMESPACES:
        return await _dispatch_knowledge_list_namespaces(session_factory, principal, frame_id, frame)
    if op == OP_KNOWLEDGE_GET:
        return await _dispatch_knowledge_get(session_factory, principal, frame_id, frame)
    if op == OP_AGENTS_ENQUEUE:
        return await _dispatch_agents_enqueue(session_factory, principal, frame_id, frame)
    if op == OP_AGENTS_GET_RUN:
        return await _dispatch_agents_get_run(session_factory, principal, frame_id, frame)
    if op == OP_FORMS_LIST:
        return await _dispatch_forms_list(session_factory, principal, frame_id, frame)
    if op == OP_FORMS_GET:
        return await _dispatch_forms_get(session_factory, principal, frame_id, frame)
    if op == OP_ROLES_CREATE:
        return await _dispatch_roles_create(session_factory, principal, frame_id, frame)
    if op == OP_ROLES_GET:
        return await _dispatch_roles_get(session_factory, principal, frame_id, frame)
    if op == OP_ROLES_LIST:
        return await _dispatch_roles_list(session_factory, principal, frame_id, frame)
    if op == OP_ROLES_UPDATE:
        return await _dispatch_roles_update(session_factory, principal, frame_id, frame)
    if op == OP_ROLES_DELETE:
        return await _dispatch_roles_delete(session_factory, principal, frame_id, frame)
    if op == OP_ROLES_LIST_USERS:
        return await _dispatch_roles_list_users(session_factory, principal, frame_id, frame)
    if op == OP_ROLES_LIST_FORMS:
        return await _dispatch_roles_list_forms(session_factory, principal, frame_id, frame)
    if op == OP_ROLES_ASSIGN_USERS:
        return await _dispatch_roles_assign_users(session_factory, principal, frame_id, frame)
    if op == OP_ROLES_ASSIGN_FORMS:
        return await _dispatch_roles_assign_forms(session_factory, principal, frame_id, frame)
    if op == OP_USERS_LIST:
        return await _dispatch_users_list(session_factory, principal, frame_id, frame)
    if op == OP_USERS_CREATE:
        return await _dispatch_users_create(session_factory, principal, frame_id, frame)
    if op == OP_USERS_GET:
        return await _dispatch_users_get(session_factory, principal, frame_id, frame)
    if op == OP_USERS_UPDATE:
        return await _dispatch_users_update(session_factory, principal, frame_id, frame)
    if op == OP_USERS_DELETE:
        return await _dispatch_users_delete(session_factory, principal, frame_id, frame)
    if op == OP_ORGANIZATIONS_CREATE:
        return await _dispatch_organizations_create(session_factory, principal, frame_id, frame)
    if op == OP_ORGANIZATIONS_GET:
        return await _dispatch_organizations_get(session_factory, principal, frame_id, frame)
    if op == OP_ORGANIZATIONS_LIST:
        return await _dispatch_organizations_list(session_factory, principal, frame_id, frame)
    if op == OP_ORGANIZATIONS_UPDATE:
        return await _dispatch_organizations_update(session_factory, principal, frame_id, frame)
    if op == OP_ORGANIZATIONS_DELETE:
        return await _dispatch_organizations_delete(session_factory, principal, frame_id, frame)
    if op == OP_WORKFLOWS_LIST:
        return await _dispatch_workflows_list(session_factory, principal, frame_id, frame)
    if op == OP_EXECUTIONS_LIST:
        return await _dispatch_executions_list(session_factory, principal, frame_id, frame)
    if op == OP_EXECUTIONS_GET:
        return await _dispatch_executions_get(session_factory, principal, frame_id, frame)
    if op == OP_WORKFLOWS_EXECUTE:
        return await _dispatch_workflows_execute(session_factory, principal, frame_id, frame)
    if op == OP_WORKFLOWS_CANCEL:
        return await _dispatch_workflows_cancel(session_factory, principal, frame_id, frame)
    if op == OP_MODULES_RESOLVE:
        return await _dispatch_modules_resolve(session_factory, principal, frame_id, frame)
    if op == OP_MODULES_FETCH:
        return await _dispatch_modules_fetch(session_factory, principal, frame_id, frame)
    if op == OP_SDK_CONTEXT:
        return await _dispatch_sdk_context(session_factory, principal, frame_id, frame)
    return [_error(frame_id, 404, f"local SDK operation not allowed: {op!r}")]


async def dispatch_frame(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame: dict[str, Any],
) -> dict[str, Any]:
    """First response frame for one request (the full response when single-frame)."""
    return next(iter(await dispatch_frames(session_factory, principal, frame)))


class _OutgoingTooLarge(Exception):
    """A frame the parent built exceeds the wire bound (internal bug guard)."""


def _encode_outgoing(frame: dict[str, Any]) -> bytes:
    """Serialize one outgoing frame, enforcing the byte bound before sending."""
    raw = json.dumps(frame, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_FRAME_BYTES:
        raise _OutgoingTooLarge(
            f"outgoing local SDK frame is {len(raw)} bytes "
            f"(limit {MAX_FRAME_BYTES})"
        )
    return raw


def _validate_request(
    model: Any, data: dict[str, Any], frame_id: str | None, op: str
) -> tuple[Any, dict[str, Any] | None]:
    """Validate one local frame with the same Pydantic DTO as its HTTP handler.

    Gives local requests the HTTP request's coercion (e.g. ``is_secret``
    bool parsing) and required-field behavior without changing public SDK
    signatures. Returns ``(request, None)`` on success, or
    ``(None, error_frame)`` with an HTTP-style 422 the child maps to the
    public exception.
    """
    from pydantic import ValidationError

    try:
        return model.model_validate(data), None
    except ValidationError as e:
        return None, _error(frame_id, 422, f"invalid {op} request: {e}")


def _scope_needs_bypass_check(
    principal: LocalDispatchPrincipal, scope: Any
) -> bool:
    """True when resolving ``scope`` would consult the bypass gate.

    Mirrors the ``needs_bypass_check`` rule the HTTP path applies: UNSET
    and the caller's own org resolve without consulting provider/admin
    membership; anything else (explicit global, another org) needs the
    gate. Malformed scopes return False here — the resolver itself 422s
    them without any membership lookup.
    """
    if scope is None or scope == "":
        return False
    if scope == "global":
        return not principal.is_platform_admin
    if not isinstance(scope, str):
        return False
    try:
        requested = UUID(scope)
    except ValueError:
        return False
    if requested == principal.caller_org_id:
        return False
    return not principal.is_platform_admin


async def _live_provider_membership(
    session_factory: SessionFactory | None,
    caller_org_id: UUID | None,
) -> bool | None:
    """Live ``is_provider`` for the caller's org, or None when unknowable.

    Returns None when there is no session factory or no caller org (the
    caller resolves the failure closed). DB errors propagate to the caller,
    which maps them to a 500 transport error — never to silent bypass.
    """
    if session_factory is None or caller_org_id is None:
        return None
    from src.models import Organization

    async def _lookup(session: Any) -> bool:
        from sqlalchemy import select

        row = await session.execute(
            select(Organization.is_provider).where(
                Organization.id == caller_org_id
            )
        )
        return bool(row.scalar_one_or_none())

    async with session_factory() as session:
        return await asyncio.wait_for(_lookup(session), DISPATCH_TIMEOUT_SECONDS)


async def _resolve_frame_scope(
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
    session_factory: SessionFactory | None = None,
) -> tuple[UUID | None, dict[str, Any] | None]:
    """Resolve one frame's untrusted scope string against the principal.

    Returns ``(org_id, None)`` on success, or ``(None, error_frame)`` with
    the HTTP-style status the child maps to the public exception.

    For supervised services, a cross-org/global request re-checks
    provider membership live in a short parent session — the snapshot
    captured when the service started must not survive a revocation.
    Own-org calls need no extra lookup. Workflow engine-token semantics
    are unchanged (superuser token path keeps its snapshot behavior).
    """
    if (
        principal.is_service
        and _scope_needs_bypass_check(principal, frame.get("scope"))
    ):
        try:
            live_provider = await _live_provider_membership(
                session_factory, principal.caller_org_id
            )
        except asyncio.TimeoutError:
            logger.warning("local scope check timed out; refusing bypass")
            return None, _error(frame_id, 500, "local scope check timed out")
        except Exception as e:  # noqa: BLE001 - transport must return errors, not raise
            logger.exception("local scope check failed")
            return None, _error(
                frame_id, 500, f"local scope check failed: {type(e).__name__}"
            )
        if live_provider is None:
            return None, _error(
                frame_id, 500, "local scope check failed: no caller organization"
            )
        try:
            resolved_org_id = await resolve_sdk_scope(
                frame.get("scope"),
                caller_org_id=principal.caller_org_id,
                is_platform_admin=principal.is_platform_admin,
                is_provider_org=live_provider,
            )
        except ScopeResolutionError as e:
            return None, _error(frame_id, e.status_code, e.detail)
        return resolved_org_id, None
    try:
        resolved_org_id = await resolve_sdk_scope(
            frame.get("scope"),
            caller_org_id=principal.caller_org_id,
            is_platform_admin=principal.is_platform_admin,
            is_provider_org=principal.is_provider_org,
        )
    except ScopeResolutionError as e:
        return None, _error(frame_id, e.status_code, e.detail)
    return resolved_org_id, None


async def _run_short(
    session_factory: SessionFactory,
    coro_factory: Callable[[], Any],
    *,
    op: str,
    log_key: str,
    status_errors: tuple[type[Exception], ...] = (),
    timeout_seconds: float = DISPATCH_TIMEOUT_SECONDS,
) -> tuple[Any, dict[str, Any] | None]:
    """Run one service coroutine on a short pooled session with a deadline.

    Returns ``(result, None)`` on success, or ``(None, error_frame)`` for
    the timeout/service failures the child maps to transport errors.
    Exceptions listed in ``status_errors`` carry their own HTTP-style
    status (e.g. the integrations service 424) instead of the generic
    500; everything else unexpected stays a 500.
    """
    try:
        async def _run() -> Any:
            async with session_factory() as session:
                return await coro_factory(session)

        result = await asyncio.wait_for(_run(), timeout_seconds)
    except asyncio.TimeoutError:
        logger.warning("local %s dispatch timed out for key=%r", op, log_key)
        return None, _error(None, 503, f"local {op} dispatch timed out")
    except status_errors as e:
        status = getattr(e, "status_code", 500)
        detail = getattr(e, "detail", str(e))
        try:
            status = int(status)
        except (TypeError, ValueError):
            status = 500
        if not isinstance(detail, str):
            detail = str(detail)
        return None, _error(None, status, detail)
    except Exception as e:  # noqa: BLE001 - transport must return errors, not raise
        logger.exception("local %s dispatch failed for key=%r", op, log_key)
        return None, _error(None, 500, f"local {op} failed: {type(e).__name__}")
    return result, None


def _single_ok(frame_id: str | None, result: Any) -> list[dict[str, Any]]:
    """One small ``ok`` frame (set/delete and small get/list results)."""
    return [
        {
            "v": TRANSPORT_VERSION,
            "id": frame_id,
            "ok": True,
            "result": result,
        }
    ]


def _ok_frames(
    frame_id: str | None, result: Any
) -> Iterable[dict[str, Any]]:
    """Single frame, or lazy bounded chunked frames for a large result.

    Shares the response-chunking contract with ``config.get``: the single
    ``raw_result`` buffer is shared and each part base64-encodes only as
    the caller iterates, so ``serve_channel`` sends sequentially with pipe
    backpressure. No total result cap (the HTTP path has none).
    """
    single = {
        "v": TRANSPORT_VERSION,
        "id": frame_id,
        "ok": True,
        "result": result,
    }
    if result is not None:
        raw_result = json.dumps(result, separators=(",", ":")).encode("utf-8")
        single_raw = json.dumps(single, separators=(",", ":")).encode("utf-8")
        if len(single_raw) > MAX_FRAME_BYTES:
            return _chunked_frames(frame_id, raw_result)
    return [single]


def _require_actor(
    principal: LocalDispatchPrincipal, frame_id: str | None, op: str
) -> dict[str, Any] | None:
    """Fail a mutation closed when the parent identity lacks an actor email.

    The email comes only from the parent-owned dispatch context — never
    from child frames — so a missing value means the write cannot be
    audited and must not proceed.
    """
    if not principal.actor_email:
        logger.warning(
            "local %s refused: parent dispatch context has no caller email", op
        )
        return _error(
            frame_id,
            500,
            f"local {op} failed: parent dispatch identity has no caller email",
        )
    return None


async def _dispatch_config_set(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    from shared.sdk_config import set_sdk_config_value
    from src.models.contracts.cli import CLIConfigSetRequest

    request, invalid = _validate_request(
        CLIConfigSetRequest,
        {
            "key": frame.get("key"),
            "value": frame.get("value"),
            "is_secret": frame.get("is_secret", False),
            "scope": frame.get("scope"),
        },
        frame_id,
        OP_CONFIG_SET,
    )
    if "value" not in frame:
        return [_error(frame_id, 422, "config set requires a value")]
    if invalid is not None:
        return [invalid]
    key, is_secret = request.key, bool(request.is_secret)
    resolved_org_id, scope_error = await _resolve_frame_scope(
        principal, frame_id, {"scope": request.scope}, session_factory
    )
    if scope_error is not None:
        return [scope_error]
    actor_error = _require_actor(principal, frame_id, OP_CONFIG_SET)
    if actor_error is not None:
        return [actor_error]
    assert principal.actor_email is not None

    async def _set(session: Any) -> None:
        await set_sdk_config_value(
            session,
            key=key,
            value=request.value,
            is_secret=is_secret,
            org_id=resolved_org_id,
            actor_email=principal.actor_email,
        )

    _, error = await _run_short(session_factory, _set, op=OP_CONFIG_SET, log_key=key)
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _single_ok(frame_id, None)


async def _dispatch_config_list(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    from shared.sdk_config import list_sdk_config_values
    from src.models.contracts.cli import CLIConfigListRequest

    request, invalid = _validate_request(
        CLIConfigListRequest,
        {"scope": frame.get("scope")},
        frame_id,
        OP_CONFIG_LIST,
    )
    if invalid is not None:
        return [invalid]
    resolved_org_id, scope_error = await _resolve_frame_scope(
        principal, frame_id, {"scope": request.scope}, session_factory
    )
    if scope_error is not None:
        return [scope_error]

    async def _list(session: Any) -> dict[str, Any]:
        return await list_sdk_config_values(
            session,
            org_id=resolved_org_id,
            external=principal.is_external,
        )

    result, error = await _run_short(
        session_factory, _list, op=OP_CONFIG_LIST, log_key=""
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_config_delete(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    from shared.sdk_config import delete_sdk_config_value
    from src.models.contracts.cli import CLIConfigDeleteRequest

    request, invalid = _validate_request(
        CLIConfigDeleteRequest,
        {"key": frame.get("key"), "scope": frame.get("scope")},
        frame_id,
        OP_CONFIG_DELETE,
    )
    if invalid is not None:
        return [invalid]
    key = request.key
    resolved_org_id, scope_error = await _resolve_frame_scope(
        principal, frame_id, {"scope": request.scope}, session_factory
    )
    if scope_error is not None:
        return [scope_error]
    actor_error = _require_actor(principal, frame_id, OP_CONFIG_DELETE)
    if actor_error is not None:
        return [actor_error]
    assert principal.actor_email is not None

    async def _delete(session: Any) -> bool:
        return await delete_sdk_config_value(
            session,
            key=key,
            org_id=resolved_org_id,
            actor_email=principal.actor_email,
        )

    result, error = await _run_short(
        session_factory, _delete, op=OP_CONFIG_DELETE, log_key=key
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _single_ok(frame_id, result)


async def _dispatch_config_frames(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``config.get`` through the common scope/session/chunk path.

    Shares ``_validate_request`` (HTTP DTO), ``_resolve_frame_scope``,
    ``_run_short``, and ``_ok_frames`` with set/list/delete, so there is
    one local scope/error/chunk behavior across the facade.
    """
    from shared.sdk_config import get_sdk_config_dict
    from src.models.contracts.cli import CLIConfigGetRequest

    request, invalid = _validate_request(
        CLIConfigGetRequest,
        {"key": frame.get("key"), "scope": frame.get("scope")},
        frame_id,
        OP_CONFIG_GET,
    )
    if invalid is not None:
        return [invalid]
    resolved_org_id, scope_error = await _resolve_frame_scope(
        principal, frame_id, {"scope": request.scope}, session_factory
    )
    if scope_error is not None:
        return [scope_error]

    async def _get(session: Any) -> dict[str, Any] | None:
        return await get_sdk_config_dict(
            session,
            key=request.key,
            org_id=resolved_org_id,
            external=principal.is_external,
        )

    result, error = await _run_short(
        session_factory, _get, op=OP_CONFIG_GET, log_key=request.key
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_integrations_get(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``integrations.get`` through the shared integrations service.

    Validates with the same HTTP DTO, resolves the untrusted scope string
    against the parent-derived principal, and calls the same service the
    HTTP handler calls. The Solution install id comes ONLY from the
    principal: a child ``"solution"`` frame field is never read, so a
    child cannot forge another install's declared-connection 424.
    """
    from shared.sdk_integrations import (
        IntegrationServiceError,
        get_sdk_integration_dict,
    )
    from src.models.contracts.cli import SDKIntegrationsGetRequest

    request, invalid = _validate_request(
        SDKIntegrationsGetRequest,
        {
            "name": frame.get("name"),
            "scope": frame.get("scope"),
            "oauth_scope": frame.get("oauth_scope"),
        },
        frame_id,
        OP_INTEGRATIONS_GET,
    )
    if invalid is not None:
        return [invalid]
    resolved_org_id, scope_error = await _resolve_frame_scope(
        principal, frame_id, {"scope": request.scope}, session_factory
    )
    if scope_error is not None:
        return [scope_error]

    async def _get(session: Any) -> dict[str, Any] | None:
        return await get_sdk_integration_dict(
            session,
            name=request.name,
            org_id=resolved_org_id,
            oauth_scope=request.oauth_scope,
            solution_id=principal.solution_id,
            external=principal.is_external,
        )

    result, error = await _run_short(
        session_factory,
        _get,
        op=OP_INTEGRATIONS_GET,
        log_key=request.name,
        status_errors=(IntegrationServiceError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_integrations_list_mappings(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``integrations.list_mappings`` through the shared service.

    A missing integration returns null (like HTTP); otherwise the
    ``{"items": [...]}`` envelope rides single or chunked frames.
    """
    from shared.sdk_integrations import list_sdk_integration_mappings
    from src.models.contracts.cli import SDKIntegrationsListMappingsRequest

    request, invalid = _validate_request(
        SDKIntegrationsListMappingsRequest,
        {"name": frame.get("name"), "scope": frame.get("scope")},
        frame_id,
        OP_INTEGRATIONS_LIST_MAPPINGS,
    )
    if invalid is not None:
        return [invalid]
    async def _list(session: Any) -> dict[str, Any] | None:
        items = await list_sdk_integration_mappings(
            session,
            name=request.name,
            scope=request.scope,
            caller_org_id=principal.caller_org_id,
            is_platform_admin=principal.is_platform_admin,
            is_provider_org=None if principal.is_service else principal.is_provider_org,
            external=principal.is_external,
        )
        if items is None:
            return None
        return {"items": items}

    result, error = await _run_short(
        session_factory,
        _list,
        op=OP_INTEGRATIONS_LIST_MAPPINGS,
        log_key=request.name,
        status_errors=(ScopeResolutionError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_integrations_get_mapping(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``integrations.get_mapping`` through the shared service.

    Entity-ID fallback lookups stay scoped by the resolved org exactly
    like the HTTP path, so non-bypass callers cannot probe other orgs.
    """
    from shared.sdk_integrations import get_sdk_integration_mapping_dict
    from src.models.contracts.cli import SDKIntegrationsGetMappingRequest

    request, invalid = _validate_request(
        SDKIntegrationsGetMappingRequest,
        {
            "name": frame.get("name"),
            "scope": frame.get("scope"),
            "entity_id": frame.get("entity_id"),
        },
        frame_id,
        OP_INTEGRATIONS_GET_MAPPING,
    )
    if invalid is not None:
        return [invalid]
    async def _get_mapping(session: Any) -> dict[str, Any] | None:
        return await get_sdk_integration_mapping_dict(
            session,
            name=request.name,
            scope=request.scope,
            caller_org_id=principal.caller_org_id,
            is_platform_admin=principal.is_platform_admin,
            is_provider_org=None if principal.is_service else principal.is_provider_org,
            entity_id=request.entity_id,
            external=principal.is_external,
        )

    result, error = await _run_short(
        session_factory,
        _get_mapping,
        op=OP_INTEGRATIONS_GET_MAPPING,
        log_key=request.name,
        status_errors=(ScopeResolutionError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_integrations_upsert_mapping(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``integrations.upsert_mapping`` through the shared service.

    Validates with the same HTTP DTO, requires the parent-derived actor
    email (writes cannot be audited off a child claim), and calls the
    same service the HTTP handler calls: missing integration 404s before
    scope validation, global scope is a 400, the existing row keeps its
    OAuth link, and the echo carries the merged config.
    """
    from shared.sdk_integrations import (
        IntegrationServiceError,
        upsert_sdk_integration_mapping,
    )
    from src.models.contracts.cli import SDKIntegrationsUpsertMappingRequest

    request, invalid = _validate_request(
        SDKIntegrationsUpsertMappingRequest,
        {
            "name": frame.get("name"),
            "scope": frame.get("scope"),
            "entity_id": frame.get("entity_id"),
            "entity_name": frame.get("entity_name"),
            "config": frame.get("config"),
        },
        frame_id,
        OP_INTEGRATIONS_UPSERT_MAPPING,
    )
    if invalid is not None:
        return [invalid]
    actor_error = _require_actor(principal, frame_id, OP_INTEGRATIONS_UPSERT_MAPPING)
    if actor_error is not None:
        return [actor_error]
    assert principal.actor_email is not None

    async def _upsert(session: Any) -> dict[str, Any]:
        return await upsert_sdk_integration_mapping(
            session,
            name=request.name,
            scope=request.scope,
            caller_org_id=principal.caller_org_id,
            is_platform_admin=principal.is_platform_admin,
            is_provider_org=None if principal.is_service else principal.is_provider_org,
            external=principal.is_external,
            entity_id=request.entity_id,
            entity_name=request.entity_name,
            config=request.config,
            actor_email=principal.actor_email,
        )

    result, error = await _run_short(
        session_factory,
        _upsert,
        op=OP_INTEGRATIONS_UPSERT_MAPPING,
        log_key=request.name,
        status_errors=(IntegrationServiceError, ScopeResolutionError),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_integrations_delete_mapping(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``integrations.delete_mapping`` through the shared service.

    A missing integration, a global scope, or a missing mapping returns
    ``{"deleted": False}`` (like HTTP); scope denials still 403.
    """
    from shared.sdk_integrations import delete_sdk_integration_mapping
    from src.models.contracts.cli import SDKIntegrationsDeleteMappingRequest

    request, invalid = _validate_request(
        SDKIntegrationsDeleteMappingRequest,
        {"name": frame.get("name"), "scope": frame.get("scope")},
        frame_id,
        OP_INTEGRATIONS_DELETE_MAPPING,
    )
    if invalid is not None:
        return [invalid]
    actor_error = _require_actor(principal, frame_id, OP_INTEGRATIONS_DELETE_MAPPING)
    if actor_error is not None:
        return [actor_error]

    async def _delete(session: Any) -> dict[str, bool]:
        return await delete_sdk_integration_mapping(
            session,
            name=request.name,
            scope=request.scope,
            caller_org_id=principal.caller_org_id,
            is_platform_admin=principal.is_platform_admin,
            is_provider_org=None if principal.is_service else principal.is_provider_org,
        )

    result, error = await _run_short(
        session_factory,
        _delete,
        op=OP_INTEGRATIONS_DELETE_MAPPING,
        log_key=request.name,
        status_errors=(ScopeResolutionError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _single_ok(frame_id, result)


async def _dispatch_integrations_refresh_token(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``integrations.refresh_token`` through the shared service.

    The SDK ``refresh()`` call passes no scope, so a missing scope
    resolves to the caller's own org — exactly like the HTTP handler.
    The locked token lookup, refresh context, rotation, persistence,
    and external-caller restrictions are the shared service's, so both
    paths commit the same state. The child registers the fresh token
    with its own secret scrubber (like the HTTP SDK facade does).
    """
    from shared.sdk_integrations import (
        IntegrationServiceError,
        refresh_sdk_oauth_token,
    )
    from src.models.contracts.cli import SDKIntegrationsRefreshTokenRequest

    request, invalid = _validate_request(
        SDKIntegrationsRefreshTokenRequest,
        {
            "connection_name": frame.get("connection_name"),
            "scope": frame.get("scope"),
        },
        frame_id,
        OP_INTEGRATIONS_REFRESH_TOKEN,
    )
    if invalid is not None:
        return [invalid]

    async def _refresh(session: Any) -> dict[str, Any]:
        return await refresh_sdk_oauth_token(
            session,
            connection_name=request.connection_name,
            scope=request.scope,
            caller_org_id=principal.caller_org_id,
            is_platform_admin=principal.is_platform_admin,
            is_provider_org=None if principal.is_service else principal.is_provider_org,
            external=principal.is_external,
        )

    result, error = await _run_short(
        session_factory,
        _refresh,
        op=OP_INTEGRATIONS_REFRESH_TOKEN,
        log_key=request.connection_name,
        status_errors=(IntegrationServiceError, ScopeResolutionError),
        timeout_seconds=OAUTH_REFRESH_DISPATCH_TIMEOUT_SECONDS,
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _single_ok(frame_id, result)


def _table_user_for_principal(principal: LocalDispatchPrincipal) -> Any:
    """Token-equivalent ``UserPrincipal`` for table resolution and policy checks.

    Mirrors the minted engine/service tokens the HTTP table path
    authenticates, so org gates, install resolution, and row policies decide
    identically on both transports:

    - workflows: system-user superuser with the parent execution and Solution
      ids as the signed ``engine_execution_id``/``engine_solution_id``
      claims (the ``mint_engine_token()`` shape);
    - services: system-user non-superuser with the parent service, attempt,
      and Solution ids (the ``mint_service_token()`` shape), org-confined.

    The config-oriented ``principal.is_platform_admin`` tracks the
    *initiating* user and must NOT stand in here: a platform-admin
    initiator's service child is still a non-superuser service caller, and a
    non-admin initiator's workflow child is still the superuser engine.
    """
    from src.core.constants import SYSTEM_USER_UUID
    from src.core.principal import UserPrincipal
    from src.core.security import ENGINE_SDK_ACTOR_EMAIL

    solution_id = (
        str(principal.solution_id) if principal.solution_id is not None else None
    )
    if principal.is_service:
        return UserPrincipal(
            user_id=SYSTEM_USER_UUID,
            email=principal.actor_email or ENGINE_SDK_ACTOR_EMAIL,
            organization_id=principal.caller_org_id,
            is_superuser=False,
            engine_execution_id=principal.service_attempt_id
            or principal.execution_id,
            engine_solution_id=solution_id,
            service_id=principal.service_id,
            service_attempt_id=principal.service_attempt_id
            or principal.execution_id,
        )
    return UserPrincipal(
        user_id=SYSTEM_USER_UUID,
        email=ENGINE_SDK_ACTOR_EMAIL,
        organization_id=None,
        is_superuser=True,
        engine_execution_id=principal.execution_id,
        engine_solution_id=solution_id,
    )


def _agent_user_for_principal(principal: LocalDispatchPrincipal) -> Any:
    """Token-equivalent ``UserPrincipal`` for SDK agent run operations.

    Reuses the table token-equivalent shape so enqueue attribution and
    run visibility decide identically on both transports: workflows run
    as the system-user superuser with no org (the ``mint_engine_token()``
    shape — global scope, all runs visible), services run as the
    system-user non-superuser confined to their service org (the
    ``mint_service_token()`` shape). The actor is built only from the
    parent-derived ``LocalDispatchPrincipal`` — never from child frame
    fields.
    """
    return _table_user_for_principal(principal)


async def _dispatch_agents_enqueue(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``agents.enqueue`` through the shared agent-run service.

    Validates with the same ``AgentRunEnqueueRequest`` DTO as the HTTP
    handler, builds the actor from the parent-derived principal, and
    calls the same ``shared.sdk_agent_runs`` service the HTTP handler
    calls — agent lookup, inactive-Solution 409, paused short-circuit,
    and queue payload/attribution are identical by construction. The
    queue service owns its durable transaction (row commit plus
    failure marking); this dispatcher adds no commit. A local attempt
    never retries over HTTP.
    """
    from shared.sdk_agent_runs import SdkAgentRunError, enqueue_sdk_agent_run
    from src.models.contracts.agent_runs import AgentRunEnqueueRequest

    request, invalid = _validate_request(
        AgentRunEnqueueRequest,
        {
            "agent_name": frame.get("agent_name"),
            "input": frame.get("input"),
            "output_schema": frame.get("output_schema"),
        },
        frame_id,
        OP_AGENTS_ENQUEUE,
    )
    if invalid is not None:
        return [invalid]
    assert request is not None
    user = _agent_user_for_principal(principal)

    async def _enqueue(session: Any) -> dict[str, Any]:
        result = await enqueue_sdk_agent_run(
            session,
            user,
            agent_name=request.agent_name,
            input_data=request.input,
            output_schema=request.output_schema,
        )
        return result.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _enqueue,
        op=OP_AGENTS_ENQUEUE,
        log_key=request.agent_name,
        status_errors=(SdkAgentRunError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_agents_get_run(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``agents.get_run`` through the shared agent-run service.

    The run id validates as a UUID (422 on malformed, like the HTTP
    path's path-param parsing). Visibility, usage, steps, and detail
    construction are the shared service's — a hidden or missing run is
    a 404 error frame the facade maps to ``ValueError``. Large details
    ride bounded chunked frames via ``_ok_frames``.
    """
    from shared.sdk_agent_runs import SdkAgentRunError, get_sdk_agent_run

    raw_id = frame.get("run_id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_AGENTS_GET_RUN} request: 'run_id' is required",
            )
        ]
    try:
        run_uuid = UUID(raw_id)
    except ValueError:
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_AGENTS_GET_RUN} request: 'run_id' must be a UUID",
            )
        ]
    user = _agent_user_for_principal(principal)

    async def _get(session: Any) -> dict[str, Any]:
        detail = await get_sdk_agent_run(session, user, run_id=run_uuid)
        return detail.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _get,
        op=OP_AGENTS_GET_RUN,
        log_key=raw_id,
        status_errors=(SdkAgentRunError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


# =============================================================================
# SDK form reads (forms.list, forms.get)
# =============================================================================


def _forms_user_for_principal(principal: LocalDispatchPrincipal) -> Any:
    """Token-equivalent ``UserPrincipal`` for SDK form list/get operations.

    Reuses the table token-equivalent shape so visibility decides
    identically on both transports: workflows run as the system-user
    superuser with no org (the ``mint_engine_token()`` shape — the full
    listing, inactive forms included), services run as the system-user
    non-superuser confined to their service org (the
    ``mint_service_token()`` shape — org plus global forms, active
    only). The actor is built only from the parent-derived
    ``LocalDispatchPrincipal`` — never from child frame fields. Engine
    children never present embed claims, so the embed binding gate in
    the shared service only ever denies locally, exactly like an
    engine-token HTTP call.
    """
    return _table_user_for_principal(principal)


async def _dispatch_forms_list(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``forms.list`` through the shared form service.

    The frame carries no fields (the SDK exposes no scope filter) — the
    parent applies the route defaults like the unfiltered HTTP call, and
    scope resolution, the superuser/org-user query split, logo
    enrichment, and dependency counts are the shared ``sdk_forms``
    service's, identical to HTTP by construction. The request runs
    under the token-equivalent engine/service user built only from the
    parent-derived principal, never from child claims. Large listings
    ride bounded chunked frames via ``_ok_frames``. A local attempt
    never retries over HTTP.
    """
    from shared.sdk_forms import list_sdk_forms

    user = _forms_user_for_principal(principal)

    async def _list(session: Any) -> dict[str, Any]:
        forms = await list_sdk_forms(session, user, scope=None)
        return {"items": [f.model_dump(mode="json") for f in forms]}

    result, error = await _run_short(
        session_factory,
        _list,
        op=OP_FORMS_LIST,
        log_key="forms",
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_forms_get(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``forms.get`` through the shared form service.

    Validates ``form_id`` as a UUID (422, like the route's FastAPI
    parsing) and calls the same ``shared.sdk_forms.get_sdk_form`` the
    HTTP handler calls — 404-before-403 error precedence, the embed
    binding gate, inactive-form hiding, logo enrichment with the inline
    logo, and role ids are identical by construction. The request runs
    under the token-equivalent engine/service user built only from the
    parent-derived principal, never from child claims. A local attempt
    never retries over HTTP.
    """
    from shared.sdk_forms import SdkFormError, get_sdk_form

    raw_id = frame.get("form_id")
    form_uuid: UUID | None = None
    if isinstance(raw_id, str) and raw_id.strip():
        try:
            form_uuid = UUID(raw_id)
        except ValueError:
            form_uuid = None
    if form_uuid is None:
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_FORMS_GET} request: 'form_id' must be a UUID",
            )
        ]
    user = _forms_user_for_principal(principal)

    async def _get(session: Any) -> dict[str, Any]:
        form = await get_sdk_form(session, user, form_uuid)
        return form.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _get,
        op=OP_FORMS_GET,
        log_key=str(form_uuid),
        status_errors=(SdkFormError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


# =============================================================================
# SDK roles facade
# (create/get/list/update/delete/list_users/list_forms/assign_users/
# assign_forms)
# =============================================================================


def _require_platform_admin(
    principal: LocalDispatchPrincipal, frame_id: str | None
) -> dict[str, Any] | None:
    """Enforce the HTTP ``CurrentSuperuser`` gate on a local roles call.

    Workflow children authenticate to HTTP with the engine superuser token,
    regardless of the initiating user's platform-admin flag. Supervised
    services authenticate with a non-superuser service token. The gate reads
    only that parent-derived token-equivalent distinction; child frame claims
    cannot grant access. It runs before frame validation, as the HTTP auth
    dependency does before validating path and body parameters.
    """
    if principal.is_service:
        return _error(frame_id, 403, "Superuser privileges required")
    return None


def _parse_roles_role_id(
    frame: dict[str, Any], frame_id: str | None, op: str
) -> tuple[UUID | None, dict[str, Any] | None]:
    """One role-id frame field (UUID), else an HTTP-style 422.

    Mirrors the route's FastAPI path parsing: a missing or malformed id
    is a 422, never a 404.
    """
    raw_id = frame.get("role_id")
    role_uuid: UUID | None = None
    if isinstance(raw_id, str) and raw_id.strip():
        try:
            role_uuid = UUID(raw_id)
        except ValueError:
            role_uuid = None
    if role_uuid is None:
        return None, _error(
            frame_id,
            422,
            f"invalid {op} request: 'role_id' must be a UUID",
        )
    return role_uuid, None


async def _dispatch_roles_create(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``roles.create`` through the shared roles service.

    Validates with the same ``RoleCreate`` DTO the HTTP handler uses
    (422), gates on the parent-derived token-equivalent authority (403, before
    validation — like the HTTP dependency), then calls the same
    ``shared.sdk_roles.create_role`` the handler calls, so response
    fields, actor attribution, audit, and cache invalidation are
    identical by construction. The shared service only flushes; the
    dispatcher commits explicitly because the HTTP ``get_db``
    dependency commits after the handler returns while ``_run_short``
    never commits. A local attempt never retries over HTTP.
    """
    from fastapi import HTTPException

    from shared.sdk_roles import RoleServiceError, create_role
    from src.models import RoleCreate

    denied = _require_platform_admin(principal, frame_id)
    if denied is not None:
        return [denied]
    request, invalid = _validate_request(
        RoleCreate,
        {
            "name": frame.get("name"),
            "description": frame.get("description"),
        },
        frame_id,
        OP_ROLES_CREATE,
    )
    if invalid is not None:
        return [invalid]
    actor_error = _require_actor(principal, frame_id, OP_ROLES_CREATE)
    if actor_error is not None:
        return [actor_error]
    assert principal.actor_email is not None

    async def _create(session: Any) -> dict[str, Any]:
        role = await create_role(
            session,
            name=request.name,
            description=request.description,
            permissions=request.permissions,
            actor_email=principal.actor_email,
        )
        # The HTTP dependency commits after the handler returns. The
        # local session factory only closes its session, so persist the
        # role before the child can read it in a subsequent SDK call.
        await session.commit()
        return role.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _create,
        op=OP_ROLES_CREATE,
        log_key=request.name,
        status_errors=(RoleServiceError, HTTPException),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_roles_get(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``roles.get`` through the shared roles service.

    Platform-admin gate first (403, like the HTTP dependency), then
    role-id UUID parsing (422, like the route's FastAPI parsing), then
    the same ``shared.sdk_roles.get_role`` the handler calls — the
    missing-role 404 the facade maps to ``ValueError`` is identical by
    construction. A local attempt never retries over HTTP.
    """
    from fastapi import HTTPException

    from shared.sdk_roles import RoleServiceError, get_role

    denied = _require_platform_admin(principal, frame_id)
    if denied is not None:
        return [denied]
    role_uuid, invalid = _parse_roles_role_id(frame, frame_id, OP_ROLES_GET)
    if invalid is not None:
        return [invalid]
    assert role_uuid is not None

    async def _get(session: Any) -> dict[str, Any]:
        role = await get_role(session, role_id=role_uuid)
        return role.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _get,
        op=OP_ROLES_GET,
        log_key=str(role_uuid),
        status_errors=(RoleServiceError, HTTPException),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_roles_list(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``roles.list`` through the shared roles service.

    The frame carries no fields (the SDK exposes no search/sort filter)
    — the parent applies the route defaults (unfiltered, name ascending,
    unbounded), like the unfiltered HTTP call. Platform-admin gate
    first (403), then the same ``shared.sdk_roles.list_roles`` the
    handler calls. Returns the ``{"items", "total"}`` envelope (the
    transport result contract does not carry bare lists; ``total``
    mirrors the HTTP ``X-Total-Count`` header the facade ignores).
    Large listings ride bounded chunked frames via ``_ok_frames``. A
    local attempt never retries over HTTP.
    """
    from fastapi import HTTPException

    from shared.sdk_roles import RoleServiceError, list_roles

    denied = _require_platform_admin(principal, frame_id)
    if denied is not None:
        return [denied]

    async def _list(session: Any) -> dict[str, Any]:
        items, total = await list_roles(session)
        return {
            "items": [item.model_dump(mode="json") for item in items],
            "total": total,
        }

    result, error = await _run_short(
        session_factory,
        _list,
        op=OP_ROLES_LIST,
        log_key="roles",
        status_errors=(RoleServiceError, HTTPException),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_roles_update(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``roles.update`` through the shared roles service.

    Platform-admin gate first (403, like the HTTP dependency), then
    role-id UUID parsing (422) and the same ``RoleUpdate`` DTO the HTTP
    handler uses (422 — unknown fields ignored, only non-None fields
    applied), then the same ``shared.sdk_roles.update_role`` the
    handler calls. The missing-role 404 the facade maps to
    ``ValueError`` is identical by construction. The dispatcher commits
    explicitly (the shared service only flushes; HTTP commits via
    ``get_db``). A local attempt never retries over HTTP.
    """
    from fastapi import HTTPException

    from shared.sdk_roles import RoleServiceError, update_role
    from src.models import RoleUpdate

    denied = _require_platform_admin(principal, frame_id)
    if denied is not None:
        return [denied]
    role_uuid, invalid_id = _parse_roles_role_id(
        frame, frame_id, OP_ROLES_UPDATE
    )
    if invalid_id is not None:
        return [invalid_id]
    assert role_uuid is not None
    raw_updates = frame.get("updates")
    if not isinstance(raw_updates, dict):
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_ROLES_UPDATE} request: 'updates' must be an object",
            )
        ]
    request, invalid = _validate_request(
        RoleUpdate, raw_updates, frame_id, OP_ROLES_UPDATE
    )
    if invalid is not None:
        return [invalid]
    actor_error = _require_actor(principal, frame_id, OP_ROLES_UPDATE)
    if actor_error is not None:
        return [actor_error]
    assert principal.actor_email is not None

    async def _update(session: Any) -> dict[str, Any]:
        role = await update_role(
            session,
            role_id=role_uuid,
            name=request.name,
            description=request.description,
            permissions=request.permissions,
            actor_email=principal.actor_email,
        )
        # The HTTP dependency commits after the handler returns; the
        # local session never commits itself.
        await session.commit()
        return role.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _update,
        op=OP_ROLES_UPDATE,
        log_key=str(role_uuid),
        status_errors=(RoleServiceError, HTTPException),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_roles_delete(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``roles.delete`` through the shared roles service.

    Platform-admin gate first (403), then role-id UUID parsing (422),
    then the same ``shared.sdk_roles.delete_role`` the handler calls —
    missing-role 404 and the solution-guard 409 (which propagates
    unchanged as an ``HTTPException``) are identical by construction.
    Returns no body (null result, like HTTP 204). The dispatcher
    commits explicitly (the shared service only flushes; HTTP commits
    via ``get_db``). A local attempt never retries over HTTP.
    """
    from fastapi import HTTPException

    from shared.sdk_roles import RoleServiceError, delete_role

    denied = _require_platform_admin(principal, frame_id)
    if denied is not None:
        return [denied]
    role_uuid, invalid = _parse_roles_role_id(
        frame, frame_id, OP_ROLES_DELETE
    )
    if invalid is not None:
        return [invalid]
    assert role_uuid is not None
    actor_error = _require_actor(principal, frame_id, OP_ROLES_DELETE)
    if actor_error is not None:
        return [actor_error]

    async def _delete(session: Any) -> None:
        await delete_role(session, role_id=role_uuid)
        # The HTTP dependency commits after the handler returns; the
        # local session never commits itself.
        await session.commit()

    _, error = await _run_short(
        session_factory,
        _delete,
        op=OP_ROLES_DELETE,
        log_key=str(role_uuid),
        status_errors=(RoleServiceError, HTTPException),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _single_ok(frame_id, None)


async def _dispatch_roles_list_users(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``roles.list_users`` through the shared roles service.

    Platform-admin gate first (403), then role-id UUID parsing (422),
    then the same ``shared.sdk_roles.list_role_users`` the handler
    calls — the exact ``RoleUsersResponse`` envelope, empty (never 404)
    for an unknown role, is identical by construction. A local attempt
    never retries over HTTP.
    """
    from fastapi import HTTPException

    from shared.sdk_roles import RoleServiceError, list_role_users

    denied = _require_platform_admin(principal, frame_id)
    if denied is not None:
        return [denied]
    role_uuid, invalid = _parse_roles_role_id(
        frame, frame_id, OP_ROLES_LIST_USERS
    )
    if invalid is not None:
        return [invalid]
    assert role_uuid is not None

    async def _list(session: Any) -> dict[str, Any]:
        return (
            await list_role_users(session, role_id=role_uuid)
        ).model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _list,
        op=OP_ROLES_LIST_USERS,
        log_key=str(role_uuid),
        status_errors=(RoleServiceError, HTTPException),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_roles_list_forms(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``roles.list_forms`` through the shared roles service.

    Platform-admin gate first (403), then role-id UUID parsing (422),
    then the same ``shared.sdk_roles.list_role_forms`` the handler
    calls — the exact ``RoleFormsResponse`` envelope, empty (never 404)
    for an unknown role, is identical by construction. A local attempt
    never retries over HTTP.
    """
    from fastapi import HTTPException

    from shared.sdk_roles import RoleServiceError, list_role_forms

    denied = _require_platform_admin(principal, frame_id)
    if denied is not None:
        return [denied]
    role_uuid, invalid = _parse_roles_role_id(
        frame, frame_id, OP_ROLES_LIST_FORMS
    )
    if invalid is not None:
        return [invalid]
    assert role_uuid is not None

    async def _list(session: Any) -> dict[str, Any]:
        return (
            await list_role_forms(session, role_id=role_uuid)
        ).model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _list,
        op=OP_ROLES_LIST_FORMS,
        log_key=str(role_uuid),
        status_errors=(RoleServiceError, HTTPException),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_roles_assign_users(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``roles.assign_users`` through the shared roles service.

    Platform-admin gate first (403), then role-id UUID parsing (422)
    and the same ``AssignUsersToRoleRequest`` DTO the HTTP handler uses
    (422 — an empty list fails its ``min_length`` exactly like the HTTP
    body validation), then the same
    ``shared.sdk_roles.assign_users_to_role`` the handler calls:
    unknown users skipped, already-assigned no-ops, per-user cache
    invalidation, and actor attribution are identical by construction.
    Returns no body (null result, like HTTP 204). The dispatcher
    commits explicitly (the shared service only flushes; HTTP commits
    via ``get_db``). A local attempt never retries over HTTP.
    """
    from fastapi import HTTPException

    from shared.sdk_roles import RoleServiceError, assign_users_to_role
    from src.models import AssignUsersToRoleRequest

    denied = _require_platform_admin(principal, frame_id)
    if denied is not None:
        return [denied]
    role_uuid, invalid_id = _parse_roles_role_id(
        frame, frame_id, OP_ROLES_ASSIGN_USERS
    )
    if invalid_id is not None:
        return [invalid_id]
    assert role_uuid is not None
    request, invalid = _validate_request(
        AssignUsersToRoleRequest,
        {"user_ids": frame.get("user_ids")},
        frame_id,
        OP_ROLES_ASSIGN_USERS,
    )
    if invalid is not None:
        return [invalid]
    actor_error = _require_actor(principal, frame_id, OP_ROLES_ASSIGN_USERS)
    if actor_error is not None:
        return [actor_error]
    assert principal.actor_email is not None

    async def _assign(session: Any) -> None:
        await assign_users_to_role(
            session,
            role_id=role_uuid,
            user_ids=request.user_ids,
            actor_email=principal.actor_email,
        )
        # The HTTP dependency commits after the handler returns; the
        # local session never commits itself.
        await session.commit()

    _, error = await _run_short(
        session_factory,
        _assign,
        op=OP_ROLES_ASSIGN_USERS,
        log_key=str(role_uuid),
        status_errors=(RoleServiceError, HTTPException),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _single_ok(frame_id, None)


async def _dispatch_roles_assign_forms(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``roles.assign_forms`` through the shared roles service.

    Platform-admin gate first (403), then role-id UUID parsing (422)
    and the same ``AssignFormsToRoleRequest`` DTO the HTTP handler uses
    (422), then the same ``shared.sdk_roles.assign_forms_to_role`` the
    handler calls: missing forms 404, solution-managed forms 409 via
    the guard (propagates unchanged), already-assigned no-ops, and a
    malformed form id raising ``ValueError`` (unlisted in
    ``status_errors``, so the generic 500 — exactly the HTTP outcome)
    are identical by construction. Returns no body (null result, like
    HTTP 204). The dispatcher commits explicitly (the shared service
    only flushes; HTTP commits via ``get_db``). A local attempt never
    retries over HTTP.
    """
    from fastapi import HTTPException

    from shared.sdk_roles import RoleServiceError, assign_forms_to_role
    from src.models import AssignFormsToRoleRequest

    denied = _require_platform_admin(principal, frame_id)
    if denied is not None:
        return [denied]
    role_uuid, invalid_id = _parse_roles_role_id(
        frame, frame_id, OP_ROLES_ASSIGN_FORMS
    )
    if invalid_id is not None:
        return [invalid_id]
    assert role_uuid is not None
    request, invalid = _validate_request(
        AssignFormsToRoleRequest,
        {"form_ids": frame.get("form_ids")},
        frame_id,
        OP_ROLES_ASSIGN_FORMS,
    )
    if invalid is not None:
        return [invalid]
    actor_error = _require_actor(principal, frame_id, OP_ROLES_ASSIGN_FORMS)
    if actor_error is not None:
        return [actor_error]
    assert principal.actor_email is not None

    async def _assign(session: Any) -> None:
        await assign_forms_to_role(
            session,
            role_id=role_uuid,
            form_ids=request.form_ids,
            actor_email=principal.actor_email,
        )
        # The HTTP dependency commits after the handler returns; the
        # local session never commits itself.
        await session.commit()

    _, error = await _run_short(
        session_factory,
        _assign,
        op=OP_ROLES_ASSIGN_FORMS,
        log_key=str(role_uuid),
        status_errors=(RoleServiceError, HTTPException),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _single_ok(frame_id, None)


# =============================================================================
# SDK users facade
# (list/create/get/update/delete)
# =============================================================================


def _users_user_for_principal(principal: LocalDispatchPrincipal) -> Any:
    """Token-equivalent ``UserPrincipal`` for SDK user list operations.

    Reuses the table token-equivalent shape so scope resolution decides
    identically on both transports: workflows run as the system-user
    superuser with no org (the ``mint_engine_token()`` shape — the scope
    filter selects all, global-only, or one org, like the HTTP
    ``CurrentSuperuser`` path), services run as the system-user
    non-superuser confined to their service org (the
    ``mint_service_token()`` shape). Services never reach the shared
    service — the platform-admin gate denies them first, like the HTTP
    auth dependency. The principal is built only from the
    parent-derived ``LocalDispatchPrincipal`` — never from child frame
    fields.
    """
    return _table_user_for_principal(principal)


def _users_actor_user_id(principal: LocalDispatchPrincipal) -> Any:
    """Parent-derived actor id for user create/delete attribution.

    The HTTP engine path authenticates as the system-user sentinel
    (``mint_engine_token()`` ``sub``), so the local actor is the same
    ``SYSTEM_USER_UUID`` — never a child frame claim and never the
    initiating user's id. Called only after the platform-admin gate,
    so service principals never reach it.
    """
    from src.core.constants import SYSTEM_USER_UUID

    return SYSTEM_USER_UUID


def _require_users_field(
    frame: dict[str, Any], field: str, frame_id: str | None, op: str
) -> tuple[str | None, dict[str, Any] | None]:
    """One required non-empty string frame field, else a 422 error frame."""
    value = frame.get(field)
    if isinstance(value, str) and value.strip():
        return value, None
    return None, _error(frame_id, 422, f"invalid {op} request: {field!r} is required")


async def _dispatch_users_list(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``users.list`` through the shared users service.

    Platform-admin gate first (403, like the HTTP dependency), then
    the frame's ``scope``/``include_inactive`` fields. The facade's
    ``org_id`` rides as ``scope``, like the HTTP query string; the
    parent applies the route defaults for the filters the SDK does not
    expose (unfiltered, legacy email order, unbounded), like the
    unfiltered HTTP call. Calls the same
    ``shared.sdk_users.list_users`` the handler calls, so scope
    resolution, the 422 on a malformed scope, invite statuses, and
    pagination totals are identical by construction. Returns the
    ``{"items", "total"}`` envelope (the transport result contract
    does not carry bare lists; ``total`` mirrors the HTTP
    ``X-Total-Count`` header the facade ignores). Large listings ride
    bounded chunked frames via ``_ok_frames``. A local attempt never
    retries over HTTP.
    """
    from shared.sdk_users import UserServiceError, list_users

    denied = _require_platform_admin(principal, frame_id)
    if denied is not None:
        return [denied]
    scope = frame.get("scope")
    if scope is not None and not isinstance(scope, str):
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_USERS_LIST} request: 'scope' must be a string",
            )
        ]
    include_inactive = frame.get("include_inactive", False)
    if not isinstance(include_inactive, bool):
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_USERS_LIST} request: 'include_inactive' must be a boolean",
            )
        ]
    user = _users_user_for_principal(principal)

    async def _list(session: Any) -> dict[str, Any]:
        items, total = await list_users(
            session,
            user,
            scope=scope,
            include_inactive=include_inactive,
        )
        return {
            "items": [item.model_dump(mode="json") for item in items],
            "total": total,
        }

    result, error = await _run_short(
        session_factory,
        _list,
        op=OP_USERS_LIST,
        log_key="users",
        status_errors=(UserServiceError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_users_create(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``users.create`` through the shared users service.

    Platform-admin gate first (403, like the HTTP dependency), then
    the same ``UserCreate`` DTO the HTTP handler uses (422 — malformed
    emails and organization ids match), then the same
    ``shared.sdk_users.create_user`` the handler calls, so the
    verified-but-unregistered row, the pending invite with its
    one-time registration URL, and audit attribution are identical by
    construction. The actor id comes only from the parent-derived
    principal (the engine sentinel, like the HTTP engine token
    ``sub``) — never from child frames. The dispatcher commits
    explicitly (the shared service only flushes; HTTP commits via
    ``get_db``). A local attempt never retries over HTTP.
    """
    from shared.sdk_users import UserServiceError, create_user
    from src.models.contracts.users import UserCreate

    denied = _require_platform_admin(principal, frame_id)
    if denied is not None:
        return [denied]
    request, invalid = _validate_request(
        UserCreate,
        {
            "email": frame.get("email"),
            "name": frame.get("name"),
            "is_active": frame.get("is_active", True),
            "is_superuser": frame.get("is_superuser", False),
            "organization_id": frame.get("organization_id"),
        },
        frame_id,
        OP_USERS_CREATE,
    )
    if invalid is not None:
        return [invalid]
    actor_error = _require_actor(principal, frame_id, OP_USERS_CREATE)
    if actor_error is not None:
        return [actor_error]
    assert principal.actor_email is not None and request is not None
    actor_user_id = _users_actor_user_id(principal)

    async def _create(session: Any) -> dict[str, Any]:
        created = await create_user(
            session,
            email=str(request.email),
            name=request.name,
            is_active=request.is_active,
            is_superuser=request.is_superuser,
            is_external=request.is_external,
            organization_id=request.organization_id,
            actor_user_id=actor_user_id,
        )
        # The HTTP dependency commits after the handler returns; the
        # local session never commits itself.
        await session.commit()
        return created.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _create,
        op=OP_USERS_CREATE,
        log_key=str(request.email),
        status_errors=(UserServiceError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_users_get(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``users.get`` through the shared users service.

    Platform-admin gate first (403, like the HTTP dependency), then
    the ``user_id`` field (UUID with email fallback, like the route —
    only a missing id is a 422), then the same
    ``shared.sdk_users.get_user`` the handler calls — the missing-user
    404 the facade maps to ``None`` is identical by construction. A
    local attempt never retries over HTTP.
    """
    from shared.sdk_users import UserServiceError, get_user

    denied = _require_platform_admin(principal, frame_id)
    if denied is not None:
        return [denied]
    user_id, invalid = _require_users_field(
        frame, "user_id", frame_id, OP_USERS_GET
    )
    if invalid is not None:
        return [invalid]
    assert user_id is not None

    async def _get(session: Any) -> dict[str, Any]:
        fetched = await get_user(session, user_id=user_id)
        return fetched.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _get,
        op=OP_USERS_GET,
        log_key=user_id,
        status_errors=(UserServiceError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_users_update(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``users.update`` through the shared users service.

    Platform-admin gate first (403, like the HTTP dependency), then
    the ``user_id`` field and the same ``UserUpdate`` DTO the HTTP
    handler uses (422 — unknown fields ignored, only non-None fields
    applied, ``organization_id=None`` meaning "no change"), then the
    same ``shared.sdk_users.update_user`` the handler calls — the
    missing-user 404 the facade maps to ``ValueError``, the system
    user 403, role-transition promotion, and audit parity (``password``
    accepted but never applied) are identical by construction. The
    dispatcher commits explicitly (the shared service only flushes;
    HTTP commits via ``get_db``). A local attempt never retries over
    HTTP.
    """
    from shared.sdk_users import UserServiceError, update_user
    from src.models.contracts.users import UserUpdate

    denied = _require_platform_admin(principal, frame_id)
    if denied is not None:
        return [denied]
    user_id, invalid_id = _require_users_field(
        frame, "user_id", frame_id, OP_USERS_UPDATE
    )
    if invalid_id is not None:
        return [invalid_id]
    assert user_id is not None
    raw_updates = frame.get("updates")
    if not isinstance(raw_updates, dict):
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_USERS_UPDATE} request: 'updates' must be an object",
            )
        ]
    request, invalid = _validate_request(
        UserUpdate, raw_updates, frame_id, OP_USERS_UPDATE
    )
    if invalid is not None:
        return [invalid]
    assert request is not None

    async def _update(session: Any) -> dict[str, Any]:
        updated = await update_user(
            session,
            user_id=user_id,
            email=str(request.email) if request.email is not None else None,
            name=request.name,
            password=request.password,
            is_active=request.is_active,
            is_superuser=request.is_superuser,
            is_verified=request.is_verified,
            is_external=request.is_external,
            mfa_enabled=request.mfa_enabled,
            organization_id=request.organization_id,
        )
        # The HTTP dependency commits after the handler returns; the
        # local session never commits itself.
        await session.commit()
        return updated.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _update,
        op=OP_USERS_UPDATE,
        log_key=user_id,
        status_errors=(UserServiceError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_users_delete(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``users.delete`` through the shared users service.

    Platform-admin gate first (403, like the HTTP dependency), then
    the ``user_id`` field, then the same
    ``shared.sdk_users.delete_user`` the handler calls — the
    self-delete 400 (checked before existence, like the service), the
    missing-user 404 the facade maps to ``ValueError``, and the system
    user 403 are identical by construction. Actor identity comes only
    from the parent-derived principal (the engine sentinel id and
    email, like the HTTP engine token) — never from child frames, so
    self-deletion protection decides exactly like the engine-token
    HTTP path. Returns no body (null result, like HTTP 204). The
    dispatcher commits explicitly (the shared service only flushes;
    HTTP commits via ``get_db``). A local attempt never retries over
    HTTP.
    """
    from shared.sdk_users import UserServiceError, delete_user

    denied = _require_platform_admin(principal, frame_id)
    if denied is not None:
        return [denied]
    user_id, invalid = _require_users_field(
        frame, "user_id", frame_id, OP_USERS_DELETE
    )
    if invalid is not None:
        return [invalid]
    assert user_id is not None
    actor_error = _require_actor(principal, frame_id, OP_USERS_DELETE)
    if actor_error is not None:
        return [actor_error]
    assert principal.actor_email is not None
    actor_user_id = _users_actor_user_id(principal)

    async def _delete(session: Any) -> None:
        await delete_user(
            session,
            user_id=user_id,
            actor_user_id=actor_user_id,
            actor_email=principal.actor_email,
        )
        # The HTTP dependency commits after the handler returns; the
        # local session never commits itself.
        await session.commit()

    _, error = await _run_short(
        session_factory,
        _delete,
        op=OP_USERS_DELETE,
        log_key=user_id,
        status_errors=(UserServiceError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _single_ok(frame_id, None)


# =============================================================================
# SDK organizations facade
# (create/get/list/update/delete)
# =============================================================================


def _parse_organizations_org_id(
    frame: dict[str, Any], frame_id: str | None, op: str
) -> tuple[UUID | None, dict[str, Any] | None]:
    """One organization-id frame field (UUID), else an HTTP-style 422.

    Mirrors the route's FastAPI path parsing: a missing or malformed id
    is a 422, never a 404.
    """
    raw_id = frame.get("org_id")
    org_uuid: UUID | None = None
    if isinstance(raw_id, str) and raw_id.strip():
        try:
            org_uuid = UUID(raw_id)
        except ValueError:
            org_uuid = None
    if org_uuid is None:
        return None, _error(
            frame_id,
            422,
            f"invalid {op} request: 'org_id' must be a UUID",
        )
    return org_uuid, None


async def _dispatch_organizations_create(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``organizations.create`` through the shared organizations service.

    Platform-admin gate first (403, like the HTTP dependency), then
    the same ``OrganizationCreate`` DTO the HTTP handler uses (422),
    then the same ``shared.sdk_organizations.create_organization`` the
    handler calls, so response fields, actor attribution, audit, and
    cache updates are identical by construction. The actor email comes
    only from the parent-derived principal — never from child frames.
    The dispatcher commits explicitly (the shared service only flushes;
    HTTP commits via ``get_db``). A local attempt never retries over
    HTTP.
    """
    from shared.sdk_organizations import (
        OrganizationServiceError,
        create_organization,
    )
    from src.models import OrganizationCreate

    denied = _require_platform_admin(principal, frame_id)
    if denied is not None:
        return [denied]
    request, invalid = _validate_request(
        OrganizationCreate,
        {
            "name": frame.get("name"),
            "domain": frame.get("domain"),
            "is_active": frame.get("is_active", True),
        },
        frame_id,
        OP_ORGANIZATIONS_CREATE,
    )
    if invalid is not None:
        return [invalid]
    actor_error = _require_actor(principal, frame_id, OP_ORGANIZATIONS_CREATE)
    if actor_error is not None:
        return [actor_error]
    assert principal.actor_email is not None

    async def _create(session: Any) -> dict[str, Any]:
        org = await create_organization(
            session,
            name=request.name,
            domain=request.domain,
            is_active=request.is_active,
            settings=request.settings,
            actor_email=principal.actor_email,
        )
        # The HTTP dependency commits after the handler returns; the
        # local session never commits itself.
        await session.commit()
        return org.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _create,
        op=OP_ORGANIZATIONS_CREATE,
        log_key=request.name,
        status_errors=(OrganizationServiceError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_organizations_get(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``organizations.get`` through the shared organizations service.

    Platform-admin gate first (403, like the HTTP dependency), then
    org-id UUID parsing (422, like the route's FastAPI parsing), then
    the same ``shared.sdk_organizations.get_organization`` the handler
    calls — the missing-organization 404 the facade maps to
    ``ValueError`` is identical by construction. A local attempt never
    retries over HTTP.
    """
    from shared.sdk_organizations import OrganizationServiceError, get_organization

    denied = _require_platform_admin(principal, frame_id)
    if denied is not None:
        return [denied]
    org_uuid, invalid = _parse_organizations_org_id(
        frame, frame_id, OP_ORGANIZATIONS_GET
    )
    if invalid is not None:
        return [invalid]
    assert org_uuid is not None

    async def _get(session: Any) -> dict[str, Any]:
        org = await get_organization(session, org_id=org_uuid)
        return org.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _get,
        op=OP_ORGANIZATIONS_GET,
        log_key=str(org_uuid),
        status_errors=(OrganizationServiceError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_organizations_list(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``organizations.list`` through the shared organizations service.

    The frame carries no fields (the SDK exposes no filter) — the parent
    applies the route defaults (active only, provider first then active
    then alphabetical), like the unfiltered HTTP call. Platform-admin
    gate first (403), then the same
    ``shared.sdk_organizations.list_organizations`` the handler calls.
    Returns the ``{"items"}`` envelope (the transport result contract
    does not carry bare lists). Large listings ride bounded chunked
    frames via ``_ok_frames``. A local attempt never retries over HTTP.
    """
    from shared.sdk_organizations import (
        OrganizationServiceError,
        list_organizations,
    )

    denied = _require_platform_admin(principal, frame_id)
    if denied is not None:
        return [denied]

    async def _list(session: Any) -> dict[str, Any]:
        items = await list_organizations(session)
        return {"items": [item.model_dump(mode="json") for item in items]}

    result, error = await _run_short(
        session_factory,
        _list,
        op=OP_ORGANIZATIONS_LIST,
        log_key="organizations",
        status_errors=(OrganizationServiceError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_organizations_update(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``organizations.update`` through the shared organizations service.

    Platform-admin gate first (403, like the HTTP dependency), then
    org-id UUID parsing (422) and the same ``OrganizationUpdate`` DTO
    the HTTP handler uses (422 — unknown fields ignored, only non-None
    fields applied), then the same
    ``shared.sdk_organizations.update_organization`` the handler calls.
    The missing-organization 404 the facade maps to ``ValueError``
    and the provider-disable 403 are identical by construction. The
    dispatcher commits explicitly (the shared service only flushes;
    HTTP commits via ``get_db``). A local attempt never retries over
    HTTP.
    """
    from shared.sdk_organizations import (
        OrganizationServiceError,
        update_organization,
    )
    from src.models import OrganizationUpdate

    denied = _require_platform_admin(principal, frame_id)
    if denied is not None:
        return [denied]
    org_uuid, invalid_id = _parse_organizations_org_id(
        frame, frame_id, OP_ORGANIZATIONS_UPDATE
    )
    if invalid_id is not None:
        return [invalid_id]
    assert org_uuid is not None
    raw_updates = frame.get("updates")
    if not isinstance(raw_updates, dict):
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_ORGANIZATIONS_UPDATE} request: 'updates' must be an object",
            )
        ]
    request, invalid = _validate_request(
        OrganizationUpdate, raw_updates, frame_id, OP_ORGANIZATIONS_UPDATE
    )
    if invalid is not None:
        return [invalid]
    assert request is not None

    async def _update(session: Any) -> dict[str, Any]:
        org = await update_organization(
            session,
            org_id=org_uuid,
            name=request.name,
            domain=request.domain,
            is_active=request.is_active,
            settings=request.settings,
        )
        # The HTTP dependency commits after the handler returns; the
        # local session never commits itself.
        await session.commit()
        return org.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _update,
        op=OP_ORGANIZATIONS_UPDATE,
        log_key=str(org_uuid),
        status_errors=(OrganizationServiceError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_organizations_delete(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``organizations.delete`` through the shared organizations service.

    Platform-admin gate first (403), then org-id UUID parsing (422),
    then the same ``shared.sdk_organizations.delete_organization`` the
    handler calls — the missing-organization 404 the facade maps to
    ``ValueError`` and the provider-organization 403 are identical by
    construction. Returns no body (null result, like HTTP 204 — the
    facade maps it to ``True``). The dispatcher commits explicitly (the
    shared service only flushes; HTTP commits via ``get_db``). A local
    attempt never retries over HTTP.
    """
    from shared.sdk_organizations import (
        OrganizationServiceError,
        delete_organization,
    )

    denied = _require_platform_admin(principal, frame_id)
    if denied is not None:
        return [denied]
    org_uuid, invalid = _parse_organizations_org_id(
        frame, frame_id, OP_ORGANIZATIONS_DELETE
    )
    if invalid is not None:
        return [invalid]
    assert org_uuid is not None

    async def _delete(session: Any) -> None:
        await delete_organization(session, org_id=org_uuid)
        # The HTTP dependency commits after the handler returns; the
        # local session never commits itself.
        await session.commit()

    _, error = await _run_short(
        session_factory,
        _delete,
        op=OP_ORGANIZATIONS_DELETE,
        log_key=str(org_uuid),
        status_errors=(OrganizationServiceError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _single_ok(frame_id, None)


# =============================================================================
# SDK workflow/execution reads
# (workflows.list, executions.list, executions.get)
# =============================================================================


def _optional_reads_str(
    frame: dict[str, Any], field: str, frame_id: str | None, op: str
) -> tuple[str | None, dict[str, Any] | None]:
    """One optional string frame field (None when absent or null), else a 422."""
    value = frame.get(field)
    if value is None:
        return None, None
    if isinstance(value, str):
        return value, None
    return (
        None,
        _error(frame_id, 422, f"invalid {op} request: {field!r} must be a string"),
    )


def _optional_reads_uuid(
    frame: dict[str, Any],
    field: str,
    frame_id: str | None,
    op: str,
    detail: str,
) -> tuple[UUID | None, dict[str, Any] | None]:
    """One optional UUID frame field (None when absent or null), else a 422.

    ``detail`` carries the route's message so local validation matches
    HTTP: the executions route rejects a malformed ``workflowId`` with
    ``"workflowId must be a UUID"``; the workflows route's FastAPI
    parsing rejects malformed entity filters the same way.
    """
    value = frame.get(field)
    if value is None:
        return None, None
    parsed: UUID | None = None
    if isinstance(value, str) and value.strip():
        try:
            parsed = UUID(value)
        except ValueError:
            parsed = None
    if parsed is None:
        return None, _error(frame_id, 422, detail)
    return parsed, None


_READ_BOOL_TRUE = frozenset({"1", "true", "yes", "on"})
_READ_BOOL_FALSE = frozenset({"0", "false", "no", "off"})


def _optional_reads_bool(
    frame: dict[str, Any],
    field: str,
    frame_id: str | None,
    op: str,
    detail: str,
) -> tuple[bool | None, dict[str, Any] | None]:
    """One optional boolean frame field, else a 422 like the HTTP route.

    Accepts native booleans plus the same ``1/true/yes/on`` and
    ``0/false/no/off`` spellings (case-insensitive) the executions
    route's ``excludeLocal`` parsing accepts.
    """
    value = frame.get(field)
    if value is None:
        return None, None
    if isinstance(value, bool):
        return value, None
    if isinstance(value, str):
        lowered = value.casefold()
        if lowered in _READ_BOOL_TRUE:
            return True, None
        if lowered in _READ_BOOL_FALSE:
            return False, None
    return None, _error(frame_id, 422, detail)


def _optional_reads_iso_date(
    frame: dict[str, Any],
    field: str,
    frame_id: str | None,
    http_name: str,
) -> tuple[str | None, dict[str, Any] | None]:
    """One optional ISO-8601 date frame field, else a 422 like the HTTP route.

    Uses the route's ``datetime.fromisoformat`` check (after the same
    ``Z``-suffix normalization) and its ``"<name> must be an ISO 8601
    date-time"`` message. The validated string passes through to the
    shared service, which applies the same range filter as HTTP.
    """
    from datetime import datetime

    value = frame.get(field)
    if value is None:
        return None, None
    if isinstance(value, str):
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return value, None
        except ValueError:
            pass
    return (
        None,
        _error(frame_id, 422, f"{http_name} must be an ISO 8601 date-time"),
    )


async def _dispatch_workflows_list(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``workflows.list`` through the shared execution-reads service.

    Filter fields mirror the ``GET /api/workflows`` query string
    (``type``/``is_tool``/``scope``/``filter_by_form``/``filter_by_app``/
    ``filter_by_agent``); the SDK facade sends none, so the parent
    applies the route defaults like the unfiltered HTTP call. Entity
    filters validate as UUIDs (422, like the route's FastAPI parsing);
    ``is_tool`` accepts native booleans plus the HTTP boolean
    spellings. Scope, authorization (superuser-only), used-by counts,
    and metadata serialization are the shared service's — identical to
    HTTP by construction. Actor, org, and Solution scope come only
    from the parent-derived principal: the request runs under the
    token-equivalent engine/service user. Large listings ride bounded
    chunked frames via ``_ok_frames``. A local attempt never retries
    over HTTP.
    """
    from shared.sdk_execution_reads import SdkExecutionReadError, list_sdk_workflows

    workflow_type, invalid = _optional_reads_str(
        frame, "type", frame_id, OP_WORKFLOWS_LIST
    )
    if invalid is not None:
        return [invalid]
    is_tool, invalid = _optional_reads_bool(
        frame,
        "is_tool",
        frame_id,
        OP_WORKFLOWS_LIST,
        f"invalid {OP_WORKFLOWS_LIST} request: 'is_tool' must be a boolean",
    )
    if invalid is not None:
        return [invalid]
    scope, invalid = _optional_reads_str(frame, "scope", frame_id, OP_WORKFLOWS_LIST)
    if invalid is not None:
        return [invalid]
    filter_by_form, invalid = _optional_reads_uuid(
        frame,
        "filter_by_form",
        frame_id,
        OP_WORKFLOWS_LIST,
        f"invalid {OP_WORKFLOWS_LIST} request: 'filter_by_form' must be a UUID",
    )
    if invalid is not None:
        return [invalid]
    filter_by_app, invalid = _optional_reads_uuid(
        frame,
        "filter_by_app",
        frame_id,
        OP_WORKFLOWS_LIST,
        f"invalid {OP_WORKFLOWS_LIST} request: 'filter_by_app' must be a UUID",
    )
    if invalid is not None:
        return [invalid]
    filter_by_agent, invalid = _optional_reads_uuid(
        frame,
        "filter_by_agent",
        frame_id,
        OP_WORKFLOWS_LIST,
        f"invalid {OP_WORKFLOWS_LIST} request: 'filter_by_agent' must be a UUID",
    )
    if invalid is not None:
        return [invalid]
    user = _workflow_user_for_principal(principal)

    async def _list(session: Any) -> dict[str, Any]:
        workflows = await list_sdk_workflows(
            session,
            user,
            type=workflow_type,
            is_tool=is_tool,
            scope=scope,
            filter_by_form=filter_by_form,
            filter_by_app=filter_by_app,
            filter_by_agent=filter_by_agent,
        )
        return {
            "items": [w.model_dump(mode="json") for w in workflows],
        }

    result, error = await _run_short(
        session_factory,
        _list,
        op=OP_WORKFLOWS_LIST,
        log_key="workflows",
        status_errors=(SdkExecutionReadError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_executions_list(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``executions.list`` through the shared execution-reads service.

    Frame fields mirror the ``GET /api/executions`` query string, and
    validation mirrors the route: malformed ``workflow_id``,
    ``start_date``/``end_date``, ``exclude_local``, ``limit``, and
    ``continuation_token`` values are 422s with the route's messages;
    an absent ``exclude_local`` defaults to true and an absent
    ``limit`` to 25, like the route defaults. The continuation token
    decodes with the shared keyset cursor (legacy numeric-offset
    fallback, like the route). Scope, ownership, status mapping, and
    summary serialization are the shared service's — identical to HTTP
    by construction. Actor, org, and Solution scope come only from the
    parent-derived principal: the request runs under the
    token-equivalent engine/service user, never from child claims.
    Large listings ride bounded chunked frames via ``_ok_frames``. A
    local attempt never retries over HTTP.
    """
    from shared.sdk_execution_reads import (
        SdkExecutionReadError,
        decode_history_cursor,
        list_sdk_executions,
    )

    scope, invalid = _optional_reads_str(
        frame, "scope", frame_id, OP_EXECUTIONS_LIST
    )
    if invalid is not None:
        return [invalid]
    workflow_name, invalid = _optional_reads_str(
        frame, "workflow_name", frame_id, OP_EXECUTIONS_LIST
    )
    if invalid is not None:
        return [invalid]
    workflow_id, invalid = _optional_reads_uuid(
        frame,
        "workflow_id",
        frame_id,
        OP_EXECUTIONS_LIST,
        "workflowId must be a UUID",
    )
    if invalid is not None:
        return [invalid]
    status_filter, invalid = _optional_reads_str(
        frame, "status", frame_id, OP_EXECUTIONS_LIST
    )
    if invalid is not None:
        return [invalid]
    start_date, invalid = _optional_reads_iso_date(
        frame, "start_date", frame_id, "startDate"
    )
    if invalid is not None:
        return [invalid]
    end_date, invalid = _optional_reads_iso_date(
        frame, "end_date", frame_id, "endDate"
    )
    if invalid is not None:
        return [invalid]
    exclude_local, invalid = _optional_reads_bool(
        frame,
        "exclude_local",
        frame_id,
        OP_EXECUTIONS_LIST,
        "excludeLocal must be a boolean",
    )
    if invalid is not None:
        return [invalid]
    if exclude_local is None:
        exclude_local = True

    raw_limit = frame.get("limit")
    if raw_limit is None:
        limit = 25
    elif (
        isinstance(raw_limit, bool)
        or not isinstance(raw_limit, int)
        or not 1 <= raw_limit <= 1000
    ):
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_EXECUTIONS_LIST} request: "
                "'limit' must be an integer between 1 and 1000",
            )
        ]
    else:
        limit = raw_limit

    raw_token = frame.get("continuation_token")
    if raw_token is None or raw_token == "":
        cursor, offset = None, 0
    elif not isinstance(raw_token, str):
        return [
            _error(frame_id, 422, "continuationToken is invalid"),
        ]
    else:
        cursor = decode_history_cursor(raw_token)
        offset = 0
        if cursor is None:
            try:
                offset = int(raw_token)
            except ValueError:
                return [
                    _error(frame_id, 422, "continuationToken is invalid"),
                ]
            if offset < 0:
                return [
                    _error(frame_id, 422, "continuationToken is invalid"),
                ]

    user = _workflow_user_for_principal(principal)

    async def _list(session: Any) -> dict[str, Any]:
        summaries, next_token = await list_sdk_executions(
            session,
            user,
            scope=scope,
            workflow_name=workflow_name,
            workflow_id=workflow_id,
            status_filter=status_filter,
            start_date=start_date,
            end_date=end_date,
            exclude_local=exclude_local,
            limit=limit,
            offset=offset,
            cursor=cursor,
        )
        return {
            "executions": [s.model_dump(mode="json") for s in summaries],
            "continuation_token": next_token,
        }

    result, error = await _run_short(
        session_factory,
        _list,
        op=OP_EXECUTIONS_LIST,
        log_key=workflow_name or "",
        status_errors=(SdkExecutionReadError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_executions_get(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``executions.get`` through the shared execution-reads service.

    Also serves ``workflows.get``, which delegates to ``executions.get``
    in the SDK. The execution id validates as a UUID (422 on
    missing/malformed, like the HTTP path-param parsing). Ownership,
    the Redis-pending fallback, log/AI-usage enrichment, and detail
    serialization are the shared service's — a missing row is a 404
    error frame the facade maps to ``ValueError``, a foreign row a 403
    the facade maps to ``PermissionError``. The request runs under the
    token-equivalent engine/service user built only from the
    parent-derived principal. Large details ride bounded chunked frames
    via ``_ok_frames``. A local attempt never retries over HTTP.
    """
    from shared.sdk_execution_reads import SdkExecutionReadError, get_sdk_execution

    raw_id = frame.get("execution_id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_EXECUTIONS_GET} request: 'execution_id' is required",
            )
        ]
    try:
        execution_uuid = UUID(raw_id)
    except ValueError:
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_EXECUTIONS_GET} request: 'execution_id' must be a UUID",
            )
        ]
    user = _workflow_user_for_principal(principal)

    async def _get(session: Any) -> dict[str, Any]:
        detail = await get_sdk_execution(session, user, execution_uuid)
        return detail.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _get,
        op=OP_EXECUTIONS_GET,
        log_key=raw_id,
        status_errors=(SdkExecutionReadError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


def _workflow_user_for_principal(principal: LocalDispatchPrincipal) -> Any:
    """Token-equivalent ``UserPrincipal`` for SDK workflow execute/cancel.

    Reuses the table token-equivalent shape so workflow lookup, role
    checks, Solution scope, org override / ``run_as`` rules, and cancel
    attribution decide identically on both transports: workflows run as
    the system-user superuser with the signed engine claims (the
    ``mint_engine_token()`` shape), services run as the system-user
    non-superuser confined to their service org (the
    ``mint_service_token()`` shape). Built only from the parent-derived
    ``LocalDispatchPrincipal`` — never from child frame fields.
    """
    return _table_user_for_principal(principal)


async def _dispatch_workflows_execute(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``workflows.execute`` through the shared workflow service.

    Validates with the same ``WorkflowExecutionRequest`` DTO as the HTTP
    handler, so required-field, mutual-exclusion, and scheduling rules
    match (422, like HTTP). The frame carries the same fields the HTTP
    payload carries; ``sync`` is fixed to false — the SDK surface is
    fire-and-forget. The actor, org, and Solution caller come only from
    the parent-derived principal: the request runs under the
    token-equivalent engine/service user, the execution org defaults to
    the parent caller org, and the engine ``?solution=``/app-header
    context is unset (an engine child sends neither — the per-call
    target rides the frame's ``solution`` field; the caller's own install
    comes from the parent principal). A
    requested ``org_id``/``run_as`` override still passes through the
    shared service's authorization check, and the Solution inbound gate
    attests the caller from the signed engine claims. Malformed
    scope/UUID inputs map to 422 (the global ``ValueError`` handler on
    HTTP). The queue service owns its durable transaction; this
    dispatcher adds no commit. A local attempt never retries over HTTP.
    """
    from shared.sdk_workflow_execution import (
        SdkWorkflowExecutionError,
        execute_sdk_workflow,
    )
    from src.models import WorkflowExecutionRequest

    request, invalid = _validate_request(
        WorkflowExecutionRequest,
        {
            "workflow_id": frame.get("workflow"),
            "input_data": frame.get("input_data", {}),
            "solution_id": frame.get("solution"),
            "caller_solution_id": (
                str(principal.solution_id) if principal.solution_id else None
            ),
            "org_id": frame.get("org_id"),
            "run_as": frame.get("run_as"),
            "scheduled_at": frame.get("scheduled_at"),
            "delay_seconds": frame.get("delay_seconds"),
            "sync": False,
        },
        frame_id,
        OP_WORKFLOWS_EXECUTE,
    )
    if invalid is not None:
        return [invalid]
    assert request is not None
    user = _workflow_user_for_principal(principal)

    async def _execute(session: Any) -> dict[str, Any]:
        try:
            result = await execute_sdk_workflow(
                session,
                user,
                request,
                caller_org_id=principal.caller_org_id,
                context_solution_id=None,
                context_app_id=None,
                context_caller_solution_id=None,
            )
        except ValueError as e:
            # Malformed scope/UUID inputs reach the global 422 handler on
            # HTTP; map them to 422 here.
            raise SdkWorkflowExecutionError(422, str(e)) from e
        return result.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _execute,
        op=OP_WORKFLOWS_EXECUTE,
        log_key=request.workflow_id or "",
        status_errors=(SdkWorkflowExecutionError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_workflows_cancel(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``workflows.cancel`` through the shared workflow service.

    The execution id validates as a UUID (422 on malformed, like the
    HTTP path-param parsing). Org/submitter checks and the
    status-guarded UPDATE race are the shared service's — a missing row
    is a 404 error frame, a foreign row a 403, and a non-scheduled row
    a 409 carrying the current status. A local attempt never retries
    over HTTP.
    """
    from shared.sdk_workflow_execution import (
        SdkWorkflowExecutionError,
        cancel_scheduled_sdk_execution,
    )

    raw_id = frame.get("execution_id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_WORKFLOWS_CANCEL} request: 'execution_id' is required",
            )
        ]
    try:
        execution_uuid = UUID(raw_id)
    except ValueError:
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_WORKFLOWS_CANCEL} request: 'execution_id' must be a UUID",
            )
        ]
    user = _workflow_user_for_principal(principal)

    async def _cancel(session: Any) -> dict[str, Any]:
        return await cancel_scheduled_sdk_execution(
            session,
            user,
            execution_uuid,
            caller_org_id=principal.caller_org_id,
        )

    result, error = await _run_short(
        session_factory,
        _cancel,
        op=OP_WORKFLOWS_CANCEL,
        log_key=raw_id,
        status_errors=(SdkWorkflowExecutionError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _single_ok(frame_id, result)


def _tables_target_solution_id(
    frame_solution: Any, principal: LocalDispatchPrincipal
) -> str | None:
    """Per-call target install ref for one table frame.

    The child-supplied ``solution`` is only ever a *target* (UUID or
    slug/name, resolved inside the target org downstream). Unset or blank
    inherits the parent-owned own install. The caller's install identity
    itself always comes from the principal — never from the frame.
    """
    if isinstance(frame_solution, str) and frame_solution.strip():
        return frame_solution
    if principal.solution_id is not None:
        return str(principal.solution_id)
    return None


def _required_tables_field(
    frame: dict[str, Any], field: str, frame_id: str | None, op: str
) -> tuple[str | None, dict[str, Any] | None]:
    """One required non-empty string frame field, else a 422 error frame."""
    value = frame.get(field)
    if isinstance(value, str) and value.strip():
        return value, None
    return None, _error(frame_id, 422, f"invalid {op} request: {field!r} is required")


def _optional_tables_field(
    frame: dict[str, Any], field: str, frame_id: str | None, op: str
) -> tuple[str | None, dict[str, Any] | None]:
    """One optional string frame field (None when absent), else a 422."""
    value = frame.get(field)
    if value is None:
        return None, None
    if isinstance(value, str):
        return value, None
    return None, _error(frame_id, 422, f"invalid {op} request: {field!r} must be a string")


async def _dispatch_tables_get(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``tables.get`` through the shared resolution + read services.

    The parent resolves the table and enforces the read policy with the
    token-equivalent principal on one short session — the same
    ``shared.table_resolution`` + ``shared.table_documents`` calls the HTTP
    handlers make. The service's actual 404/403 rides the error frame; the
    SDK facade maps a 404 to ``None``.
    """
    from fastapi import HTTPException

    table_ref, invalid = _required_tables_field(frame, "table", frame_id, OP_TABLES_GET)
    if invalid is not None:
        return [invalid]
    doc_id, invalid = _required_tables_field(frame, "doc_id", frame_id, OP_TABLES_GET)
    if invalid is not None:
        return [invalid]
    scope, invalid = _optional_tables_field(frame, "scope", frame_id, OP_TABLES_GET)
    if invalid is not None:
        return [invalid]
    solution, invalid = _optional_tables_field(
        frame, "solution", frame_id, OP_TABLES_GET
    )
    if invalid is not None:
        return [invalid]
    assert table_ref is not None and doc_id is not None

    async def _get(session: Any) -> dict[str, Any]:
        from shared.table_documents import get_table_document
        from shared.table_resolution import LocalTableContext, get_table_or_404

        user = _table_user_for_principal(principal)
        ctx = LocalTableContext(
            db=session,
            user=user,
            org_id=principal.caller_org_id,
            solution_id=_tables_target_solution_id(solution, principal),
        )
        table = await get_table_or_404(ctx, table_ref, scope=scope)
        doc = await get_table_document(session, table, doc_id, user)
        return doc.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _get,
        op=OP_TABLES_GET,
        log_key=table_ref,
        status_errors=(HTTPException,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_tables_query(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``tables.query`` through the shared resolution + read services.

    The query payload validates with the same ``DocumentQuery`` DTO as the
    HTTP handler, so coercion, defaults, and pagination limits match. The
    service's actual 404/403 rides the error frame; the SDK facade maps a
    404 to an empty ``DocumentList``.
    """
    from fastapi import HTTPException

    from src.models.contracts.tables import DocumentQuery

    table_ref, invalid = _required_tables_field(
        frame, "table", frame_id, OP_TABLES_QUERY
    )
    if invalid is not None:
        return [invalid]
    scope, invalid = _optional_tables_field(frame, "scope", frame_id, OP_TABLES_QUERY)
    if invalid is not None:
        return [invalid]
    solution, invalid = _optional_tables_field(
        frame, "solution", frame_id, OP_TABLES_QUERY
    )
    if invalid is not None:
        return [invalid]
    raw_query = frame.get("query")
    if raw_query is None:
        raw_query = {}
    if not isinstance(raw_query, dict):
        return [_error(frame_id, 422, f"invalid {OP_TABLES_QUERY} request: 'query' must be an object")]
    request, invalid = _validate_request(
        DocumentQuery, raw_query, frame_id, OP_TABLES_QUERY
    )
    if invalid is not None:
        return [invalid]
    assert table_ref is not None and request is not None

    async def _query(session: Any) -> dict[str, Any]:
        from shared.table_documents import query_table_documents
        from shared.table_resolution import LocalTableContext, get_table_or_404

        user = _table_user_for_principal(principal)
        ctx = LocalTableContext(
            db=session,
            user=user,
            org_id=principal.caller_org_id,
            solution_id=_tables_target_solution_id(solution, principal),
        )
        table = await get_table_or_404(ctx, table_ref, scope=scope)
        response = await query_table_documents(session, table, request, user)
        return response.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _query,
        op=OP_TABLES_QUERY,
        log_key=table_ref,
        status_errors=(HTTPException,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_tables_count(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve the unfiltered ``tables.count`` through the shared services.

    Only the unfiltered count rides this operation (returned as
    ``{"count": n}`` — the transport result contract does not carry bare
    integers). A filtered count stays composed through the public
    ``tables.query(limit=1)`` in the SDK facade, exactly like the HTTP path.
    """
    from fastapi import HTTPException

    table_ref, invalid = _required_tables_field(
        frame, "table", frame_id, OP_TABLES_COUNT
    )
    if invalid is not None:
        return [invalid]
    scope, invalid = _optional_tables_field(frame, "scope", frame_id, OP_TABLES_COUNT)
    if invalid is not None:
        return [invalid]
    solution, invalid = _optional_tables_field(
        frame, "solution", frame_id, OP_TABLES_COUNT
    )
    if invalid is not None:
        return [invalid]
    assert table_ref is not None

    async def _count(session: Any) -> dict[str, Any]:
        from shared.table_documents import count_table_documents
        from shared.table_resolution import LocalTableContext, get_table_or_404

        user = _table_user_for_principal(principal)
        ctx = LocalTableContext(
            db=session,
            user=user,
            org_id=principal.caller_org_id,
            solution_id=_tables_target_solution_id(solution, principal),
        )
        table = await get_table_or_404(ctx, table_ref, scope=scope)
        response = await count_table_documents(session, table, user)
        return response.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _count,
        op=OP_TABLES_COUNT,
        log_key=table_ref,
        status_errors=(HTTPException,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_tables_create(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``tables.create`` through the shared metadata service.

    Validates with the same ``SDKTableCreateRequest`` DTO as the HTTP
    handler, keeps the Solution-restriction-before-scope ordering, and
    calls the same ``create_sdk_table`` service. ``solution_present``
    comes only from the principal — a Solution execution cannot conjure
    tables ad hoc on either transport. The facade's per-call ``app``
    argument is not read: the HTTP DTO has no such field, so the server
    ignores it there too.
    """
    from shared.sdk_table_metadata import (
        SDKTableMetadataError,
        create_sdk_table,
        ensure_sdk_table_create_allowed,
    )
    from src.models.contracts.cli import SDKTableCreateRequest, SDKTableInfo

    request, invalid = _validate_request(
        SDKTableCreateRequest,
        {
            "name": frame.get("name"),
            "table_schema": frame.get("table_schema"),
            "description": frame.get("description"),
            "scope": frame.get("scope"),
        },
        frame_id,
        OP_TABLES_CREATE,
    )
    if invalid is not None:
        return [invalid]
    actor_error = _require_actor(principal, frame_id, OP_TABLES_CREATE)
    if actor_error is not None:
        return [actor_error]
    assert principal.actor_email is not None and request is not None
    solution_present = principal.solution_id is not None
    try:
        ensure_sdk_table_create_allowed(solution_present)
    except SDKTableMetadataError as e:
        return [_error(frame_id, e.status_code, e.detail)]
    resolved_org_id, scope_error = await _resolve_frame_scope(
        principal, frame_id, {"scope": request.scope}, session_factory
    )
    if scope_error is not None:
        return [scope_error]

    async def _create(session: Any) -> dict[str, Any]:
        result = await create_sdk_table(
            session,
            name=request.name,
            table_schema=request.table_schema,
            description=request.description,
            org_id=resolved_org_id,
            actor_email=principal.actor_email,
            solution_present=solution_present,
        )
        return SDKTableInfo(**result).model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _create,
        op=OP_TABLES_CREATE,
        log_key=request.name,
        status_errors=(SDKTableMetadataError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_tables_list(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``tables.list`` through the shared metadata service.

    Same DTO, same scope resolution, same external-sentinel handling as
    the HTTP handler. The items ride an ``{"items"}`` envelope — the
    transport result contract does not carry bare lists.
    """
    from shared.sdk_table_metadata import list_sdk_tables
    from src.models.contracts.cli import SDKTableInfo, SDKTableListRequest

    request, invalid = _validate_request(
        SDKTableListRequest,
        {"scope": frame.get("scope")},
        frame_id,
        OP_TABLES_LIST,
    )
    if invalid is not None:
        return [invalid]
    assert request is not None
    resolved_org_id, scope_error = await _resolve_frame_scope(
        principal, frame_id, {"scope": request.scope}, session_factory
    )
    if scope_error is not None:
        return [scope_error]

    async def _list(session: Any) -> dict[str, Any]:
        items = await list_sdk_tables(
            session,
            org_id=resolved_org_id,
            external=principal.is_external,
        )
        return {
            "items": [
                SDKTableInfo(**item).model_dump(mode="json") for item in items
            ]
        }

    result, error = await _run_short(
        session_factory, _list, op=OP_TABLES_LIST, log_key=""
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_tables_delete(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``tables.delete`` through the shared metadata service.

    The HTTP route requires a platform-admin principal; the local
    equivalent is the workflow engine identity — supervised service
    children (system-user non-superuser) get a 403, exactly like their
    HTTP DELETE would. A missing table is a 404 error frame (the facade
    raises); a Solution-managed table is a 409.
    """
    from shared.sdk_table_metadata import SDKTableMetadataError, delete_sdk_table

    raw_id = frame.get("table_id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_TABLES_DELETE} request: 'table_id' is required",
            )
        ]
    try:
        table_uuid = UUID(raw_id)
    except ValueError:
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_TABLES_DELETE} request: 'table_id' must be a UUID",
            )
        ]
    if principal.is_service:
        return [_error(frame_id, 403, "Only platform admins can delete tables")]
    actor_error = _require_actor(principal, frame_id, OP_TABLES_DELETE)
    if actor_error is not None:
        return [actor_error]

    async def _delete(session: Any) -> bool:
        return await delete_sdk_table(
            session, table_id=table_uuid, org_id=principal.caller_org_id
        )

    result, error = await _run_short(
        session_factory,
        _delete,
        op=OP_TABLES_DELETE,
        log_key=raw_id,
        status_errors=(SDKTableMetadataError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    if not result:
        return [_error(frame_id, 404, f"Table '{table_uuid}' not found")]
    return _single_ok(frame_id, True)


async def _resolve_tables_write_target(
    session: Any,
    principal: LocalDispatchPrincipal,
    table_ref: str,
    *,
    scope: str | None,
    solution: str | None,
    require_explicit_scope_gate: bool = False,
) -> tuple[Any, Any, Any]:
    """Resolve and gate one write's target table on an open session.

    Builds the parent-owned ``LocalTableContext`` (token-equivalent
    user, caller org, per-call target install) and resolves through the
    shared ``get_table_or_404`` — org gating, Solution install fallback,
    and the inbound gate stay identical to the HTTP path. Then applies
    the same Solution write-target gate (and, for batch writes, the
    explicit-scope exact-table gate) the HTTP handlers apply.

    Returns ``(table, user, ctx)``. Resolution and gate failures raise
    ``HTTPException`` for the caller to map to error frames.
    """
    from shared.table_resolution import (
        LocalTableContext,
        assert_explicit_scope_targets_table,
        assert_solution_write_targets_owned_table,
        get_table_or_404,
    )

    user = _table_user_for_principal(principal)
    ctx = LocalTableContext(
        db=session,
        user=user,
        org_id=principal.caller_org_id,
        solution_id=_tables_target_solution_id(solution, principal),
    )
    table = await get_table_or_404(ctx, table_ref, scope=scope)
    await assert_solution_write_targets_owned_table(ctx, table)
    if require_explicit_scope_gate:
        await assert_explicit_scope_targets_table(ctx, table, scope)
    return table, user, ctx


async def _dispatch_tables_insert(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``tables.insert`` through the shared write service.

    The body validates with the same ``DocumentCreate`` DTO as the HTTP
    handler (plain insert — no upsert branch). A missing table is a 404
    error frame; the facade auto-creates outside a Solution and retries
    once, exactly like the HTTP path.
    """
    from fastapi import HTTPException

    from shared.table_document_writes import TableWriteError
    from src.models.contracts.tables import DocumentCreate, DocumentPublic

    table_ref, invalid = _required_tables_field(
        frame, "table", frame_id, OP_TABLES_INSERT
    )
    if invalid is not None:
        return [invalid]
    scope, invalid = _optional_tables_field(frame, "scope", frame_id, OP_TABLES_INSERT)
    if invalid is not None:
        return [invalid]
    solution, invalid = _optional_tables_field(
        frame, "solution", frame_id, OP_TABLES_INSERT
    )
    if invalid is not None:
        return [invalid]
    doc_id = frame.get("doc_id")
    if doc_id is not None and not isinstance(doc_id, str):
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_TABLES_INSERT} request: 'doc_id' must be a string",
            )
        ]
    request, invalid = _validate_request(
        DocumentCreate,
        {
            "id": doc_id,
            "data": frame.get("data"),
            "created_by": frame.get("created_by"),
            "updated_by": frame.get("updated_by"),
        },
        frame_id,
        OP_TABLES_INSERT,
    )
    if invalid is not None:
        return [invalid]
    assert table_ref is not None and request is not None
    actor_error = _require_actor(principal, frame_id, OP_TABLES_INSERT)
    if actor_error is not None:
        return [actor_error]

    async def _insert(session: Any) -> dict[str, Any]:
        from shared.table_document_writes import insert_table_document

        table, user, _ctx = await _resolve_tables_write_target(
            session, principal, table_ref, scope=scope, solution=solution
        )
        doc = await insert_table_document(
            session,
            table,
            user,
            doc_id=request.id,
            data=request.data,
            created_by=request.created_by,
            updated_by=request.updated_by,
            upsert=False,
        )
        return DocumentPublic.model_validate(doc).model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _insert,
        op=OP_TABLES_INSERT,
        log_key=table_ref,
        status_errors=(HTTPException, TableWriteError),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_tables_upsert(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``tables.upsert`` through the shared write service.

    Atomic replace-upsert by id (the JSONB ``data`` column is replaced,
    not merged), validated with the same ``DocumentUpsert`` DTO as the
    HTTP handler. A missing table is a 404 error frame for the facade's
    auto-create retry.
    """
    from fastapi import HTTPException

    from shared.table_document_writes import TableWriteError
    from src.models.contracts.tables import DocumentPublic, DocumentUpsert

    table_ref, invalid = _required_tables_field(
        frame, "table", frame_id, OP_TABLES_UPSERT
    )
    if invalid is not None:
        return [invalid]
    scope, invalid = _optional_tables_field(frame, "scope", frame_id, OP_TABLES_UPSERT)
    if invalid is not None:
        return [invalid]
    doc_id, invalid = _required_tables_field(
        frame, "doc_id", frame_id, OP_TABLES_UPSERT
    )
    if invalid is not None:
        return [invalid]
    request, invalid = _validate_request(
        DocumentUpsert,
        {
            "id": doc_id,
            "data": frame.get("data"),
            "created_by": frame.get("created_by"),
            "updated_by": frame.get("updated_by"),
        },
        frame_id,
        OP_TABLES_UPSERT,
    )
    if invalid is not None:
        return [invalid]
    assert table_ref is not None and doc_id is not None and request is not None
    actor_error = _require_actor(principal, frame_id, OP_TABLES_UPSERT)
    if actor_error is not None:
        return [actor_error]

    async def _upsert(session: Any) -> dict[str, Any]:
        from shared.table_document_writes import upsert_table_document

        table, user, _ctx = await _resolve_tables_write_target(
            session, principal, table_ref, scope=scope, solution=None
        )
        doc = await upsert_table_document(
            session,
            table,
            user,
            doc_id=request.id,
            data=request.data,
            created_by=request.created_by,
            updated_by=request.updated_by,
        )
        return DocumentPublic.model_validate(doc).model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _upsert,
        op=OP_TABLES_UPSERT,
        log_key=table_ref,
        status_errors=(HTTPException, TableWriteError),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_tables_update(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``tables.update`` through the shared write service.

    Partial merge-update validated with the same ``DocumentUpdate`` DTO
    as the HTTP handler. A missing table or row is a 404 error frame
    (the facade maps it to ``None``).
    """
    from fastapi import HTTPException

    from shared.table_document_writes import TableWriteError
    from src.models.contracts.tables import DocumentPublic, DocumentUpdate

    table_ref, invalid = _required_tables_field(
        frame, "table", frame_id, OP_TABLES_UPDATE
    )
    if invalid is not None:
        return [invalid]
    doc_id, invalid = _required_tables_field(
        frame, "doc_id", frame_id, OP_TABLES_UPDATE
    )
    if invalid is not None:
        return [invalid]
    scope, invalid = _optional_tables_field(frame, "scope", frame_id, OP_TABLES_UPDATE)
    if invalid is not None:
        return [invalid]
    request, invalid = _validate_request(
        DocumentUpdate,
        {"data": frame.get("data"), "updated_by": frame.get("updated_by")},
        frame_id,
        OP_TABLES_UPDATE,
    )
    if invalid is not None:
        return [invalid]
    assert table_ref is not None and doc_id is not None and request is not None
    actor_error = _require_actor(principal, frame_id, OP_TABLES_UPDATE)
    if actor_error is not None:
        return [actor_error]

    async def _update(session: Any) -> dict[str, Any]:
        from shared.table_document_writes import update_table_document

        table, user, _ctx = await _resolve_tables_write_target(
            session, principal, table_ref, scope=scope, solution=None
        )
        doc = await update_table_document(
            session,
            table,
            user,
            doc_id=doc_id,
            data=request.data,
            updated_by=request.updated_by,
        )
        return DocumentPublic.model_validate(doc).model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _update,
        op=OP_TABLES_UPDATE,
        log_key=table_ref,
        status_errors=(HTTPException, TableWriteError),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_tables_delete_document(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``tables.delete_document`` through the shared write service.

    A missing table or row is a 404 error frame (the facade maps it to
    ``False``).
    """
    from fastapi import HTTPException

    from shared.table_document_writes import TableWriteError

    table_ref, invalid = _required_tables_field(
        frame, "table", frame_id, OP_TABLES_DELETE_DOCUMENT
    )
    if invalid is not None:
        return [invalid]
    doc_id, invalid = _required_tables_field(
        frame, "doc_id", frame_id, OP_TABLES_DELETE_DOCUMENT
    )
    if invalid is not None:
        return [invalid]
    scope, invalid = _optional_tables_field(
        frame, "scope", frame_id, OP_TABLES_DELETE_DOCUMENT
    )
    if invalid is not None:
        return [invalid]
    assert table_ref is not None and doc_id is not None
    actor_error = _require_actor(principal, frame_id, OP_TABLES_DELETE_DOCUMENT)
    if actor_error is not None:
        return [actor_error]

    async def _delete(session: Any) -> bool:
        from shared.table_document_writes import delete_table_document

        table, user, _ctx = await _resolve_tables_write_target(
            session, principal, table_ref, scope=scope, solution=None
        )
        return await delete_table_document(session, table, user, doc_id=doc_id)

    result, error = await _run_short(
        session_factory,
        _delete,
        op=OP_TABLES_DELETE_DOCUMENT,
        log_key=table_ref,
        status_errors=(HTTPException, TableWriteError),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    assert result is True
    return _single_ok(frame_id, True)


async def _dispatch_tables_batch(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``tables.batch`` through the shared batch write service.

    One operation covers the facade's ``insert_batch`` (plain insert),
    ``upsert_batch`` (legacy merge upsert), and ``bulk_upsert``
    (privileged replace upsert): the body validates with the same
    ``DocumentBatchCreate`` DTO as the HTTP handler, so the 1000-row
    limit, explicit-id requirements, and write-mode compatibility match.
    The response carries the same ``DocumentBatchCreateResponse`` shape
    (insert conflicts listed, documents only when requested). A missing
    table is a 404 error frame for the facade's auto-create retry; a
    concurrent-write conflict is a 409 the facade retries boundedly.
    """
    from fastapi import HTTPException

    from shared.table_document_writes import TableWriteError
    from src.models.contracts.tables import DocumentBatchCreate, DocumentPublic

    table_ref, invalid = _required_tables_field(
        frame, "table", frame_id, OP_TABLES_BATCH
    )
    if invalid is not None:
        return [invalid]
    scope, invalid = _optional_tables_field(frame, "scope", frame_id, OP_TABLES_BATCH)
    if invalid is not None:
        return [invalid]
    raw_body: dict[str, Any] = {"documents": frame.get("documents")}
    for key in ("upsert", "write_mode", "return_documents"):
        if frame.get(key) is not None:
            raw_body[key] = frame[key]
    request, invalid = _validate_request(
        DocumentBatchCreate, raw_body, frame_id, OP_TABLES_BATCH
    )
    if invalid is not None:
        return [invalid]
    assert table_ref is not None and request is not None
    actor_error = _require_actor(principal, frame_id, OP_TABLES_BATCH)
    if actor_error is not None:
        return [actor_error]

    async def _batch(session: Any) -> dict[str, Any]:
        from shared.table_document_writes import (
            BatchDocumentInput,
            batch_write_table_documents,
        )

        table, user, _ctx = await _resolve_tables_write_target(
            session,
            principal,
            table_ref,
            scope=scope,
            solution=None,
            require_explicit_scope_gate=True,
        )
        outcome = await batch_write_table_documents(
            session,
            table,
            user,
            items=[
                BatchDocumentInput(
                    id=item.id,
                    data=item.data,
                    created_by=item.created_by,
                    updated_by=item.updated_by,
                )
                for item in request.documents
            ],
            mode=request.effective_write_mode,
        )
        return {
            "inserted": outcome.inserted,
            "errors": [
                {"id": conflict.id, "error": "Document already exists"}
                for conflict in outcome.insert_conflicts
            ],
            "documents": (
                [
                    DocumentPublic.model_validate(doc).model_dump(mode="json")
                    for doc in outcome.ordered_documents
                ]
                if request.return_documents
                else []
            ),
        }

    result, error = await _run_short(
        session_factory,
        _batch,
        op=OP_TABLES_BATCH,
        log_key=table_ref,
        status_errors=(HTTPException, TableWriteError),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_tables_batch_delete(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``tables.batch_delete`` through the shared write service.

    Same ``DocumentBatchDeleteRequest`` DTO and all-or-nothing policy
    behavior as the HTTP handler. A missing table is a 404 error frame
    (the facade maps it to an empty result).
    """
    from fastapi import HTTPException

    from shared.table_document_writes import TableWriteError
    from src.models.contracts.tables import DocumentBatchDeleteRequest

    table_ref, invalid = _required_tables_field(
        frame, "table", frame_id, OP_TABLES_BATCH_DELETE
    )
    if invalid is not None:
        return [invalid]
    scope, invalid = _optional_tables_field(
        frame, "scope", frame_id, OP_TABLES_BATCH_DELETE
    )
    if invalid is not None:
        return [invalid]
    request, invalid = _validate_request(
        DocumentBatchDeleteRequest,
        {"ids": frame.get("ids")},
        frame_id,
        OP_TABLES_BATCH_DELETE,
    )
    if invalid is not None:
        return [invalid]
    assert table_ref is not None and request is not None
    actor_error = _require_actor(principal, frame_id, OP_TABLES_BATCH_DELETE)
    if actor_error is not None:
        return [actor_error]

    async def _batch_delete(session: Any) -> dict[str, Any]:
        from shared.table_document_writes import batch_delete_table_documents

        table, user, _ctx = await _resolve_tables_write_target(
            session, principal, table_ref, scope=scope, solution=None
        )
        outcome = await batch_delete_table_documents(
            session, table, user, ids=list(request.ids)
        )
        return {"deleted": outcome.deleted, "deleted_ids": outcome.deleted_ids}

    result, error = await _run_short(
        session_factory,
        _batch_delete,
        op=OP_TABLES_BATCH_DELETE,
        log_key=table_ref,
        status_errors=(HTTPException, TableWriteError),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


def _artifact_user_for_principal(principal: LocalDispatchPrincipal) -> Any:
    """Token-equivalent ``UserPrincipal`` for artifact scope checks.

    Reuses the table token-equivalent shape so org gates decide
    identically on both transports: workflows run as the system-user
    superuser with no org (the ``mint_engine_token()`` shape — global
    scope), services run as the system-user non-superuser confined to
    their service org (the ``mint_service_token()`` shape). The
    ``shared.sdk_artifacts`` service applies the platform-admin bypass
    internally from ``is_superuser``, so the engine sees global scope
    while a service token stays org-scoped — exactly like HTTP.
    """
    return _table_user_for_principal(principal)


def _artifact_id_from_frame(
    frame: dict[str, Any], frame_id: str | None, op: str
) -> tuple[Any, dict[str, Any] | None]:
    """One required UUID frame field (artifact id), else a 422 error frame."""
    raw = frame.get("artifact_id")
    if not isinstance(raw, str) or not raw:
        return None, _error(frame_id, 422, f"invalid {op} request: 'artifact_id' is required")
    try:
        return UUID(raw), None
    except ValueError:
        return None, _error(frame_id, 422, f"invalid {op} request: 'artifact_id' is not a UUID")


def _workspace_id_from_frame(
    frame: dict[str, Any], frame_id: str | None, op: str, *, required: bool
) -> tuple[Any, dict[str, Any] | None]:
    """One optional (write) or required (list) UUID workspace field.

    Mirrors the HTTP query-param validation (FastAPI UUID → 422 on
    malformed). A missing workspace on write means unscoped storage
    (like the HTTP default); a missing workspace on list is a 422.
    """
    raw = frame.get("workspace_id")
    if raw is None:
        if required:
            return None, _error(frame_id, 422, f"invalid {op} request: 'workspace_id' is required")
        return None, None
    if not isinstance(raw, str) or not raw:
        return None, _error(frame_id, 422, f"invalid {op} request: 'workspace_id' is required" if required else f"invalid {op} request: 'workspace_id' must be a UUID string")
    try:
        return UUID(raw), None
    except ValueError:
        return None, _error(frame_id, 422, f"invalid {op} request: 'workspace_id' is not a UUID")


async def _dispatch_artifacts_write(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``artifacts.write`` through the shared artifact service.

    Validates the frame (filename/content_type required, content base64,
    workspace optional UUID — 422 on malformed, like the HTTP query and
    multipart parsing), then calls the exact ``shared.sdk_artifacts``
    service the HTTP handler calls. Content validation failures
    (``ValueError``) map to 422 like the global ``ValueError → 422``
    handler; authorization misses map to their 404. Large contents ride
    bounded chunked response frames via ``_ok_frames``; large requests
    arrive reassembled by ``serve_channel`` (no total cap, like HTTP).
    """
    import base64 as _b64

    from shared.sdk_artifacts import ArtifactCaller, SdkArtifactError

    filename, invalid = _required_str_field(frame, "filename", frame_id, OP_ARTIFACTS_WRITE)
    if invalid is not None:
        return [invalid]
    content_type, invalid = _required_str_field(frame, "content_type", frame_id, OP_ARTIFACTS_WRITE)
    if invalid is not None:
        return [invalid]
    raw_content = frame.get("content")
    if not isinstance(raw_content, str) or not raw_content:
        return [_error(frame_id, 422, f"invalid {OP_ARTIFACTS_WRITE} request: 'content' is required")]
    try:
        content = _b64.b64decode(raw_content.encode("ascii"), validate=True)
    except Exception:
        return [_error(frame_id, 422, f"invalid {OP_ARTIFACTS_WRITE} request: 'content' is not valid base64")]
    workspace_id, invalid = _workspace_id_from_frame(frame, frame_id, OP_ARTIFACTS_WRITE, required=False)
    if invalid is not None:
        return [invalid]
    assert filename is not None and content_type is not None

    async def _store(session: Any) -> dict[str, Any]:
        from shared.sdk_artifacts import sdk_store_artifact

        user = _artifact_user_for_principal(principal)
        try:
            ref = await sdk_store_artifact(
                ArtifactCaller(user=user, db=session),
                filename=filename,
                content_type=content_type,
                content=content,
                workspace_id=workspace_id,
            )
        except ValueError as exc:
            raise SdkArtifactError(422, str(exc)) from exc
        # The HTTP dependency commits after the handler returns. The local
        # session factory only closes its session, so persist the artifact
        # before the child can read it in a subsequent SDK call.
        await session.commit()
        return ref.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _store,
        op=OP_ARTIFACTS_WRITE,
        log_key=filename,
        status_errors=(SdkArtifactError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_artifacts_read(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``artifacts.read`` through the shared artifact service.

    The SDK surfaces bytes only (like the HTTP body); the envelope
    carries base64 ``content`` plus ``content_type`` so the wire stays
    JSON. A missing or out-of-scope id is a 404 error frame — never a
    null result. Large contents ride bounded chunked frames.
    """
    import base64 as _b64

    from shared.sdk_artifacts import ArtifactCaller, SdkArtifactError

    artifact_id, invalid = _artifact_id_from_frame(frame, frame_id, OP_ARTIFACTS_READ)
    if invalid is not None:
        return [invalid]
    assert artifact_id is not None

    async def _read(session: Any) -> dict[str, Any]:
        from shared.sdk_artifacts import sdk_read_artifact

        user = _artifact_user_for_principal(principal)
        result = await sdk_read_artifact(
            ArtifactCaller(user=user, db=session),
            artifact_id=artifact_id,
        )
        return {
            "content": _b64.b64encode(result.content).decode("ascii"),
            "content_type": result.content_type,
        }

    result, error = await _run_short(
        session_factory,
        _read,
        op=OP_ARTIFACTS_READ,
        log_key=str(artifact_id),
        status_errors=(SdkArtifactError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_artifacts_list(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``artifacts.list`` through the shared artifact service.

    Returns the ``{"items": [...]}`` envelope of ``ArtifactRef`` dicts
    (the transport result contract does not carry bare lists). Scope
    filtering (owner-or-org, admin bypass) is the shared service's, so
    both paths agree by construction.
    """
    from shared.sdk_artifacts import ArtifactCaller

    workspace_id, invalid = _workspace_id_from_frame(frame, frame_id, OP_ARTIFACTS_LIST, required=True)
    if invalid is not None:
        return [invalid]
    assert workspace_id is not None

    async def _list(session: Any) -> dict[str, Any]:
        from shared.sdk_artifacts import sdk_list_artifacts

        user = _artifact_user_for_principal(principal)
        refs = await sdk_list_artifacts(
            ArtifactCaller(user=user, db=session),
            workspace_id=workspace_id,
        )
        return {"items": [ref.model_dump(mode="json") for ref in refs]}

    result, error = await _run_short(
        session_factory,
        _list,
        op=OP_ARTIFACTS_LIST,
        log_key=str(workspace_id),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_artifacts_download_url(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``artifacts.get_download_url`` through the shared service.

    Returns the ``{"url": ...}`` envelope, identical to the HTTP path
    (inert headers owned by the signed-URL path). A missing or
    out-of-scope id is a 404 error frame.
    """
    from shared.sdk_artifacts import ArtifactCaller, SdkArtifactError

    artifact_id, invalid = _artifact_id_from_frame(frame, frame_id, OP_ARTIFACTS_GET_DOWNLOAD_URL)
    if invalid is not None:
        return [invalid]
    assert artifact_id is not None

    async def _url(session: Any) -> dict[str, Any]:
        from shared.sdk_artifacts import sdk_artifact_download_url

        user = _artifact_user_for_principal(principal)
        response = await sdk_artifact_download_url(
            ArtifactCaller(user=user, db=session),
            artifact_id=artifact_id,
        )
        return {"url": response.url}

    result, error = await _run_short(
        session_factory,
        _url,
        op=OP_ARTIFACTS_GET_DOWNLOAD_URL,
        log_key=str(artifact_id),
        status_errors=(SdkArtifactError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_artifacts_create_document(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``artifacts.create_document`` through the shared service.

    Validates with the same ``DocumentArtifactSpec`` DTO as the HTTP
    handler, resolves the optional workspace id like the HTTP query
    param, and calls the exact ``shared.sdk_artifact_generation``
    service the HTTP handler calls — actor/org scope, workspace image
    resolution, renderer/provider errors, and content storage agree by
    construction. The child sends only data; actor, org, and execution
    identity come from the parent-derived principal. Image reads run in
    a short-lived session the shared service releases before CPU
    rendering; the final store commits on the raw parent session so the
    artifact survives for subsequent reads. ``SdkArtifactError`` rides
    its status; ``ValueError`` (renderer, access, validation) maps to
    422 like the global ``ValueError → 422`` handler. No commit on
    errors, no HTTP retry after a failed local write.
    """
    from shared.sdk_artifacts import ArtifactCaller, SdkArtifactError
    from src.models.contracts.artifacts import DocumentArtifactSpec

    request, invalid = _validate_request(
        DocumentArtifactSpec,
        {
            "filename": frame.get("filename"),
            "format": frame.get("format"),
            "title": frame.get("title"),
            "subtitle": frame.get("subtitle"),
            "sections": frame.get("sections"),
            "page_size": frame.get("page_size", "letter"),
        },
        frame_id,
        OP_ARTIFACTS_CREATE_DOCUMENT,
    )
    if invalid is not None:
        return [invalid]
    assert request is not None
    workspace_id, invalid = _workspace_id_from_frame(
        frame, frame_id, OP_ARTIFACTS_CREATE_DOCUMENT, required=False
    )
    if invalid is not None:
        return [invalid]

    async def _create(session: Any) -> dict[str, Any]:
        from shared.sdk_artifact_generation import sdk_render_document_artifact

        user = _artifact_user_for_principal(principal)
        try:
            ref = await sdk_render_document_artifact(
                ArtifactCaller(user=user, db=session),
                spec=request,
                workspace_id=workspace_id,
            )
        except ValueError as exc:
            raise SdkArtifactError(422, str(exc)) from exc
        # The raw parent session factory does not auto-commit: persist
        # the artifact before the child can read it in a later SDK call.
        await session.commit()
        return ref.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _create,
        op=OP_ARTIFACTS_CREATE_DOCUMENT,
        log_key=request.filename,
        status_errors=(SdkArtifactError,),
        timeout_seconds=ARTIFACT_RENDER_DISPATCH_TIMEOUT_SECONDS,
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_artifacts_create_spreadsheet(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``artifacts.create_spreadsheet`` through the shared service.

    Same ``SpreadsheetArtifactSpec`` DTO, parent-derived principal, and
    commit-on-success contract as ``create_document``. Pure CPU render
    in a worker thread holding no DB connection, then one store.
    """
    from shared.sdk_artifacts import ArtifactCaller, SdkArtifactError
    from src.models.contracts.artifacts import SpreadsheetArtifactSpec

    request, invalid = _validate_request(
        SpreadsheetArtifactSpec,
        {
            "filename": frame.get("filename"),
            "sheets": frame.get("sheets"),
        },
        frame_id,
        OP_ARTIFACTS_CREATE_SPREADSHEET,
    )
    if invalid is not None:
        return [invalid]
    assert request is not None
    workspace_id, invalid = _workspace_id_from_frame(
        frame, frame_id, OP_ARTIFACTS_CREATE_SPREADSHEET, required=False
    )
    if invalid is not None:
        return [invalid]

    async def _create(session: Any) -> dict[str, Any]:
        from shared.sdk_artifact_generation import sdk_render_spreadsheet_artifact

        user = _artifact_user_for_principal(principal)
        try:
            ref = await sdk_render_spreadsheet_artifact(
                ArtifactCaller(user=user, db=session),
                spec=request,
                workspace_id=workspace_id,
            )
        except ValueError as exc:
            raise SdkArtifactError(422, str(exc)) from exc
        await session.commit()
        return ref.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _create,
        op=OP_ARTIFACTS_CREATE_SPREADSHEET,
        log_key=request.filename,
        status_errors=(SdkArtifactError,),
        timeout_seconds=ARTIFACT_RENDER_DISPATCH_TIMEOUT_SECONDS,
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_artifacts_create_text(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``artifacts.create_text`` through the shared service.

    Same ``TextArtifactSpec`` DTO, parent-derived principal, and
    commit-on-success contract as ``create_document``. Pure CPU render
    in a worker thread holding no DB connection, then one store.
    """
    from shared.sdk_artifacts import ArtifactCaller, SdkArtifactError
    from src.models.contracts.artifacts import TextArtifactSpec

    request, invalid = _validate_request(
        TextArtifactSpec,
        {
            "filename": frame.get("filename"),
            "format": frame.get("format"),
            "content": frame.get("content"),
        },
        frame_id,
        OP_ARTIFACTS_CREATE_TEXT,
    )
    if invalid is not None:
        return [invalid]
    assert request is not None
    workspace_id, invalid = _workspace_id_from_frame(
        frame, frame_id, OP_ARTIFACTS_CREATE_TEXT, required=False
    )
    if invalid is not None:
        return [invalid]

    async def _create(session: Any) -> dict[str, Any]:
        from shared.sdk_artifact_generation import sdk_render_text_artifact

        user = _artifact_user_for_principal(principal)
        try:
            ref = await sdk_render_text_artifact(
                ArtifactCaller(user=user, db=session),
                spec=request,
                workspace_id=workspace_id,
            )
        except ValueError as exc:
            raise SdkArtifactError(422, str(exc)) from exc
        await session.commit()
        return ref.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _create,
        op=OP_ARTIFACTS_CREATE_TEXT,
        log_key=request.filename,
        status_errors=(SdkArtifactError,),
        timeout_seconds=ARTIFACT_RENDER_DISPATCH_TIMEOUT_SECONDS,
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_artifacts_create_image(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``artifacts.create_image`` through the shared service.

    Same ``ImageArtifactSpec`` DTO, parent-derived principal, and
    commit-on-success contract as the render operations. The shared
    service resolves the provider config in a short-lived session,
    releases it, runs the provider HTTP (up to 180s) with no session
    held, then stores and records usage on the caller's session in one
    transaction the dispatcher commits. Usage attribution uses the
    execution id from the parent-derived dispatch principal, never a
    forged child identity. The workspace id rides the frame as data. Provider
    errors (``MediaGenerationError``) map to 422 like the global
    ``ValueError → 422`` handler. Cancellation (child gone, parent
    shutdown, or the dispatch deadline) propagates without committing.
    """
    from shared.sdk_artifacts import ArtifactCaller, SdkArtifactError
    from src.models.contracts.artifacts import ImageArtifactSpec

    request, invalid = _validate_request(
        ImageArtifactSpec,
        {
            "filename": frame.get("filename"),
            "prompt": frame.get("prompt"),
        },
        frame_id,
        OP_ARTIFACTS_CREATE_IMAGE,
    )
    if invalid is not None:
        return [invalid]
    assert request is not None
    workspace_id, invalid = _workspace_id_from_frame(
        frame, frame_id, OP_ARTIFACTS_CREATE_IMAGE, required=False
    )
    if invalid is not None:
        return [invalid]
    execution_id = UUID(principal.execution_id) if principal.execution_id else None

    async def _create(session: Any) -> dict[str, Any]:
        from shared.sdk_artifact_generation import sdk_generate_image_artifact

        user = _artifact_user_for_principal(principal)
        try:
            ref = await sdk_generate_image_artifact(
                ArtifactCaller(user=user, db=session),
                spec=request,
                workspace_id=workspace_id,
                execution_id=execution_id,
            )
        except ValueError as exc:
            raise SdkArtifactError(422, str(exc)) from exc
        await session.commit()
        return ref.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _create,
        op=OP_ARTIFACTS_CREATE_IMAGE,
        log_key=request.filename,
        status_errors=(SdkArtifactError,),
        timeout_seconds=ARTIFACT_IMAGE_DISPATCH_TIMEOUT_SECONDS,
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


def _video_user_for_principal(principal: LocalDispatchPrincipal) -> Any:
    """Token-equivalent ``UserPrincipal`` for SDK video job operations.

    Reuses the table token-equivalent shape so enqueue attribution and
    job visibility decide identically on both transports: workflows run
    as the system-user superuser with no org (the ``mint_engine_token()``
    shape), services run as the system-user non-superuser confined to
    their service org (the ``mint_service_token()`` shape). Built only
    from the parent-derived ``LocalDispatchPrincipal`` — never from
    child frame fields, which carry data only.
    """
    return _table_user_for_principal(principal)


async def _dispatch_artifacts_create_video(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``artifacts.create_video`` enqueue through the shared service.

    Validates with the same ``VideoArtifactSpec`` DTO as the HTTP
    handler, resolves the optional workspace id like the HTTP query
    param, and calls the exact ``shared.sdk_video`` enqueue/finalize
    sequence the HTTP handler calls — requester/org/resource metadata,
    notification creation, and the commit/refresh/update ordering agree
    by construction. The execution id comes from the parent-derived
    dispatch principal (like the HTTP query param on engine calls),
    never from a child claim. ``SdkVideoJobError`` is unreachable here
    (enqueue validates the DTO only); the accepted payload rides the
    shared ``PlatformJobAccepted`` shape. No commit on errors, no HTTP
    retry after a failed local enqueue.
    """
    from src.models.contracts.artifacts import VideoArtifactSpec

    request, invalid = _validate_request(
        VideoArtifactSpec,
        {
            "filename": frame.get("filename"),
            "prompt": frame.get("prompt"),
        },
        frame_id,
        OP_ARTIFACTS_CREATE_VIDEO,
    )
    if invalid is not None:
        return [invalid]
    assert request is not None
    workspace_id, invalid = _workspace_id_from_frame(
        frame, frame_id, OP_ARTIFACTS_CREATE_VIDEO, required=False
    )
    if invalid is not None:
        return [invalid]
    execution_id = UUID(principal.execution_id) if principal.execution_id else None
    user = _video_user_for_principal(principal)

    async def _enqueue(session: Any) -> dict[str, Any]:
        from shared.sdk_video import (
            enqueue_sdk_video_job,
            finalize_sdk_video_job,
            sdk_video_job_accepted,
        )

        job, reused = await enqueue_sdk_video_job(
            session,
            user,
            spec=request,
            workspace_id=workspace_id,
            execution_id=execution_id,
        )
        await finalize_sdk_video_job(session, job)
        return sdk_video_job_accepted(job, reused).model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _enqueue,
        op=OP_ARTIFACTS_CREATE_VIDEO,
        log_key=request.filename,
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_artifacts_video_status(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``artifacts.video_status`` polling through the shared service.

    The job id validates as a UUID (422 on malformed, like the HTTP
    path-param parsing). Visibility and serialization are the shared
    ``shared.sdk_video`` service's — the same requester-visibility rule
    and ``PlatformJobPublic`` shape as the HTTP status endpoint — and
    the call stays fixed to SDK video jobs (any other job type is a 404
    error frame, never a generic job API). Each poll runs on its own
    short parent session, so child polling holds no DB connection and
    pins no API container.
    """
    from shared.sdk_video import SdkVideoJobError, get_sdk_video_job_status

    raw_id = frame.get("job_id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_ARTIFACTS_VIDEO_STATUS} request: 'job_id' is required",
            )
        ]
    try:
        job_uuid = UUID(raw_id)
    except ValueError:
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_ARTIFACTS_VIDEO_STATUS} request: 'job_id' must be a UUID",
            )
        ]
    user = _video_user_for_principal(principal)

    async def _status(session: Any) -> dict[str, Any]:
        public = await get_sdk_video_job_status(session, user, job_uuid)
        return public.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _status,
        op=OP_ARTIFACTS_VIDEO_STATUS,
        log_key=raw_id,
        status_errors=(SdkVideoJobError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


def _files_target_solution_id(
    frame_solution: Any, principal: LocalDispatchPrincipal
) -> str | None:
    """Per-call target install ref for one file frame.

    The child-supplied ``solution`` is only ever a *target* (UUID or
    slug/name, resolved inside the target org downstream). Unset or blank
    inherits the parent-owned own install. The caller's install identity
    itself always comes from the principal — never from the frame.
    Callers must never send ``caller_solution``/``app_id``/``user`` claims;
    they are never read here.
    """
    if isinstance(frame_solution, str) and frame_solution.strip():
        return frame_solution
    if principal.solution_id is not None:
        return str(principal.solution_id)
    return None


def _file_caller_for_principal(
    session: Any,
    principal: LocalDispatchPrincipal,
    solution_target: str | None,
) -> Any:
    """Parent-built ``FileCaller`` for one engine-local file operation.

    The ``user`` is the token-equivalent principal (engine sentinel
    superuser for workflows, org-scoped service identity for ``@service``
    children — never the initiating user's admin flag); ``org_id`` is the
    parent-owned caller org; ``solution_id`` is the per-call target
    install ref; ``caller_solution_id``/``app_id`` stay parent-owned
    (None for engine children — never child frame claims, which attest
    their install through the signed engine claims on ``user`` instead).
    """
    from shared.file_access import FileCaller

    return FileCaller(
        user=_table_user_for_principal(principal),
        db=session,
        org_id=principal.caller_org_id,
        solution_id=solution_target,
        caller_solution_id=None,
        app_id=None,
    )


def _files_422(
    frame_id: str | None, op: str, msg: str
) -> dict[str, Any]:
    """One 422 error frame for a malformed file request field."""
    return _error(frame_id, 422, f"invalid {op} request: {msg}")


def _files_str(
    frame: dict[str, Any],
    field: str,
    frame_id: str | None,
    op: str,
    *,
    default: str | None = None,
    required: bool = False,
) -> tuple[str | None, dict[str, Any] | None]:
    """One string frame field with the HTTP DTO's presence/type behavior.

    Required fields must be present strings (empty allowed — the DTO sets
    no min_length, so emptiness fails downstream exactly like HTTP);
    optional fields fall back to ``default`` when absent (None) and must
    be strings otherwise.
    """
    value = frame.get(field, default)
    if value is None:
        if required or default is not None:
            return None, _files_422(frame_id, op, f"{field!r} is required")
        return None, None
    if not isinstance(value, str):
        return None, _files_422(frame_id, op, f"{field!r} must be a string")
    return value, None


def _files_opt_str(
    frame: dict[str, Any],
    field: str,
    frame_id: str | None,
    op: str,
) -> tuple[str | None, dict[str, Any] | None]:
    """One ``str | None`` frame field (None when absent), else a 422."""
    value = frame.get(field)
    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, _files_422(frame_id, op, f"{field!r} must be a string")
    return value, None


def _files_bool(
    frame: dict[str, Any],
    field: str,
    frame_id: str | None,
    op: str,
    *,
    default: bool = False,
) -> tuple[bool, dict[str, Any] | None]:
    """One bool frame field with the HTTP DTO's default, else a 422."""
    value = frame.get(field, default)
    if not isinstance(value, bool):
        return default, _files_422(frame_id, op, f"{field!r} must be a boolean")
    return value, None


def _files_mode(
    frame: dict[str, Any],
    frame_id: str | None,
    op: str,
) -> tuple[str, dict[str, Any] | None]:
    """The ``mode`` field: ``"local"`` or ``"cloud"`` (default ``"cloud"``).

    Mirrors the ``Mode = Literal["local", "cloud"]`` DTO — anything else
    is a 422, exactly like the HTTP handler.
    """
    value = frame.get("mode", "cloud")
    if value not in ("local", "cloud"):
        return "cloud", _files_422(
            frame_id, op, "'mode' must be 'local' or 'cloud'"
        )
    return value, None


def _files_method(
    frame: dict[str, Any],
    frame_id: str | None,
    op: str,
) -> tuple[str, dict[str, Any] | None]:
    """The signed-URL ``method`` field: ``"PUT"`` or ``"GET"`` (default ``"PUT"``)."""
    value = frame.get("method", "PUT")
    if value not in ("PUT", "GET"):
        return "PUT", _files_422(
            frame_id, op, "'method' must be 'PUT' or 'GET'"
        )
    return value, None


def _files_expires(
    frame: dict[str, Any],
    frame_id: str | None,
    op: str,
) -> tuple[int, dict[str, Any] | None]:
    """The ``expires_in`` field: int within 1..604800 (default 600).

    Mirrors the ``SignedUrlRequest`` ``ge=1, le=604800`` bounds — out of
    range is a 422, exactly like the HTTP handler.
    """
    value = frame.get("expires_in", 600)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= 604800
    ):
        return 600, _files_422(
            frame_id, op, "'expires_in' must be an int within 1..604800"
        )
    return value, None


def _files_max_results(
    frame: dict[str, Any],
    frame_id: str | None,
    op: str,
) -> tuple[int, dict[str, Any] | None]:
    """The search ``max_results`` field: int within 1..10000 (default 1000)."""
    value = frame.get("max_results", 1000)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= 10000
    ):
        return 1000, _files_422(
            frame_id, op, "'max_results' must be an int within 1..10000"
        )
    return value, None


async def _dispatch_files_read(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``files.read``/``read_bytes`` through the shared file service.

    Validates with the same ``FileReadRequest`` DTO as the HTTP handler,
    so path/location/mode/binary coercion and 422 behavior match. The
    parent calls the exact ``shared.sdk_files.sdk_read_file`` the HTTP
    route calls, with a parent-derived ``FileCaller`` — tier order,
    Solution gates, policy probes, 404/403/400 semantics, and text/binary
    encoding are identical by construction. ``mode="local"`` runs the
    HTTP route's own filesystem backend in the parent (never the child's
    CWD). Binary content returns base64 in the result dict (chunked when
    large), exactly like the HTTP JSON body.
    """
    import base64

    from shared.file_access import FileServiceError
    from types import SimpleNamespace

    # Same fields/defaults as the HTTP ``FileReadRequest`` DTO (see
    # ``src/routers/files.py``) — presence/type behavior matches, so
    # coercion and 422s agree on both transports.
    path, invalid = _files_str(
        frame, "path", frame_id, OP_FILES_READ, required=True
    )
    if invalid is not None:
        return [invalid]
    location, invalid = _files_str(
        frame, "location", frame_id, OP_FILES_READ, default="workspace"
    )
    if invalid is not None:
        return [invalid]
    scope, invalid = _files_opt_str(frame, "scope", frame_id, OP_FILES_READ)
    if invalid is not None:
        return [invalid]
    mode, invalid = _files_mode(frame, frame_id, OP_FILES_READ)
    if invalid is not None:
        return [invalid]
    binary, invalid = _files_bool(frame, "binary", frame_id, OP_FILES_READ)
    if invalid is not None:
        return [invalid]
    request = SimpleNamespace(
        path=path, location=location, scope=scope, mode=mode,
        binary=binary,
    )
    solution_target = _files_target_solution_id(
        frame.get("solution"), principal
    )

    async def _read(session: Any) -> dict[str, Any]:
        from shared.sdk_files import sdk_read_file

        result = await sdk_read_file(
            _file_caller_for_principal(session, principal, solution_target),
            path=request.path,
            location=request.location,
            scope=request.scope,
            mode=request.mode,
            binary=request.binary,
        )
        if request.binary:
            content = base64.b64encode(result.content).decode()
        else:
            content = result.content.decode("utf-8")
        return {"content": content, "binary": request.binary}

    result, error = await _run_short(
        session_factory,
        _read,
        op=OP_FILES_READ,
        log_key=request.path,
        status_errors=(FileServiceError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_files_write(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``files.write``/``write_bytes`` through the shared file service.

    Validates with the same ``FileWriteRequest`` DTO as the HTTP handler
    (``expected_version``/``create_only`` conflict semantics included) and
    calls the exact ``shared.sdk_files.sdk_write_file`` the HTTP route
    calls: effective scope, declared-Solution gates, policy check with
    denial audit, advisory lock, conflict checks, backend write, and —
    cloud mode only — metadata commit before publish. Returns nothing
    (HTTP 204). A local attempt never falls back to HTTP.
    """
    from shared.file_access import FileServiceError
    from types import SimpleNamespace

    # Same fields/defaults as the HTTP ``FileWriteRequest`` DTO.
    path, invalid = _files_str(
        frame, "path", frame_id, OP_FILES_WRITE, required=True
    )
    if invalid is not None:
        return [invalid]
    content, invalid = _files_str(
        frame, "content", frame_id, OP_FILES_WRITE, required=True
    )
    if invalid is not None:
        return [invalid]
    location, invalid = _files_str(
        frame, "location", frame_id, OP_FILES_WRITE, default="workspace"
    )
    if invalid is not None:
        return [invalid]
    scope, invalid = _files_opt_str(frame, "scope", frame_id, OP_FILES_WRITE)
    if invalid is not None:
        return [invalid]
    mode, invalid = _files_mode(frame, frame_id, OP_FILES_WRITE)
    if invalid is not None:
        return [invalid]
    binary, invalid = _files_bool(frame, "binary", frame_id, OP_FILES_WRITE)
    if invalid is not None:
        return [invalid]
    expected_version, invalid = _files_opt_str(
        frame, "expected_version", frame_id, OP_FILES_WRITE
    )
    if invalid is not None:
        return [invalid]
    create_only, invalid = _files_bool(
        frame, "create_only", frame_id, OP_FILES_WRITE
    )
    if invalid is not None:
        return [invalid]
    request = SimpleNamespace(
        path=path, content=content, location=location, scope=scope,
        mode=mode, binary=binary, expected_version=expected_version,
        create_only=create_only,
    )
    solution_target = _files_target_solution_id(
        frame.get("solution"), principal
    )

    async def _write(session: Any) -> None:
        from shared.sdk_files import sdk_write_file

        await sdk_write_file(
            _file_caller_for_principal(session, principal, solution_target),
            path=request.path,
            content=request.content,
            binary=request.binary,
            location=request.location,
            scope=request.scope,
            mode=request.mode,
            expected_version=request.expected_version,
            create_only=request.create_only,
        )

    _, error = await _run_short(
        session_factory,
        _write,
        op=OP_FILES_WRITE,
        log_key=request.path,
        status_errors=(FileServiceError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _single_ok(frame_id, None)


async def _dispatch_files_list(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``files.list`` through the shared file service.

    Validates with the same ``FileListRequest`` DTO as the HTTP handler
    and calls the exact ``shared.sdk_files.sdk_list_files`` the HTTP
    route calls. The workspace ``include_metadata=True`` branch stays
    router-only (the Python SDK never requests it) — a child asking for
    it gets the same plain listing the shared service returns, never
    metadata. The names ride a ``{"files"}`` envelope (the transport
    result contract does not carry bare lists).
    """
    from shared.file_access import FileServiceError
    from types import SimpleNamespace

    # Same fields/defaults as the HTTP ``FileListRequest`` DTO. The
    # workspace ``include_metadata=True`` branch stays router-only (the
    # Python SDK never requests it) — the flag is accepted and ignored,
    # exactly like the shared service path behind the HTTP route.
    directory, invalid = _files_str(
        frame, "directory", frame_id, OP_FILES_LIST, default=""
    )
    if invalid is not None:
        return [invalid]
    location, invalid = _files_str(
        frame, "location", frame_id, OP_FILES_LIST, default="workspace"
    )
    if invalid is not None:
        return [invalid]
    scope, invalid = _files_opt_str(frame, "scope", frame_id, OP_FILES_LIST)
    if invalid is not None:
        return [invalid]
    mode, invalid = _files_mode(frame, frame_id, OP_FILES_LIST)
    if invalid is not None:
        return [invalid]
    _, invalid = _files_bool(
        frame, "include_metadata", frame_id, OP_FILES_LIST
    )
    if invalid is not None:
        return [invalid]
    request = SimpleNamespace(
        directory=directory, location=location, scope=scope, mode=mode,
    )
    solution_target = _files_target_solution_id(
        frame.get("solution"), principal
    )

    async def _list(session: Any) -> dict[str, Any]:
        from shared.sdk_files import sdk_list_files

        names = await sdk_list_files(
            _file_caller_for_principal(session, principal, solution_target),
            directory=request.directory,
            location=request.location,
            scope=request.scope,
            mode=request.mode,
        )
        return {"files": names}

    result, error = await _run_short(
        session_factory,
        _list,
        op=OP_FILES_LIST,
        log_key=request.directory,
        status_errors=(FileServiceError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_files_delete(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``files.delete`` through the shared file service.

    Validates with the same ``FileDeleteRequest`` DTO as the HTTP handler
    and calls the exact ``shared.sdk_files.sdk_delete_file`` the HTTP
    route calls: effective scope, declared-Solution gates, policy check
    with denial audit, advisory lock, ``expected_version`` conflict
    checks, backend delete, and — cloud mode only — metadata delete with
    commit before publish. A backend ``FileNotFoundError`` is 404.
    Returns nothing (HTTP 204).
    """
    from shared.file_access import FileServiceError
    from types import SimpleNamespace

    # Same fields/defaults as the HTTP ``FileDeleteRequest`` DTO.
    path, invalid = _files_str(
        frame, "path", frame_id, OP_FILES_DELETE, required=True
    )
    if invalid is not None:
        return [invalid]
    location, invalid = _files_str(
        frame, "location", frame_id, OP_FILES_DELETE, default="workspace"
    )
    if invalid is not None:
        return [invalid]
    scope, invalid = _files_opt_str(
        frame, "scope", frame_id, OP_FILES_DELETE
    )
    if invalid is not None:
        return [invalid]
    mode, invalid = _files_mode(frame, frame_id, OP_FILES_DELETE)
    if invalid is not None:
        return [invalid]
    expected_version, invalid = _files_opt_str(
        frame, "expected_version", frame_id, OP_FILES_DELETE
    )
    if invalid is not None:
        return [invalid]
    request = SimpleNamespace(
        path=path, location=location, scope=scope, mode=mode,
        expected_version=expected_version,
    )
    solution_target = _files_target_solution_id(
        frame.get("solution"), principal
    )

    async def _delete(session: Any) -> None:
        from shared.sdk_files import sdk_delete_file

        await sdk_delete_file(
            _file_caller_for_principal(session, principal, solution_target),
            path=request.path,
            location=request.location,
            scope=request.scope,
            mode=request.mode,
            expected_version=request.expected_version,
        )

    _, error = await _run_short(
        session_factory,
        _delete,
        op=OP_FILES_DELETE,
        log_key=request.path,
        status_errors=(FileServiceError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _single_ok(frame_id, None)


async def _dispatch_files_exists(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``files.exists`` through the shared file service.

    Validates with the same ``FileExistsRequest`` DTO as the HTTP handler
    and calls the exact ``shared.sdk_files.sdk_file_exists`` the HTTP
    route calls. Existence probes never 403/404 — just False. Returns a
    bare bool.
    """
    from shared.file_access import FileServiceError
    from types import SimpleNamespace

    # Same fields/defaults as the HTTP ``FileExistsRequest`` DTO.
    path, invalid = _files_str(
        frame, "path", frame_id, OP_FILES_EXISTS, required=True
    )
    if invalid is not None:
        return [invalid]
    location, invalid = _files_str(
        frame, "location", frame_id, OP_FILES_EXISTS, default="workspace"
    )
    if invalid is not None:
        return [invalid]
    scope, invalid = _files_opt_str(
        frame, "scope", frame_id, OP_FILES_EXISTS
    )
    if invalid is not None:
        return [invalid]
    mode, invalid = _files_mode(frame, frame_id, OP_FILES_EXISTS)
    if invalid is not None:
        return [invalid]
    request = SimpleNamespace(
        path=path, location=location, scope=scope, mode=mode,
    )
    solution_target = _files_target_solution_id(
        frame.get("solution"), principal
    )

    async def _exists(session: Any) -> bool:
        from shared.sdk_files import sdk_file_exists

        return await sdk_file_exists(
            _file_caller_for_principal(session, principal, solution_target),
            path=request.path,
            location=request.location,
            scope=request.scope,
            mode=request.mode,
        )

    result, error = await _run_short(
        session_factory,
        _exists,
        op=OP_FILES_EXISTS,
        log_key=request.path,
        status_errors=(FileServiceError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    assert isinstance(result, bool)
    return _single_ok(frame_id, result)


async def _dispatch_files_stat(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``files.stat`` through the shared file service.

    The HTTP route reuses ``FileReadRequest`` for stat — the local frame
    validates with the same DTO (``binary`` is accepted and ignored,
    exactly like HTTP). Calls the exact
    ``shared.sdk_files.sdk_file_stat`` the HTTP route calls and returns
    the ``FileStatResponse`` dict (``exists=False`` when absent — never
    a 404).
    """
    from shared.file_access import FileServiceError
    from types import SimpleNamespace

    # The HTTP stat route reuses ``FileReadRequest`` — the local frame
    # validates with the same fields (``binary`` accepted and ignored).
    path, invalid = _files_str(
        frame, "path", frame_id, OP_FILES_STAT, required=True
    )
    if invalid is not None:
        return [invalid]
    location, invalid = _files_str(
        frame, "location", frame_id, OP_FILES_STAT, default="workspace"
    )
    if invalid is not None:
        return [invalid]
    scope, invalid = _files_opt_str(frame, "scope", frame_id, OP_FILES_STAT)
    if invalid is not None:
        return [invalid]
    mode, invalid = _files_mode(frame, frame_id, OP_FILES_STAT)
    if invalid is not None:
        return [invalid]
    _, invalid = _files_bool(frame, "binary", frame_id, OP_FILES_STAT)
    if invalid is not None:
        return [invalid]
    request = SimpleNamespace(
        path=path, location=location, scope=scope, mode=mode,
    )
    solution_target = _files_target_solution_id(
        frame.get("solution"), principal
    )

    async def _stat(session: Any) -> dict[str, Any]:
        from shared.sdk_files import sdk_file_stat

        stat = await sdk_file_stat(
            _file_caller_for_principal(session, principal, solution_target),
            path=request.path,
            location=request.location,
            scope=request.scope,
            mode=request.mode,
        )
        return stat.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _stat,
        op=OP_FILES_STAT,
        log_key=request.path,
        status_errors=(FileServiceError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_files_signed_url(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``files.get_signed_url`` through the shared file service.

    Validates with the same ``SignedUrlRequest`` DTO as the HTTP handler
    and calls the exact ``shared.sdk_files.sdk_signed_url`` the HTTP
    route calls (signed GET tier cascade and signed PUT scope/policy
    resolution included). A raw ``ValueError`` from PUT scope resolution
    maps to 422 — the router historically let it reach the 422
    middleware. Returns the ``{"url", "path", "expires_in"}`` dict.
    """
    from shared.file_access import FileServiceError
    from types import SimpleNamespace

    # Same fields/defaults/bounds as the HTTP ``SignedUrlRequest`` DTO.
    path, invalid = _files_str(
        frame, "path", frame_id, OP_FILES_SIGNED_URL, required=True
    )
    if invalid is not None:
        return [invalid]
    method, invalid = _files_method(frame, frame_id, OP_FILES_SIGNED_URL)
    if invalid is not None:
        return [invalid]
    content_type, invalid = _files_str(
        frame,
        "content_type",
        frame_id,
        OP_FILES_SIGNED_URL,
        default="application/octet-stream",
    )
    if invalid is not None:
        return [invalid]
    location, invalid = _files_str(
        frame, "location", frame_id, OP_FILES_SIGNED_URL,
        default="uploads",
    )
    if invalid is not None:
        return [invalid]
    scope, invalid = _files_opt_str(
        frame, "scope", frame_id, OP_FILES_SIGNED_URL
    )
    if invalid is not None:
        return [invalid]
    expires_in, invalid = _files_expires(frame, frame_id, OP_FILES_SIGNED_URL)
    if invalid is not None:
        return [invalid]
    request = SimpleNamespace(
        path=path, method=method, content_type=content_type,
        location=location, scope=scope, expires_in=expires_in,
    )
    solution_target = _files_target_solution_id(
        frame.get("solution"), principal
    )

    async def _sign(session: Any) -> dict[str, Any]:
        from shared.sdk_files import sdk_signed_url

        try:
            signed = await sdk_signed_url(
                _file_caller_for_principal(
                    session, principal, solution_target
                ),
                path=request.path,
                location=request.location,
                scope=request.scope,
                method=request.method,
                content_type=request.content_type,
                expires_in=request.expires_in,
            )
        except ValueError as exc:
            # PUT scope-resolution ValueErrors propagate raw out of the
            # shared service (the HTTP router lets them reach the 422
            # middleware); map them to 422 here.
            raise FileServiceError(422, str(exc)) from exc
        return {
            "url": signed.url,
            "path": signed.path,
            "expires_in": signed.expires_in,
        }

    result, error = await _run_short(
        session_factory,
        _sign,
        op=OP_FILES_SIGNED_URL,
        log_key=request.path,
        status_errors=(FileServiceError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_files_search(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``files.search`` through the shared search service.

    Validates with the same ``SearchRequest`` DTO as the HTTP handler and
    calls the exact ``src.services.editor.search.search_files_db`` the
    HTTP route calls (``root_path=""``), so validation, glob filtering,
    truncation, and timing are identical by construction. Like HTTP there
    is no scope parameter — results are scoped by the caller's identity —
    and the endpoint is superuser-only: service children (system-user
    non-superuser, like their HTTP token) get a 403, exactly like their
    HTTP POST would. Large result sets arrive chunked.
    """
    from src.models.contracts.editor import SearchRequest

    # Same fields/defaults/bounds as the HTTP ``SearchRequest`` DTO. The
    # full DTO still validates here (cheap, no router import) so
    # coercion and 422 behavior match the HTTP handler exactly.
    raw: dict[str, Any] = {
        "query": frame.get("query"),
        "case_sensitive": frame.get("case_sensitive", False),
        "is_regex": frame.get("is_regex", False),
        "include_pattern": frame.get("include_pattern", "**/*"),
        "max_results": frame.get("max_results", 1000),
    }
    request, invalid = _validate_request(
        SearchRequest, raw, frame_id, OP_FILES_SEARCH
    )
    if invalid is not None:
        return [invalid]
    assert request is not None
    if principal.is_service:
        return [_error(frame_id, 403, "Only platform admins can search files")]

    from shared.file_access import FileServiceError

    async def _search(session: Any) -> dict[str, Any]:
        from src.services.editor.search import search_files_db

        try:
            response = await search_files_db(session, request, root_path="")
        except ValueError as exc:
            raise FileServiceError(400, str(exc)) from exc
        return response.model_dump(mode="json")

    result, error = await _run_short(
        session_factory,
        _search,
        op=OP_FILES_SEARCH,
        log_key=request.query,
        status_errors=(FileServiceError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


def _knowledge_actor_id() -> Any:
    """Parent-derived ``created_by`` for knowledge writes (never a child claim).

    Both HTTP workflow and service calls authenticate as the engine
    sentinel ``sub`` (``mint_engine_token``/``mint_service_token``), so
    the local dispatcher attributes to the same ``SYSTEM_USER_UUID`` —
    matching the HTTP ``current_user.user_id`` on both paths.
    """
    from src.core.constants import SYSTEM_USER_UUID

    return SYSTEM_USER_UUID


async def _knowledge_embedder(
    session_factory: SessionFactory,
    frame_id: str | None,
    op: str,
    fail_prefix: str,
) -> tuple[Any | None, dict[str, Any] | None]:
    """Phase 1: load the embedding client on one short session.

    Holds a pooled parent connection only for the config read; the
    caller closes it before the network embedding work. ``ValueError``
    (unconfigured embeddings) keeps its 503 detail and any other
    load failure keeps the historical generic 500 detail, so the
    error frame matches the single-session HTTP path exactly.
    """
    from shared.sdk_knowledge import SDKKnowledgeError, load_knowledge_embedder

    async def _load(session: Any) -> Any:
        try:
            return await load_knowledge_embedder(session)
        except ValueError as e:
            raise SDKKnowledgeError(503, str(e)) from None
        except SDKKnowledgeError:
            raise
        except Exception as e:
            raise SDKKnowledgeError(500, f"{fail_prefix}: {str(e)}") from None

    embedder, error = await _run_short(
        session_factory, _load, op=op, log_key="",
        status_errors=(SDKKnowledgeError,),
    )
    if error is not None:
        error["id"] = frame_id
        return None, error
    return embedder, None


async def _dispatch_knowledge_store(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``knowledge.store`` through the shared knowledge service.

    Validates with the same ``CLIKnowledgeStoreRequest`` DTO as the HTTP
    handler, resolves the untrusted scope string against the
    parent-derived principal (workflows keep the engine-token snapshot,
    services re-check provider membership live), and calls the same
    shared service. ``created_by`` is the parent-derived engine
    sentinel UUID — never a child claim.

    Split-phase transaction (explicit): the embedding-config read runs
    on one short session, chunking/embedding runs off-connection with
    no pooled session held, and the vector/DB write runs on a second
    short session with one commit. A pre-commit embedding failure
    leaves no rows (the single-session path rolled back the flushed
    rows instead) — same observable outcome, no needlessly held
    connection. ``ValueError`` in either phase keeps its 503 detail;
    anything else is the historical generic 500.
    """
    from shared.sdk_knowledge import (
        SDKKnowledgeError,
        embed_content_chunks,
        store_knowledge_preembedded,
    )
    from src.models.contracts.cli import CLIKnowledgeStoreRequest

    request, invalid = _validate_request(
        CLIKnowledgeStoreRequest,
        {
            "content": frame.get("content"),
            "namespace": frame.get("namespace", "default"),
            "key": frame.get("key"),
            "metadata": frame.get("metadata"),
            "scope": frame.get("scope"),
        },
        frame_id,
        OP_KNOWLEDGE_STORE,
    )
    if invalid is not None:
        return [invalid]
    assert request is not None
    resolved_org_id, scope_error = await _resolve_frame_scope(
        principal, frame_id, {"scope": request.scope}, session_factory
    )
    if scope_error is not None:
        return [scope_error]

    embedder, error = await _knowledge_embedder(
        session_factory, frame_id, OP_KNOWLEDGE_STORE, "Knowledge store failed"
    )
    if error is not None:
        return [error]
    assert embedder is not None
    try:
        chunks, embeddings = await embed_content_chunks(embedder, request.content)
    except ValueError as e:
        return [_error(frame_id, 503, str(e))]
    except Exception as e:
        return [_error(frame_id, 500, f"Knowledge store failed: {str(e)}")]

    async def _store(session: Any) -> dict[str, Any]:
        return await store_knowledge_preembedded(
            session,
            chunks=chunks,
            embeddings=embeddings,
            namespace=request.namespace,
            key=request.key,
            metadata=request.metadata,
            org_id=resolved_org_id,
            created_by=_knowledge_actor_id(),
        )

    result, error = await _run_short(
        session_factory,
        _store,
        op=OP_KNOWLEDGE_STORE,
        log_key=request.namespace,
        status_errors=(SDKKnowledgeError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _single_ok(frame_id, result)


async def _dispatch_knowledge_store_many(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``knowledge.store_many`` through the shared knowledge service.

    Same DTO, scope, actor, and split-phase transaction as
    :func:`_dispatch_knowledge_store`: one embedder loads on a short
    session, every document embeds sequentially off-connection (a doc
    missing ``content`` fails with the historical generic 500 before
    any write transaction opens — the single-session path flushed the
    prior docs then rolled back instead), and one repository persists
    all documents with one commit on a second short session.
    """
    from shared.sdk_knowledge import (
        SDKKnowledgeError,
        embed_content_chunks,
        store_many_preembedded,
    )
    from src.models.contracts.cli import CLIKnowledgeStoreManyRequest

    request, invalid = _validate_request(
        CLIKnowledgeStoreManyRequest,
        {
            "documents": frame.get("documents"),
            "namespace": frame.get("namespace", "default"),
            "scope": frame.get("scope"),
        },
        frame_id,
        OP_KNOWLEDGE_STORE_MANY,
    )
    if invalid is not None:
        return [invalid]
    assert request is not None
    resolved_org_id, scope_error = await _resolve_frame_scope(
        principal, frame_id, {"scope": request.scope}, session_factory
    )
    if scope_error is not None:
        return [scope_error]

    embedder, error = await _knowledge_embedder(
        session_factory, frame_id, OP_KNOWLEDGE_STORE_MANY,
        "Knowledge store failed",
    )
    if error is not None:
        return [error]
    assert embedder is not None
    try:
        items: list[dict[str, Any]] = []
        for doc in request.documents:
            chunks, embeddings = await embed_content_chunks(
                embedder, doc["content"]
            )
            items.append(
                {
                    "chunks": chunks,
                    "embeddings": embeddings,
                    "key": doc.get("key"),
                    "metadata": doc.get("metadata"),
                }
            )
    except ValueError as e:
        return [_error(frame_id, 503, str(e))]
    except Exception as e:
        return [_error(frame_id, 500, f"Knowledge store failed: {str(e)}")]

    async def _store(session: Any) -> dict[str, Any]:
        return await store_many_preembedded(
            session,
            items=items,
            namespace=request.namespace,
            org_id=resolved_org_id,
            created_by=_knowledge_actor_id(),
        )

    result, error = await _run_short(
        session_factory,
        _store,
        op=OP_KNOWLEDGE_STORE_MANY,
        log_key=request.namespace,
        status_errors=(SDKKnowledgeError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _single_ok(frame_id, result)


async def _dispatch_knowledge_search(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``knowledge.search`` through the shared knowledge service.

    Same ``CLIKnowledgeSearchRequest`` DTO, scope resolution, single
    query-embedding, fused lexical/vector ranking, and result shape as
    the HTTP handler. The query embeds off-connection (no pooled
    session held across the provider call); the vector query runs on a
    short session with no commit. Large result sets ride bounded
    chunked frames via ``_ok_frames`` inside a ``{"items"}`` envelope
    (the transport result contract does not carry bare lists).
    """
    from shared.sdk_knowledge import (
        SDKKnowledgeError,
        embed_query_text,
        search_knowledge_with_embedding,
    )
    from src.models.contracts.cli import CLIKnowledgeSearchRequest

    request, invalid = _validate_request(
        CLIKnowledgeSearchRequest,
        {
            "query": frame.get("query"),
            "namespace": frame.get("namespace", ["default"]),
            "limit": frame.get("limit", 5),
            "min_score": frame.get("min_score"),
            "metadata_filter": frame.get("metadata_filter"),
            "scope": frame.get("scope"),
            "fallback": frame.get("fallback", True),
        },
        frame_id,
        OP_KNOWLEDGE_SEARCH,
    )
    if invalid is not None:
        return [invalid]
    assert request is not None
    resolved_org_id, scope_error = await _resolve_frame_scope(
        principal, frame_id, {"scope": request.scope}, session_factory
    )
    if scope_error is not None:
        return [scope_error]

    embedder, error = await _knowledge_embedder(
        session_factory, frame_id, OP_KNOWLEDGE_SEARCH,
        "Knowledge search failed",
    )
    if error is not None:
        return [error]
    assert embedder is not None
    try:
        query_embedding = await embed_query_text(embedder, request.query)
    except ValueError as e:
        return [_error(frame_id, 503, str(e))]
    except Exception as e:
        return [_error(frame_id, 500, f"Knowledge search failed: {str(e)}")]

    async def _search(session: Any) -> dict[str, Any]:
        items = await search_knowledge_with_embedding(
            session,
            query_embedding=query_embedding,
            query_text=request.query,
            namespace=request.namespace,
            limit=request.limit,
            min_score=request.min_score,
            metadata_filter=request.metadata_filter,
            fallback=request.fallback,
            org_id=resolved_org_id,
        )
        return {"items": items}

    result, error = await _run_short(
        session_factory,
        _search,
        op=OP_KNOWLEDGE_SEARCH,
        log_key=request.query[:50],
        status_errors=(SDKKnowledgeError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_knowledge_delete(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``knowledge.delete`` through the shared knowledge service.

    Same ``CLIKnowledgeDeleteRequest`` DTO, exact-scope delete, commit
    ordering, and ``{"deleted": bool}`` shape as the HTTP handler.
    """
    from shared.sdk_knowledge import SDKKnowledgeError, delete_knowledge_document
    from src.models.contracts.cli import CLIKnowledgeDeleteRequest

    request, invalid = _validate_request(
        CLIKnowledgeDeleteRequest,
        {
            "key": frame.get("key"),
            "namespace": frame.get("namespace", "default"),
            "scope": frame.get("scope"),
        },
        frame_id,
        OP_KNOWLEDGE_DELETE,
    )
    if invalid is not None:
        return [invalid]
    assert request is not None
    resolved_org_id, scope_error = await _resolve_frame_scope(
        principal, frame_id, {"scope": request.scope}, session_factory
    )
    if scope_error is not None:
        return [scope_error]

    async def _delete(session: Any) -> dict[str, Any]:
        return await delete_knowledge_document(
            session,
            key=request.key,
            namespace=request.namespace,
            org_id=resolved_org_id,
        )

    result, error = await _run_short(
        session_factory,
        _delete,
        op=OP_KNOWLEDGE_DELETE,
        log_key=request.key,
        status_errors=(SDKKnowledgeError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _single_ok(frame_id, result)


async def _dispatch_knowledge_delete_namespace(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``knowledge.delete_namespace`` through the shared service.

    The HTTP route takes ``namespace`` from the path and ``scope`` from
    the query string (no body DTO), so the frame validates the same two
    fields directly: a missing/blank namespace and a non-string scope
    are 422, matching the tables string-field helpers. Same
    exact-scope delete, commit ordering, and ``{"deleted_count": int}``
    shape as the HTTP handler.
    """
    from shared.sdk_knowledge import SDKKnowledgeError, delete_knowledge_namespace

    namespace = frame.get("namespace")
    if not isinstance(namespace, str) or not namespace:
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_KNOWLEDGE_DELETE_NAMESPACE} request: "
                "'namespace' is required",
            )
        ]
    scope = frame.get("scope")
    if scope is not None and not isinstance(scope, str):
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_KNOWLEDGE_DELETE_NAMESPACE} request: "
                "'scope' must be a string",
            )
        ]
    resolved_org_id, scope_error = await _resolve_frame_scope(
        principal, frame_id, {"scope": scope}, session_factory
    )
    if scope_error is not None:
        return [scope_error]

    async def _delete(session: Any) -> dict[str, Any]:
        return await delete_knowledge_namespace(
            session,
            namespace=namespace,
            org_id=resolved_org_id,
        )

    result, error = await _run_short(
        session_factory,
        _delete,
        op=OP_KNOWLEDGE_DELETE_NAMESPACE,
        log_key=namespace,
        status_errors=(SDKKnowledgeError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _single_ok(frame_id, result)


async def _dispatch_knowledge_list_namespaces(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``knowledge.list_namespaces`` through the shared service.

    The HTTP route takes ``scope``/``include_global`` from the query
    string (no body DTO): a non-string scope and a non-bool
    ``include_global`` are 422 (the HTTP layer coerces query strings
    through its own parsing; the frame carries typed values, so only
    the type check applies). Same include-global behavior and
    namespace/count shape as the HTTP handler, inside an ``{"items"}``
    envelope with no commit. Large listings ride chunked frames.
    """
    from shared.sdk_knowledge import SDKKnowledgeError, list_knowledge_namespaces

    scope = frame.get("scope")
    if scope is not None and not isinstance(scope, str):
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_KNOWLEDGE_LIST_NAMESPACES} request: "
                "'scope' must be a string",
            )
        ]
    include_global = frame.get("include_global", True)
    if not isinstance(include_global, bool):
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_KNOWLEDGE_LIST_NAMESPACES} request: "
                "'include_global' must be a bool",
            )
        ]
    resolved_org_id, scope_error = await _resolve_frame_scope(
        principal, frame_id, {"scope": scope}, session_factory
    )
    if scope_error is not None:
        return [scope_error]

    async def _list(session: Any) -> dict[str, Any]:
        items = await list_knowledge_namespaces(
            session,
            org_id=resolved_org_id,
            include_global=include_global,
        )
        return {"items": items}

    result, error = await _run_short(
        session_factory,
        _list,
        op=OP_KNOWLEDGE_LIST_NAMESPACES,
        log_key="",
        status_errors=(SDKKnowledgeError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_knowledge_get(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``knowledge.get`` through the shared knowledge service.

    The HTTP route takes ``key``/``namespace``/``scope`` from the query
    string (no body DTO): a missing/blank key is 422 and a non-string
    namespace/scope is 422. Same exact-scope read, full-content
    reassembly, and document shape as the HTTP handler, with the
    repository miss mapped to a 404 error frame (the facade maps it to
    ``None``). Large reassembled content rides chunked frames.
    """
    from shared.sdk_knowledge import SDKKnowledgeError, get_knowledge_document

    key = frame.get("key")
    if not isinstance(key, str) or not key:
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_KNOWLEDGE_GET} request: 'key' is required",
            )
        ]
    namespace = frame.get("namespace", "default")
    if not isinstance(namespace, str):
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_KNOWLEDGE_GET} request: "
                "'namespace' must be a string",
            )
        ]
    scope = frame.get("scope")
    if scope is not None and not isinstance(scope, str):
        return [
            _error(
                frame_id,
                422,
                f"invalid {OP_KNOWLEDGE_GET} request: 'scope' must be a string",
            )
        ]
    resolved_org_id, scope_error = await _resolve_frame_scope(
        principal, frame_id, {"scope": scope}, session_factory
    )
    if scope_error is not None:
        return [scope_error]

    async def _get(session: Any) -> dict[str, Any]:
        return await get_knowledge_document(
            session,
            key=key,
            namespace=namespace,
            org_id=resolved_org_id,
        )

    result, error = await _run_short(
        session_factory,
        _get,
        op=OP_KNOWLEDGE_GET,
        log_key=key,
        status_errors=(SDKKnowledgeError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


def _required_str_field(
    frame: dict[str, Any], field: str, frame_id: str | None, op: str
) -> tuple[str | None, dict[str, Any] | None]:
    """One required non-empty string frame field, else a 422 error frame."""
    value = frame.get(field)
    if isinstance(value, str) and value:
        return value, None
    return None, _error(frame_id, 422, f"invalid {op} request: {field!r} is required")


def _module_source_scope(
    principal: LocalDispatchPrincipal,
) -> Any:
    """Authoritative source scope for one child's ``modules.*`` calls.

    Built only from the parent-derived principal (its Solution install id
    plus the install's global-repo flag) — child frames are never read
    for scope, so a child cannot forge another install's sources or widen
    a sealed Solution's import surface.
    """
    from shared.sdk_modules import ModuleSourceScope

    return ModuleSourceScope(
        solution_id=(
            str(principal.solution_id) if principal.solution_id is not None else None
        ),
        global_repo_access=principal.solution_global_repo_access,
    )


async def _dispatch_modules_resolve(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``modules.resolve`` through the shared module-source service.

    The child supplies only the logical import ``name``; the scope comes
    exclusively from the parent-derived principal. Calls the exact shared
    ``shared.sdk_modules.resolve_module_name`` the HTTP handler calls, so
    Solution-first ordering, sealed-Solution restrictions, cache keys/TTLs,
    and namespace handling are identical by construction. Large resolved
    sources ride bounded chunked frames via ``_ok_frames``.
    """
    from shared.sdk_modules import ModuleSourceError, resolve_module_name

    name, invalid = _required_str_field(
        frame, "name", frame_id, OP_MODULES_RESOLVE
    )
    if invalid is not None:
        return [invalid]
    assert name is not None
    scope = _module_source_scope(principal)

    async def _resolve(session: Any) -> dict[str, object]:
        return await resolve_module_name(name, scope=scope)

    result, error = await _run_short(
        session_factory,
        _resolve,
        op=OP_MODULES_RESOLVE,
        log_key=name,
        status_errors=(ModuleSourceError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


async def _dispatch_modules_fetch(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``modules.fetch`` through the shared module-source service.

    The child supplies only the candidate storage ``path``; the scope
    comes exclusively from the parent-derived principal, which also
    enforces the Solution-path access rules (a sealed install's bare
    workspace fetch 403s; an out-of-install solution path 403s; a miss
    404s so the child advances to the next candidate). Large sources ride
    bounded chunked frames via ``_ok_frames``.
    """
    from shared.sdk_modules import ModuleSourceError, fetch_module_source

    path, invalid = _required_str_field(
        frame, "path", frame_id, OP_MODULES_FETCH
    )
    if invalid is not None:
        return [invalid]
    assert path is not None
    scope = _module_source_scope(principal)

    async def _fetch(session: Any) -> dict:
        return await fetch_module_source(path, scope=scope)

    result, error = await _run_short(
        session_factory,
        _fetch,
        op=OP_MODULES_FETCH,
        log_key=path,
        status_errors=(ModuleSourceError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


def _context_user_for_principal(principal: LocalDispatchPrincipal) -> Any:
    """Token-equivalent ``UserPrincipal`` for the SDK context operation.

    Reuses the table token-equivalent shape so the context payload
    matches the authenticated ``GET /api/sdk/context`` call the child's
    engine/service token would make: workflows read as the system-user
    superuser with no org (engine sentinel user, null organization),
    services read as the system-user non-superuser confined to their
    service org (service actor user, own organization). Built only from
    the parent-derived ``LocalDispatchPrincipal`` — the child sends no
    identity fields, and the HTTP ``?org_id=`` override is never
    honored locally (the SDK never sends it).
    """
    return _table_user_for_principal(principal)


async def _dispatch_sdk_context(
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    frame_id: str | None,
    frame: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    """Serve ``sdk.context`` through the shared context service.

    The frame carries no fields — identity and org scope come only from
    the parent-derived principal (the token-equivalent engine/service
    user), never from child claims, exactly like the ``GET
    /api/sdk/context`` call without an ``?org_id=`` override. Calls the
    same ``shared.sdk_context.get_sdk_context`` the HTTP handler calls,
    so the ``user``/``organization``/``default_parameters``/
    ``track_executions`` payload is identical by construction. Served on
    the synchronous import channel (not the async SDK channel) so the
    synchronous ``BifrostClient.context`` property can call it without
    deadlocking a running child event loop. A local attempt never
    retries over HTTP.
    """
    from shared.sdk_context import SdkContextError, get_sdk_context

    user = _context_user_for_principal(principal)

    async def _context(session: Any) -> dict[str, Any]:
        return await get_sdk_context(session, user, org_id=None)

    result, error = await _run_short(
        session_factory,
        _context,
        op=OP_SDK_CONTEXT,
        log_key="context",
        status_errors=(SdkContextError,),
    )
    if error is not None:
        error["id"] = frame_id
        return [error]
    return _ok_frames(frame_id, result)


def _chunked_frames(
    frame_id: str | None, raw_result: bytes
) -> Iterable[dict[str, Any]]:
    """Yield a header plus ordered part frames for a large result.

    Lazy generator: the single ``raw_result`` buffer is shared and each
    part is base64-encoded only when the caller iterates to it, so
    ``serve_channel`` sends sequentially with pipe backpressure instead of
    materializing every part up front. Each frame is size-validated at
    send time by ``serve_channel``; there is no total result cap (the
    HTTP path has none).
    """
    import base64

    total = len(raw_result)
    parts = -(-total // _CHUNK_RAW_BYTES)
    yield {
        "v": TRANSPORT_VERSION,
        "id": frame_id,
        "ok": True,
        "chunked": True,
        "total": total,
        "parts": parts,
    }
    for i in range(parts):
        chunk = raw_result[i * _CHUNK_RAW_BYTES : (i + 1) * _CHUNK_RAW_BYTES]
        yield {
            "v": TRANSPORT_VERSION,
            "id": frame_id,
            "part": i,
            "data": base64.b64encode(chunk).decode("ascii"),
        }


class _RequestGone(Exception):
    """The child went away mid-request (EOF/pipe error during reassembly)."""


class _FrameOversized(Exception):
    """A received frame exceeds the wire-size bound."""


class _RequestMalformed(Exception):
    """A chunked request violated the wire contract."""


def _check_chunk_claim(total: Any, parts: Any) -> tuple[int, int]:
    """Validate a chunk header's ``total``/``parts`` claim (either direction).

    No total cap is enforced — the HTTP path has none. Raises
    :class:`_RequestMalformed` on any inconsistency before a single part
    is read.
    """
    if (
        not isinstance(total, int)
        or isinstance(total, bool)
        or total <= 0
        or not isinstance(parts, int)
        or isinstance(parts, bool)
        or parts < 1
        or parts != -(-total // _CHUNK_RAW_BYTES)
    ):
        raise _RequestMalformed("invalid chunk header")
    return total, parts


async def _reassemble_request(header: dict[str, Any], recv: Callable[[], Any]) -> dict[str, Any]:
    """Rebuild one chunked child request (e.g. large ``config.set`` value).

    Reads exactly the announced parts with bounded per-frame reads; the
    pipe itself provides sequential backpressure. There is no total cap.
    Any deviation — wrong order, wrong id, bad encoding, length mismatch,
    child gone mid-stream — raises instead of returning a partial request.
    """
    request_id = header.get("id")
    op = header.get("op")
    total, parts = _check_chunk_claim(header.get("total"), header.get("parts"))
    buf = bytearray()
    for i in range(parts):
        try:
            raw = await recv()
        except EOFError as e:
            raise _RequestGone("child went away mid-request") from e
        except _FrameOversized as e:
            raise _RequestMalformed("request part exceeded frame bound") from e
        except OSError as e:
            raise _RequestGone(f"child channel failed mid-request: {e}") from e
        try:
            part = decode_frame(raw)
        except Exception as e:
            raise _RequestMalformed(f"unparseable request part: {e}") from e
        if part.get("id") != request_id or part.get("part") != i:
            raise _RequestMalformed("request chunk stream desynchronized")
        data = part.get("data")
        if not isinstance(data, str):
            raise _RequestMalformed("invalid request chunk encoding")
        try:
            chunk = base64.b64decode(data.encode("ascii"), validate=True)
        except Exception as e:
            raise _RequestMalformed(f"invalid request chunk payload: {e}") from e
        buf.extend(chunk)
        if len(buf) > total:
            raise _RequestMalformed("request chunk stream exceeded declared total")
    if len(buf) != total:
        raise _RequestMalformed("request chunk stream length mismatch")
    try:
        request = json.loads(bytes(buf).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise _RequestMalformed(f"invalid chunked request: {e}") from e
    if (
        not isinstance(request, dict)
        or request.get("id") != request_id
        or request.get("op") != op
    ):
        raise _RequestMalformed("reassembled request id/op mismatch")
    return request


async def serve_channel(
    *,
    recv_conn: Any,
    send_conn: Any,
    session_factory: SessionFactory,
    principal: LocalDispatchPrincipal,
    executor: Any = None,
    allowed_ops: frozenset[str] = SDK_CHANNEL_ALLOWED_OPS,
) -> str:
    """Pump one child SDK channel until EOF, protocol violation, or cancel.

    Reads exactly one frame at a time (the child serializes requests, so at
    most one is ever outstanding; a chunked request header is followed by
    exactly its announced parts, read sequentially with bounded frames and
    pipe backpressure), dispatches it on a short session, and writes the
    response frame-by-frame as the dispatch iterable yields (large results
    encode one part per send, preserving pipe backpressure). Blocking pipe
    IO runs in ``executor`` (or ``asyncio.to_thread``) so the pool event
    loop stays responsive; no unbounded queues are used at any point, and
    there is no total size cap in either direction (the HTTP path has none).

    Returns a short reason string: ``"eof"`` (child exited/crashed),
    ``"oversized"`` (a frame exceeded the byte bound — the pipe is left
    unreadable by design, so the channel closes rather than risk unbounded
    allocation), ``"malformed"`` (unparseable or id-less frame — channel
    closed rather than risk desync), ``"oversize-out"`` (a frame the parent
    built exceeded the bound — internal bug guard, channel closed), or
    ``"child-gone"`` (response write failed). Cancellation propagates for
    pool shutdown.
    """
    loop = asyncio.get_running_loop()

    async def _recv() -> bytes:
        if executor is not None:
            raw = await loop.run_in_executor(
                executor, recv_conn.recv_bytes, MAX_FRAME_BYTES + 1
            )
        else:
            raw = await asyncio.to_thread(recv_conn.recv_bytes, MAX_FRAME_BYTES + 1)
        if len(raw) > MAX_FRAME_BYTES:
            raise _FrameOversized
        return raw

    async def _send(raw: bytes) -> None:
        if executor is not None:
            await loop.run_in_executor(executor, send_conn.send_bytes, raw)
        else:
            await asyncio.to_thread(send_conn.send_bytes, raw)

    async def _send_frame(frame: dict[str, Any]) -> str | None:
        """Validate one outgoing frame's size at send time, then send it."""
        try:
            raw = _encode_outgoing(frame)
        except _OutgoingTooLarge:
            logger.error(
                "local SDK outgoing frame exceeded byte bound; closing channel"
            )
            return "oversize-out"
        try:
            await _send(raw)
        except (EOFError, OSError):
            return "child-gone"
        return None

    while True:
        try:
            raw = await _recv()
        except EOFError:
            return "eof"
        except _FrameOversized:
            logger.warning("local SDK frame exceeded byte bound; closing channel")
            return "oversized"
        except OSError:
            # Connection has no portable exception subtype for an
            # over-bound frame. The descriptor cannot be safely reused, and
            # every OSError on this bounded receive has the same close path.
            return "oversized"
        try:
            frame = decode_frame(raw)
        except Exception:
            return "malformed"
        if frame.get("chunked"):
            # A large child request (config.set with a big JSON value):
            # reassemble before dispatch. Any violation closes the channel
            # rather than risk desync; a child gone mid-stream reads as EOF.
            if frame.get("v") != TRANSPORT_VERSION or not isinstance(
                frame.get("id"), str
            ):
                return "malformed"
            try:
                frame = await _reassemble_request(frame, _recv)
            except _RequestGone:
                return "eof"
            except _RequestMalformed as e:
                logger.warning("local SDK chunked request rejected: %s", e)
                return "malformed"
            except _FrameOversized:
                logger.warning("local SDK frame exceeded byte bound; closing channel")
                return "oversized"
            except OSError:
                return "oversized"
        if not isinstance(frame.get("id"), str):
            return "malformed"
        if frame.get("op") not in allowed_ops:
            reason = await _send_frame(
                _error(
                    frame["id"],
                    404,
                    f"local channel operation not allowed: {frame.get('op')!r}",
                )
            )
            if reason is not None:
                return reason
            continue
        for response in await dispatch_frames(session_factory, principal, frame):
            reason = await _send_frame(response)
            if reason is not None:
                return reason
