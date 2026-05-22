"""Resolver unit tests — no live DB; uses fakes for the source-table query.

The resolver's contract:
  - Lazy: only resolves a claim when actually referenced.
  - Per-request cache: hangs `claims` dict off the principal.
  - Empty result returns [] (list) or None (scalar) — fail-closed on access.
"""
from types import SimpleNamespace
from uuid import uuid4

from src.models.contracts.claims import ClaimQuery, CustomClaim


def make_user(org_id, **extra):
    return SimpleNamespace(
        user_id=uuid4(),
        organization_id=org_id,
        email="alice@example.com",
        is_platform_admin=False,
        role_ids=[],
        role_names=[],
        claims={},  # request-scoped cache lives here
        **extra,
    )


def make_claim(name, *, type_="list", table="user_campus_access", select="campus_id", where=None):
    return CustomClaim(
        id=uuid4(),
        organization_id=uuid4(),
        name=name,
        type=type_,
        query=ClaimQuery(table=table, select=select, where=where),
    )


def test_list_claim_resolves_to_list(monkeypatch):
    from shared.claims import resolver

    monkeypatch.setattr(
        resolver,
        "_run_claim_query",
        lambda claim, user, db: [{"campus_id": "c1"}, {"campus_id": "c2"}],
    )

    user = make_user(org_id=uuid4())
    claim = make_claim("allowed_campus_ids")
    out = resolver.resolve_claim(claim, user, db=None)
    assert out == ["c1", "c2"]
    assert user.claims["allowed_campus_ids"] == ["c1", "c2"]


def test_resolver_caches_per_request(monkeypatch):
    from shared.claims import resolver

    call_count = {"n": 0}

    def fake_run(claim, user, db):
        call_count["n"] += 1
        return [{"campus_id": "c1"}]

    monkeypatch.setattr(resolver, "_run_claim_query", fake_run)

    user = make_user(org_id=uuid4())
    claim = make_claim("allowed_campus_ids")
    resolver.resolve_claim(claim, user, db=None)
    resolver.resolve_claim(claim, user, db=None)
    resolver.resolve_claim(claim, user, db=None)
    assert call_count["n"] == 1


def test_scalar_claim_returns_first_value_or_none(monkeypatch):
    from shared.claims import resolver

    monkeypatch.setattr(
        resolver, "_run_claim_query", lambda c, u, db: [{"campus_id": "c1"}]
    )

    user = make_user(org_id=uuid4())
    claim = make_claim("primary_campus_id", type_="scalar")
    assert resolver.resolve_claim(claim, user, db=None) == "c1"


def test_empty_list_result_resolves_to_empty_list(monkeypatch):
    from shared.claims import resolver

    monkeypatch.setattr(resolver, "_run_claim_query", lambda c, u, db: [])
    user = make_user(org_id=uuid4())
    claim = make_claim("allowed_campus_ids")
    assert resolver.resolve_claim(claim, user, db=None) == []


def test_empty_scalar_result_resolves_to_none(monkeypatch):
    from shared.claims import resolver

    monkeypatch.setattr(resolver, "_run_claim_query", lambda c, u, db: [])
    user = make_user(org_id=uuid4())
    claim = make_claim("primary_campus_id", type_="scalar")
    assert resolver.resolve_claim(claim, user, db=None) is None


def test_resolver_dispatches_to_runner(monkeypatch):
    """resolver._run_claim_query must dispatch through runner.run."""
    from shared.claims import resolver, runner

    captured = {}

    def fake_run(claim, user, db):
        captured["claim"] = claim.name
        return []

    monkeypatch.setattr(runner, "run", fake_run)
    user = make_user(org_id=uuid4())
    claim = make_claim("allowed_campus_ids")
    resolver.resolve_claim(claim, user, db=None)
    assert captured["claim"] == "allowed_campus_ids"


def test_missing_user_claims_attribute_is_initialized(monkeypatch):
    from shared.claims import resolver

    monkeypatch.setattr(resolver, "_run_claim_query", lambda c, u, db: [])
    user = SimpleNamespace(user_id=uuid4(), organization_id=uuid4())  # no .claims yet
    claim = make_claim("allowed_campus_ids")
    out = resolver.resolve_claim(claim, user, db=None)
    assert out == []
    assert user.claims == {"allowed_campus_ids": []}
