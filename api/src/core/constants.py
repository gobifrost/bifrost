"""
Platform Constants

Well-known UUIDs and other constants used across the platform.
"""

from uuid import UUID

from shared.authorization_defaults_v1 import (
    BUILDER_ROLE_ID as BUILDER_ROLE_ID,
    ORGANIZATION_MEMBER_ROLE_ID as ORGANIZATION_MEMBER_ROLE_ID,
    PLATFORM_ADMIN_ROLE_ID as PLATFORM_ADMIN_ROLE_ID,
    PLATFORM_BUILDER_ROLE_ID as PLATFORM_BUILDER_ROLE_ID,
    PLATFORM_OPERATOR_ROLE_ID as PLATFORM_OPERATOR_ROLE_ID,
)

# System user for automated executions (schedules, webhooks, internal events)
# Created by migration: 20251210_141849_add_system_user_and_api_key_id.py
SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"  # String for SDK context
SYSTEM_USER_UUID = UUID(SYSTEM_USER_ID)  # UUID for ORM operations
SYSTEM_USER_EMAIL = "system@internal.gobifrost.com"

# Provider organization - the MSP/platform operator's home organization
# Created by migration: 20260107_022300_add_provider_org.py
# All platform admins belong to this org. Cannot be deleted.
PROVIDER_ORG_ID = UUID("00000000-0000-0000-0000-000000000002")

# Deterministic identities for Bifrost-managed authorization roles.
PLATFORM_ADMIN_ROLE_KEY = "platform_admin"
PLATFORM_OPERATOR_ROLE_KEY = "platform_operator"
BUILDER_ROLE_KEY = "builder"
PLATFORM_BUILDER_ROLE_KEY = "platform_builder"
ORGANIZATION_MEMBER_ROLE_KEY = "organization_member"
