# Approved code packet A1: recorded assertion semantics

Owner: primary. Executor: OpenCode `opencode-go/muse-spark-1.3-contributor`.
Status: authorized for implementation by primary under user's broader execution request. No user approval needed between internal packets. Stop after A1 and return for primary review; primary advances A2/B.

This document OVERRIDES conflicting proposals in workbench-contracts.md and recorded-evaluation-handoff.md. Those were audit proposals, not approved APIs. Do not implement their tables/jobs/routes yet.

## Scope / files

Worktree `/home/jack/GitHub/bifrost/.worktrees/durable-agent-platform-backend`. Existing dirty product code belongs to this task; preserve it. Create ONLY:
- `api/shared/agent_recorded_evaluation.py` — pure recorded assertion wrapper and aggregation business logic (repository requires new business logic in shared).
- `api/tests/unit/services/agent_evaluations/test_recorded_outcomes.py` — behavioral tests.
- `docs/superpowers/plans/2026-09-20-recorded-core-report.md` — completion evidence.
No loaders, schemas, routes, DTOs, migrations, jobs, CLI or mockup changes. No commits/push/install/secret access. Read testing skill. Run Docker tests via test.sh; stack is already UP. Shared quality gate allowed, but don't fix outside scope without returning the exact blocker.

## Interface (plain mappings; no new Pydantic model)

`evaluate_recorded_assertions(assertions, evidence, *, completeness, applicability)` returns list of outcomes. `applicability` is REQUIRED, one of `applicable`, `not_applicable`, `unknown`; upstream admission/classification must establish it, never infer from absence of expected calls. Unknown -> insufficient_evidence; known false -> not_applicable. Validate definitions even for inapplicable inputs. Do not mutate inputs.

`completeness` REQUIRED maps evidence dimensions to booleans: terminal_status, output, tool_calls, tool_order, tool_arguments, delegation, real_tool_executions, usage.iterations, usage.tokens, usage.cost_usd, usage.latency_ms (flat dotted keys okay). Missing/false is incomplete. True must also have the corresponding evidence key/type present; explicit empty complete tool list differs from missing. Explicit output=None is recorded null, not a missing field. Reuse existing `validate_assertions` and `evaluate_assertions` for exact semantics, do not duplicate evaluators. Preserve redaction and qualified evidence references; adapt output field names consistently with existing outcomes. Input references untrusted: no private data echo for incomplete/unknown applicability outcomes.

Returned `outcome`: passed, failed, not_applicable, insufficient_evidence, pending_judge, error. Include code/type/label where existing; reason detail. Never use passed=True for pending/unknown. Preserve original input; no fabricated simulator_state/no_real_tools=0.

Required evidence:
- terminal_status: status present + terminal_status completeness; do not treat queued/running/sleeping/waiting_* as a completed pass. Existing terminal enum values verified in source. Historical execution failure can be tested as recorded terminal status; evaluator execution error is a separate thing.
- output_schema/output_path: output present and complete. Missing key is insufficient; explicit null judged normally. Never default absent output to successful empty object.
- tool_called/tool_not_called/forbidden_tool/tool_count: complete, structurally valid tool_calls. Conservative requirement for full trace acceptable for first slice; no green absence from incomplete trace.
- tool_order: complete tool_calls + tool_order. Do not infer global causal order across parallel children from timestamps.
- tool_args: complete tool_calls + tool_arguments; redacted/missing args must be marked incomplete upstream.
- delegation_tree: complete delegation, valid children list.
- no_real_tools: complete real_tool_executions, present nonnegative integer (bool not integer). Do not manufacture zero.
- max_*: respective complete usage key, present finite nonnegative numeric, bool rejected.
- simulator_state: insufficient_evidence with clear unsupported recorded-state reason. Missing state is not proof the situation doesn't apply. Only known-false TEST applicability returns not_applicable.
- llm_judge: pending_judge with passed false / no pass claim. This slice never calls a judge. Do not use existing sync pending outcome's passed=True. Actual judge resolution comes in later job slice.

`aggregate_recorded_pair(outcomes)` returns outcome, complete boolean and counts preserving each category. Precedence: error > failed > pending_judge > insufficient_evidence > all-not-applicable > passed. Pending/error/insufficient make complete false even when another known failure makes overall outcome failed. Empty assertion set is insufficient, never vacuous pass. Mixed pass + explicit not_applicable may pass only if at least one passed and no failure/pending/missing/error; false applicability applies to whole test in wrapper.

`aggregate_recorded_evaluation(pairs)` keeps counts and all_inapplicable, complete, gate_passed (requires at least one applicable passing pair and no failure/error/pending/insufficient). Never collapse lost/incomplete evidence into a green total. Do not set PlatformJob status: a job can finish successfully and report failed tests. Shared job lifecycle remains separate from test verdicts. Pair dictionaries come from aggregate_recorded_pair; count pairs, not assertions.

## Tests and review gates

Write focused behavior tests proving: forbidden calls cannot pass incomplete trace, explicit empty complete trace passes, missing fields with true completeness fail closed, unknown versus false applicability, absent simulator state insufficient, pending judge not pass, failed+insufficient remains failed with complete false, all-N/A and empty cannot green-gate, budgets reject bool/NaN/inf/negative/missing, null output isn't absent, tool order needs separate order completeness, no real tool count missing is insufficient, inputs unchanged and redaction maintained. Include existing exact evaluator consumer regression by running existing assertion tests.

Run:
`./test.sh tests/unit/services/agent_evaluations/test_recorded_outcomes.py tests/unit/services/agent_evaluations/test_assertions.py -v`
`./test.sh quality api`

If a known broader quality failure appears, record precise reproduction, don't wave away or silently expand. Report commands/results and broader suites not run. No user notifications. Primary consumes completion, independently reviews/tests, then issues next packet.
