"""Contract-version gate tripwire.

Two jobs:

1. **Sync check** — the CLI-baked ``CONTRACT_VERSION`` must equal the
   server-side ``CONTRACT_VERSION``. They are two hand-maintained integers
   (one shipped in the CLI wheel, one in the server) and the runtime gate
   compares them across the wire, so they must agree at the source.

2. **Tripwire** — a fingerprint over the contract surface the CLI actually
   depends on (the request/response DTOs it sends + the routes it calls). Any
   change to that surface flips the fingerprint, failing this test until the
   author makes an explicit decision: bump ``CONTRACT_VERSION`` (breaking) or
   just refresh the fingerprint (cosmetic/additive). This is what makes a
   missed bump a red test instead of a production incident.

The fingerprint is computed live, in-process, and only ever compared to a
constant committed in THIS file — never shipped or compared across machines —
so cross-machine schema-serialization differences are irrelevant.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

# Standalone bifrost SDK package import (mirrors test_contracts_parity.py).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from bifrost.contract_version import CONTRACT_VERSION as CLI_CONTRACT_VERSION  # noqa: E402
from shared.contract_version import CONTRACT_VERSION as SERVER_CONTRACT_VERSION  # noqa: E402

# DTOs the CLI sends/receives. Server-canonical classes (the wire truth).
from src.models.contracts.agents import AgentCreate, AgentUpdate  # noqa: E402
from src.models.contracts.applications import (  # noqa: E402
    ApplicationCreate,
    ApplicationUpdate,
)
from src.models.contracts.claims import CustomClaimCreate, CustomClaimUpdate  # noqa: E402
from src.models.contracts.config import ConfigCreate, ConfigUpdate  # noqa: E402
from src.models.contracts.events import (  # noqa: E402
    EventSourceCreate,
    EventSourceUpdate,
    EventSubscriptionCreate,
    EventSubscriptionUpdate,
)
from src.models.contracts.executions import (  # noqa: E402
    WorkflowExecutionRequest,
    WorkflowExecutionResponse,
)
from src.models.contracts.forms import FormCreate, FormUpdate  # noqa: E402
from src.models.contracts.integrations import (  # noqa: E402
    IntegrationCreate,
    IntegrationMappingCreate,
    IntegrationMappingUpdate,
    IntegrationUpdate,
)
from src.models.contracts.organizations import (  # noqa: E402
    OrganizationCreate,
    OrganizationUpdate,
)
from src.models.contracts.tables import TableCreate, TableUpdate  # noqa: E402
from src.models.contracts.users import RoleCreate, RoleUpdate  # noqa: E402
from src.models.contracts.workflows import WorkflowUpdateRequest  # noqa: E402

import inspect  # noqa: E402

from pydantic import BaseModel  # noqa: E402

from src.models.contracts import cli as _cli_contracts  # noqa: E402

# ---------------------------------------------------------------------------
# The contract surface the CLI depends on.
# ---------------------------------------------------------------------------

#: The explicit CRUD + execute DTOs the `bifrost <entity>` command surface uses.
_COMMAND_DTOS: list[type] = [
    OrganizationCreate,
    OrganizationUpdate,
    RoleCreate,
    RoleUpdate,
    WorkflowUpdateRequest,
    WorkflowExecutionRequest,
    WorkflowExecutionResponse,
    FormCreate,
    FormUpdate,
    AgentCreate,
    AgentUpdate,
    ApplicationCreate,
    ApplicationUpdate,
    IntegrationCreate,
    IntegrationUpdate,
    IntegrationMappingCreate,
    IntegrationMappingUpdate,
    ConfigCreate,
    ConfigUpdate,
    CustomClaimCreate,
    CustomClaimUpdate,
    TableCreate,
    TableUpdate,
    EventSourceCreate,
    EventSourceUpdate,
    EventSubscriptionCreate,
    EventSubscriptionUpdate,
]

#: Every request/response DTO the in-workflow SDK sends/parses against
#: ``/api/sdk/*`` lives in ``src.models.contracts.cli``. We pull them in
#: programmatically so a NEW SDK DTO is automatically fingerprinted — no manual
#: list to forget to update. A retype on any of these silently breaks running
#: workflows on a stale CLI, so they belong in the contract.
_SDK_DTOS: list[type] = [
    obj
    for _name, obj in inspect.getmembers(_cli_contracts, inspect.isclass)
    if issubclass(obj, BaseModel)
    and obj is not BaseModel
    and obj.__module__ == _cli_contracts.__name__
]

#: Request/response DTOs the CLI/SDK sends or parses. Type-aware via JSON Schema.
#: This is the real wire contract: the command DTOs (`bifrost <entity>` /
#: `workflows execute`) plus EVERY SDK DTO (pulled in programmatically, so new
#: ones are auto-covered). A field removed/renamed/retyped here is exactly what
#: silently corrupts a stale CLI, and it is caught completely and automatically.
CONTRACT_FINGERPRINT_MODELS: list[type] = _COMMAND_DTOS + _SDK_DTOS

#: We deliberately do NOT fingerprint the full route list. Route strings are a
#: weak, noisy proxy: hand-listing ~100 `/api/*` paths is perpetually incomplete
#: (every omission is a false-negative), and a route rename produces a clean 404
#: — not the silent corruption a response-shape change causes, which the DTOs
#: above already catch. We keep only `/api/version` itself, since the gate's own
#: handshake depends on that literal path.
CLI_ROUTES: tuple[str, ...] = ("/api/version",)

#: Committed fingerprint of the contract surface above. If a code change flips
#: the live fingerprint, this test fails — update this value, and bump
#: CONTRACT_VERSION (both sides) IF the change is breaking. See module docstring.
EXPECTED_CONTRACT_FINGERPRINT = (
    "e6460f50e29885a406c962823bf9490db0272221c76c7b5f5e12adf03ce0f9f4"
)


def _fingerprint(models: list[type], routes: tuple[str, ...]) -> str:
    """Deterministic sha256 over model JSON schemas + the route list."""
    h = hashlib.sha256()
    for model in sorted(models, key=lambda m: m.__name__):
        schema = model.model_json_schema()
        h.update(model.__name__.encode())
        h.update(json.dumps(schema, sort_keys=True).encode())
    h.update(json.dumps(sorted(routes)).encode())
    return h.hexdigest()


def test_cli_and_server_contract_version_agree() -> None:
    """The two hand-maintained integers must match at the source."""
    assert CLI_CONTRACT_VERSION == SERVER_CONTRACT_VERSION, (
        f"CONTRACT_VERSION drift: CLI={CLI_CONTRACT_VERSION} "
        f"(api/bifrost/contract_version.py) vs "
        f"server={SERVER_CONTRACT_VERSION} (api/shared/contract_version.py). "
        f"When you bump one, bump the other."
    )


def test_contract_fingerprint_tripwire() -> None:
    """A change to the CLI-consumed contract surface forces a decision."""
    current = _fingerprint(CONTRACT_FINGERPRINT_MODELS, CLI_ROUTES)
    assert current == EXPECTED_CONTRACT_FINGERPRINT, (
        "A CLI-consumed contract (DTO schema) changed.\n"
        f"  current fingerprint: {current}\n"
        "  - BREAKING change (field removed/renamed/retyped, "
        "response shape the CLI parses changed): bump CONTRACT_VERSION in BOTH "
        "api/shared/contract_version.py AND api/bifrost/contract_version.py, "
        "then update EXPECTED_CONTRACT_FINGERPRINT below.\n"
        "  - COSMETIC/ADDITIVE (description tweak, new optional field the CLI "
        "ignores): just update EXPECTED_CONTRACT_FINGERPRINT, leave "
        "CONTRACT_VERSION.\n"
        "See test_contract_version.py module docstring."
    )
