"""Opt-in scale benchmark for claim-backed hierarchical policy scopes.

This module is intentionally outside the default unit/e2e suites. Run it with:

    ./test.sh tests/performance/test_policy_path_containment.py -s

It prints machine-specific measurements without imposing brittle latency
thresholds on CI. Assertions cover fixture size and authorization correctness.
"""

from __future__ import annotations

import json
import statistics
import time
import tracemalloc
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from shared.path_matching import path_within_any
from shared.policies.compile import compile_to_sql
from src.models.contracts.policies import Expr
from src.models.orm.custom_claims import CustomClaim
from src.models.orm.file_metadata import FileMetadata, FilePolicy
from src.models.orm.organizations import Organization
from src.models.orm.tables import Document, Table
from src.services.file_policy_service import FilePolicyService

SITE_COUNT = 150
CATEGORY_COUNT = 8
DOCUMENTS_PER_CATEGORY = 50
DOCUMENT_COUNT = SITE_COUNT * CATEGORY_COUNT * DOCUMENTS_PER_CATEGORY
FILE_COUNT = DOCUMENT_COUNT * 2
LEGACY_PREFIX_COUNT = SITE_COUNT * CATEGORY_COUNT * 2
PROFILE_SITE_COUNTS = {"sparse": 1, "medium": 10, "broad": 40}


def _chunks(items: list[dict], size: int = 5_000):
    for offset in range(0, len(items), size):
        yield items[offset : offset + size]


def _path(site: int, category: int, mode: str, document: int) -> str:
    suffix = "pdf" if mode == "pdf" else "dwg"
    return (
        f"site-{site:03d}:category-{category:02d}/{mode}/"
        f"document-{document:03d}/drawing.{suffix}"
    )


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * fraction), len(ordered) - 1)]


async def _explain(db: AsyncSession, stmt, *, dialect: postgresql.dialect) -> dict:
    sql = str(stmt.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))
    result = await db.execute(text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}"))
    return result.scalar_one()[0]


def _plan_summary(result: dict) -> dict:
    plan = result["Plan"]
    return {
        "node_type": plan["Node Type"],
        "actual_rows": plan["Actual Rows"],
        "planning_ms": round(result["Planning Time"], 3),
        "execution_ms": round(result["Execution Time"], 3),
        "shared_hit_blocks": plan.get("Shared Hit Blocks", 0),
    }


