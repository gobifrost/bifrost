# R1 quality usage reporting core report

Status: source-ready checkpoint; tests are written but not run because the shared test stack is reserved by the B2 evidence worker.

Implemented scope:

- Added `api/shared/quality_usage_reporting.py`.
- Added focused DB-backed tests in `api/tests/unit/services/test_quality_usage_reporting.py`.
- No DTO, route, UI, CLI, migration, foundation, or generated-type changes.

Core behavior:

- Requires explicit `scope="platform"` or `scope="organization"`.
- Organization scope requires `organization_id`.
- Validates aware UTC `start_at` / `end_at` and rejects reversed ranges.
- Preserves existing source semantics:
  - `executions` means `AIUsage.execution_id is not null`;
  - `chat` means `AIUsage.conversation_id is not null`;
  - `agents` means `AIUsage.agent_run_id is not null`;
  - quality overhead and unresolved attempts appear only under `source="all"`.
- Aggregates in SQL independently for overall totals and each bounded dimension page.
- Does not load the full ledger or all operation groups into Python.
- Returns internal dictionaries only; API DTO mapping remains later work.

Dimensions:

- purpose
- provider/model
- profile/provider/model
- organization
- operation type/id/purpose

Totals:

- input/output/cache token sums;
- call count;
- duration subtotal plus missing-duration count;
- provider-observed cost;
- locally estimated cost;
- known cost;
- missing-cost call count;
- legacy call count.

Coverage:

- started attempt gaps;
- unobserved attempt gaps;
- missing-cost observed rows;
- legacy coverage unknown;
- synthetic rows with missing/invalid operation lineage.

Classification:

- explicit `AIUsage.usage_purpose` wins;
- execution/chat/runtime agent rows classify from their source context;
- `sequence=0` agent rows classify as `agent_summary`;
- `evaluation_synthetic` rows classify as `test_designer` only when `evaluation_designer` is a JSON boolean true;
- other `evaluation_synthetic` rows classify as `simulation`;
- malformed synthetic operation lineage stays unassigned and increments coverage rather than being inferred;
- production runs with arbitrary synthetic-looking correlation are still runtime agent usage.

Known pending verification:

- `./test.sh tests/unit/services/test_quality_usage_reporting.py -v`
- `./test.sh quality api`

