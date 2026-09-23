"""Import boundaries for the public model packages."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any


API_ROOT = Path(__file__).resolve().parents[2]


def _run_import_probe(source: str) -> dict[str, Any]:
    script = textwrap.dedent(source)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_enums_import_does_not_load_database_or_contracts() -> None:
    result = _run_import_probe(
        """
        import json
        import sys

        from src.models.enums import ExecutionStatus

        print(json.dumps({
            "value": ExecutionStatus.SUCCESS.value,
            "loaded": sorted(
                name for name in sys.modules
                if name == "sqlalchemy"
                or name.startswith("sqlalchemy.")
                or name == "src.models.contracts"
                or name.startswith("src.models.contracts.")
                or name == "src.models.orm"
                or name.startswith("src.models.orm.")
            ),
        }))
        """
    )

    assert result["value"] == "Success"
    assert result["loaded"] == []


def test_root_model_exports_are_lazy_but_still_resolve_public_symbols() -> None:
    result = _run_import_probe(
        """
        import json
        import sys

        import src.models as models

        after_import = sorted(
            name for name in sys.modules
            if name == "src.models.contracts"
            or name.startswith("src.models.contracts.")
            or name == "src.models.orm"
            or name.startswith("src.models.orm.")
        )

        user_name = models.User.__name__
        after_orm = {
            "orm_loaded": "src.models.orm" in sys.modules,
            "contracts_loaded": any(
                name == "src.models.contracts" or name.startswith("src.models.contracts.")
                for name in sys.modules
            ),
        }

        org_create_name = models.OrganizationCreate.__name__
        loaded_contracts = sorted(
            name for name in sys.modules
            if name == "src.models.contracts" or name.startswith("src.models.contracts.")
        )

        print(json.dumps({
            "after_import": after_import,
            "user_name": user_name,
            "after_orm": after_orm,
            "org_create_name": org_create_name,
            "loaded_contracts": loaded_contracts,
        }))
        """
    )

    assert result["after_import"] == []
    assert result["user_name"] == "User"
    assert result["after_orm"] == {"orm_loaded": True, "contracts_loaded": False}
    assert result["org_create_name"] == "OrganizationCreate"
    assert result["loaded_contracts"] == [
        "src.models.contracts",
        "src.models.contracts.organizations",
    ]


def test_contract_package_exports_are_lazy_and_resolve_public_symbols() -> None:
    result = _run_import_probe(
        """
        import json
        import sys

        import src.models.contracts as contracts

        after_import = sorted(
            name for name in sys.modules
            if name.startswith("src.models.contracts.")
        )

        org_create_name = contracts.OrganizationCreate.__name__
        loaded_after_org = sorted(
            name for name in sys.modules
            if name.startswith("src.models.contracts.")
        )

        user_create_name = contracts.UserCreate.__name__
        loaded_after_user = sorted(
            name for name in sys.modules
            if name.startswith("src.models.contracts.")
        )

        print(json.dumps({
            "after_import": after_import,
            "org_create_name": org_create_name,
            "loaded_after_org": loaded_after_org,
            "user_create_name": user_create_name,
            "loaded_after_user": loaded_after_user,
        }))
        """
    )

    assert result["after_import"] == []
    assert result["org_create_name"] == "OrganizationCreate"
    assert result["loaded_after_org"] == [
        "src.models.contracts.organizations",
    ]
    assert result["user_create_name"] == "UserCreate"
    assert result["loaded_after_user"] == [
        "src.models.contracts.organizations",
        "src.models.contracts.users",
    ]


def test_contract_all_resolves_every_public_export_and_alias() -> None:
    result = _run_import_probe(
        """
        import json

        import src.models.contracts as contracts
        from src.models.contracts import sdk

        failed = []
        for name in contracts.__all__:
            try:
                getattr(contracts, name)
            except Exception as exc:
                failed.append([name, type(exc).__name__, str(exc)])

        print(json.dumps({
            "count": len(contracts.__all__),
            "failed": failed,
            "sdk_alias": contracts.SDKOAuthCredentials is sdk.OAuthCredentials,
        }))
        """
    )

    assert result["count"] == 491
    assert result["failed"] == []
    assert result["sdk_alias"] is True


def test_root_model_all_preserves_public_exports_and_resolves_every_symbol() -> None:
    result = _run_import_probe(
        """
        import json

        import src.models as models
        import src.models.contracts as contracts

        failed = []
        for name in models.__all__:
            try:
                getattr(models, name)
            except Exception as exc:
                failed.append([name, type(exc).__name__, str(exc)])

        print(json.dumps({
            "count": len(models.__all__),
            "failed": failed,
            "contract_tail_matches": models.__all__[-len(contracts.__all__):] == contracts.__all__,
        }))
        """
    )

    assert result["count"] == 546
    assert result["failed"] == []
    assert result["contract_tail_matches"] is True


def test_lazy_model_packages_raise_meaningful_attribute_errors() -> None:
    result = _run_import_probe(
        """
        import json

        import src.models as models
        import src.models.contracts as contracts

        messages = []
        for module in [models, contracts]:
            try:
                getattr(module, "DefinitelyMissingModel")
            except AttributeError as exc:
                messages.append(str(exc))

        print(json.dumps({"messages": messages}))
        """
    )

    assert result["messages"] == [
        "module 'src.models' has no attribute 'DefinitelyMissingModel'",
        "module 'src.models.contracts' has no attribute 'DefinitelyMissingModel'",
    ]


def test_database_import_registers_full_orm_metadata() -> None:
    result = _run_import_probe(
        """
        import json

        from sqlalchemy.orm import configure_mappers

        from src.core.database import get_session_factory
        from src.models.orm import Base

        get_session_factory()
        configure_mappers()

        print(json.dumps({
            "table_count": len(Base.metadata.tables),
            "mapper_count": len(Base.registry.mappers),
            "has_core_tables": all(
                table in Base.metadata.tables
                for table in ["users", "organizations", "forms", "workflows", "agents"]
            ),
        }))
        """
    )

    assert result["table_count"] > 40
    assert result["mapper_count"] > 40
    assert result["has_core_tables"] is True
