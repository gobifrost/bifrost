"""Pre-resolving claim references from table policies."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.models.contracts.claims import ClaimQuery, CustomClaim
from src.models.contracts.policies import TablePolicies


def _claim(name: str) -> CustomClaim:
    return CustomClaim(
        id=uuid4(),
        organization_id=uuid4(),
        name=name,
        type="list",
        query=ClaimQuery(table="memberships", select="campus_id"),
    )


@pytest.mark.asyncio
async def test_preresolve_resolves_each_referenced_claim_once(monkeypatch):
    from shared.claims import preresolve

    org_id = uuid4()
    claims = {
        "allowed_campus_ids": _claim("allowed_campus_ids"),
        "allowed_doc_type_ids": _claim("allowed_doc_type_ids"),
    }
    resolved: list[str] = []

    async def fake_load(db, loaded_org_id):
        assert loaded_org_id == org_id
        return claims

    async def fake_resolve(claim, all_claims, user, db, resolving):
        resolved.append(claim.name)

    monkeypatch.setattr(preresolve, "_load_org_claims", fake_load)
    monkeypatch.setattr(preresolve, "_resolve_claim", fake_resolve)

    policies = TablePolicies.model_validate({
        "policies": [
            {
                "name": "scoped_read",
                "actions": ["read"],
                "when": {
                    "and": [
                        {
                            "in": [
                                {"row": "campus_id"},
                                {"claims": "allowed_campus_ids"},
                            ]
                        },
                        {
                            "in": [
                                {"row": "doc_type_id"},
                                {"claims": "allowed_doc_type_ids"},
                            ]
                        },
                        {
                            "in": [
                                {"row": "campus_id"},
                                {"claims": "allowed_campus_ids"},
                            ]
                        },
                    ]
                },
            }
        ]
    })

    await preresolve.preresolve_for_policies(
        SimpleNamespace(),
        policies,
        db=None,  # type: ignore[arg-type]
        org_id=org_id,
    )

    assert set(resolved) == {"allowed_campus_ids", "allowed_doc_type_ids"}
    assert len(resolved) == 2


@pytest.mark.asyncio
async def test_preresolve_noops_when_no_claim_refs(monkeypatch):
    from shared.claims import preresolve

    async def fail_load(db, org_id):
        raise AssertionError("claims should not be loaded")

    monkeypatch.setattr(preresolve, "_load_org_claims", fail_load)
    policies = TablePolicies.model_validate({
        "policies": [
            {
                "name": "own_row",
                "actions": ["read"],
                "when": {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
            }
        ]
    })

    await preresolve.preresolve_for_policies(
        SimpleNamespace(),
        policies,
        db=None,  # type: ignore[arg-type]
        org_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_run_claim_query_returns_empty_when_source_table_denies_read(monkeypatch):
    """Regression: claims must NOT bypass the source table's read filter.

    If `compile_read_filter` returns None for the source table (no rule grants
    read to the caller), the claim resolves to [] without touching Document.
    """
    from shared.claims import preresolve
    from src.models.contracts.policies import TablePolicies

    source = SimpleNamespace(id=uuid4(), access=None)

    class _FakeResult:
        def scalar_one_or_none(self):
            return source

        def scalars(self):
            raise AssertionError(
                "Document scan must not happen when source table denies read"
            )

    class _FakeDB:
        async def execute(self, _stmt):
            return _FakeResult()

    # Source table has no read-granting policies → compile_read_filter is None.
    monkeypatch.setattr(
        preresolve, "_load_source_policies", lambda _s: TablePolicies()
    )

    claim = _claim("locked")
    rows = await preresolve._run_claim_query(
        claim,
        claims={},
        user=SimpleNamespace(claims={}),
        db=_FakeDB(),  # type: ignore[arg-type]
        resolving=set(),
    )
    assert rows == []
