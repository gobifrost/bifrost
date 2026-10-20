"""Explicit Global grants work for users whose home is Global."""

from uuid import uuid4

import pytest

from shared.builtin_roles import DECRYPTION_ROLE_ID, USER_ROLE_ID
from src.services.authorization.context import AuthorizationContext, Boundary, BoundaryKind, RoleGrant
from src.services.authorization.evaluator import GLOBAL, HOME, cross_org, decide
from src.services.authorization.explain import permission_entry
from tests.helpers.authorization import platform_admin_grant


@pytest.mark.parametrize("target, allowed", [(HOME, True), (GLOBAL, True), (cross_org(uuid4()), False)])
def test_global_home_secrets_reader_grant_covers_only_global(target, allowed):
    ctx = AuthorizationContext(
        user_id=uuid4(), home_organization_id=None, base_role_id=USER_ROLE_ID,
        is_external=False, base_permissions=frozenset(),
        role_grants=(platform_admin_grant(), RoleGrant(
            DECRYPTION_ROLE_ID, frozenset({"secrets.read"}), (Boundary(BoundaryKind.PLATFORM),),
        )),
    )

    assert decide(ctx, permission_entry("secrets.read"), target).allowed is allowed