@pytest.mark.asyncio
@pytest.mark.slow
@pytest.mark.timeout(180)
async def test_large_claim_backed_policy_benchmark(
    db_session: AsyncSession,
) -> None:
    started = time.perf_counter()
    org_id = uuid4()
    user_ids = {profile: str(uuid4()) for profile in PROFILE_SITE_COUNTS}
    user_id = user_ids["broad"]
    documents_table_id = uuid4()
    grants_table_id = uuid4()

    db_session.add(
        Organization(
            id=org_id, name=f"Policy benchmark {uuid4().hex[:8]}", created_by="test"
        )
    )
    db_session.add_all(
        [
            Table(
                id=documents_table_id,
                name=f"benchmark_documents_{uuid4().hex[:8]}",
                organization_id=org_id,
            ),
            Table(
                id=grants_table_id,
                name=f"benchmark_grants_{uuid4().hex[:8]}",
                organization_id=org_id,
                access={
                    "policies": [
                        {
                            "name": "own_rows",
                            "actions": ["read"],
                            "when": {"eq": [{"row": "user_id"}, {"user": "user_id"}]},
                        }
                    ]
                },
            ),
        ]
    )
    await db_session.flush()

    document_rows: list[dict] = []
    metadata_rows: list[dict] = []
    for site in range(SITE_COUNT):
        for category in range(CATEGORY_COUNT):
            access_key = f"site-{site:03d}:category-{category:02d}"
            for document in range(DOCUMENTS_PER_CATEGORY):
                document_rows.append(
                    {
                        "id": f"document-{site:03d}-{category:02d}-{document:03d}",
                        "table_id": documents_table_id,
                        "data": {
                            "access_key": access_key,
                            "resource_path": _path(site, category, "pdf", document),
                        },
                    }
                )
                for mode in ("pdf", "download"):
                    path = _path(site, category, mode, document)
                    metadata_rows.append(
                        {
                            "id": uuid4(),
                            "organization_id": org_id,
                            "location": "documents",
                            "path": path,
                            "s3_key": f"benchmark/{path}",
                            "content_type": (
                                "application/pdf"
                                if mode == "pdf"
                                else "application/octet-stream"
                            ),
                            "size_bytes": 1,
                            "sha256": "0" * 64,
                            "created_by": uuid4(),
                        }
                    )

    for chunk in _chunks(document_rows):
        await db_session.execute(insert(Document), chunk)
    for chunk in _chunks(metadata_rows):
        await db_session.execute(insert(FileMetadata), chunk)

    # Sparse, medium, and broad principals exercise growing permission sets.
    # Half the sites include a separate download capability.
    grant_rows: list[dict] = []
    access_keys_by_profile: dict[str, list[str]] = {}
    paths_by_profile: dict[str, list[str]] = {}
    for profile, site_count in PROFILE_SITE_COUNTS.items():
        access_keys: list[str] = []
        paths: list[str] = []
        for site in range(site_count):
            for category in range(CATEGORY_COUNT):
                access_key = f"site-{site:03d}:category-{category:02d}"
                access_keys.append(access_key)
                prefixes = [f"{access_key}/pdf"]
                if site % 2 == 0:
                    prefixes.append(f"{access_key}/download")
                for mode, prefix in enumerate(prefixes):
                    paths.append(prefix)
                    grant_rows.append(
                        {
                            "id": f"grant-{profile}-{site:03d}-{category:02d}-{mode}",
                            "table_id": grants_table_id,
                            "data": {
                                "user_id": user_ids[profile],
                                "path_prefix": prefix,
                            },
                        }
                    )
        access_keys_by_profile[profile] = access_keys
        paths_by_profile[profile] = paths
    await db_session.execute(insert(Document), grant_rows)
    allowed_access_keys = access_keys_by_profile["broad"]
    allowed_paths = paths_by_profile["broad"]

    grant_table_name = (
        await db_session.execute(select(Table.name).where(Table.id == grants_table_id))
    ).scalar_one()
    db_session.add(
        CustomClaim(
            organization_id=org_id,
            name="allowed_resource_paths",
            type="list",
            query={
                "table": grant_table_name,
                "where": {"eq": [{"row": "user_id"}, {"user": "user_id"}]},
                "select": "path_prefix",
            },
        )
    )

    root_policies = {
        "policies": [
            {
                "name": "claim_read",
                "actions": ["read"],
                "when": {
                    "path_within_any": [
                        {"file": "path"},
                        {"claims": "allowed_resource_paths"},
                    ]
                },
            }
        ]
    }
    db_session.add(
        FilePolicy(
            organization_id=org_id,
            location="documents",
            path="",
            policies=root_policies,
            created_by=uuid4(),
        )
    )

    legacy_rows = [
        {
            "id": uuid4(),
            "organization_id": org_id,
            "location": "legacy-documents",
            "path": "",
            "policies": {"policies": []},
            "created_by": uuid4(),
        }
    ]
    for site in range(SITE_COUNT):
        for category in range(CATEGORY_COUNT):
            for mode in ("pdf", "download"):
                prefix = f"site-{site:03d}:category-{category:02d}/{mode}"
                legacy_rows.append(
                    {
                        "id": uuid4(),
                        "organization_id": org_id,
                        "location": "legacy-documents",
                        "path": prefix,
                        "policies": {
                            "policies": [
                                {
                                    "name": "claim_read",
                                    "actions": ["read"],
                                    "when": {
                                        "in": [
                                            prefix,
                                            {"claims": "allowed_resource_paths"},
                                        ]
                                    },
                                }
                            ]
                        },
                        "created_by": uuid4(),
                    }
                )
    for chunk in _chunks(legacy_rows):
        await db_session.execute(insert(FilePolicy), chunk)
    await db_session.flush()
    seed_seconds = time.perf_counter() - started

    assert len(document_rows) == DOCUMENT_COUNT
    assert len(metadata_rows) == FILE_COUNT
    assert len(legacy_rows) - 1 == LEGACY_PREFIX_COUNT
    assert len(allowed_access_keys) == 320
    assert len(allowed_paths) == 480
    assert len(grant_rows) == 616

    user = SimpleNamespace(
        user_id=user_id,
        email="benchmark@example.com",
        organization_id=str(org_id),
        is_platform_admin=False,
        is_provider_org=False,
        is_external=True,
        role_ids=[],
        role_names=[],
        claims={},
    )
    service = FilePolicyService(db_session)

    async def authorize(location: str, path: str) -> bool:
        return await service.is_allowed(
            "read",
            organization_id=org_id,
            location=location,
            path=path,
            user=user,
        )

    assert await authorize("documents", _path(0, 0, "pdf", 0)) is True
    assert await authorize("documents", _path(0, 0, "download", 0)) is True
    assert await authorize("documents", _path(1, 0, "download", 0)) is False
    assert await authorize("documents", _path(40, 0, "pdf", 0)) is False
    assert (
        await authorize(
            "documents", "site-000-annex:category-00/pdf/document-000/drawing.pdf"
        )
        is False
    )

    assert await authorize("legacy-documents", _path(0, 0, "pdf", 0)) is True
    assert await authorize("legacy-documents", _path(1, 0, "download", 0)) is False

    new_latencies: list[float] = []
    legacy_latencies: list[float] = []
    for sample in range(20):
        path = _path(sample % 40, sample % CATEGORY_COUNT, "pdf", sample)
        before = time.perf_counter()
        assert await authorize("documents", path) is True
        new_latencies.append((time.perf_counter() - before) * 1_000)

        before = time.perf_counter()
        assert await authorize("legacy-documents", path) is True
        legacy_latencies.append((time.perf_counter() - before) * 1_000)

    exact_expr = Expr.model_validate(
        {
            "in": [
                {"row": "access_key"},
                {"claims": "allowed_document_access_keys"},
            ]
        }
    )
    claims_user = SimpleNamespace(
        claims={
            "allowed_document_access_keys": allowed_access_keys,
            "allowed_resource_paths": allowed_paths,
        }
    )
    exact_stmt = select(Document.id).where(
        Document.table_id == documents_table_id,
        compile_to_sql(exact_expr, claims_user),
    )
    dialect = postgresql.dialect()
    exact_plan = await _explain(db_session, exact_stmt, dialect=dialect)

    iterations = 20_000
    evaluator_results: dict[str, dict] = {}
    for profile, paths in paths_by_profile.items():
        tracemalloc.start()
        before = time.perf_counter()
        for i in range(iterations):
            path_within_any(_path(i % 150, i % 8, "pdf", i % 50), paths)
        evaluator_seconds = time.perf_counter() - before
        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        evaluator_results[profile] = {
            "resolved_path_scopes": len(paths),
            "seconds": round(evaluator_seconds, 3),
            "evaluations_per_second": round(iterations / evaluator_seconds),
            "peak_bytes": peak_bytes,
        }

    report = {
        "fixture": {
            "sites": SITE_COUNT,
            "categories": CATEGORY_COUNT,
            "documents": DOCUMENT_COUNT,
            "file_metadata": FILE_COUNT,
            "compound_grants": len(allowed_access_keys),
            "resolved_path_scopes": len(allowed_paths),
            "permission_rows": len(grant_rows),
            "legacy_policy_prefixes": LEGACY_PREFIX_COUNT,
            "new_policy_prefixes": 1,
        },
        "seed_seconds": round(seed_seconds, 3),
        "file_authorization_ms": {
            "root_claim_policy_median": round(statistics.median(new_latencies), 3),
            "root_claim_policy_p95": round(_percentile(new_latencies, 0.95), 3),
            "legacy_prefix_policy_median": round(
                statistics.median(legacy_latencies), 3
            ),
            "legacy_prefix_policy_p95": round(_percentile(legacy_latencies, 0.95), 3),
        },
        "in_memory_evaluator": {
            "iterations_per_profile": iterations,
            "profiles": evaluator_results,
        },
        "table_sql": {
            "composite_in": _plan_summary(exact_plan),
        },
    }
    print("POLICY_PATH_BENCHMARK=" + json.dumps(report, default=str))
